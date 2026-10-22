"""Quick read: a fast path for read-only tasks, tried before the full agent.

The full agent spends one model call per step and reads the page one screen at a time. Most "look this up",
"list every…" and "for each X open its page" tasks need neither: Python can open the pages, read all their text
(scrolling virtualised lists itself) and open many detail pages at once, so the model only has to *read*.

    route(task)            -> "read" or "agent": a cheap decision, no model call
    await quick_read(...)  -> QuickResult: done (answer + items), or handed to the agent with notes

How a quick read goes, one model call per round:
1. Python opens the start page in a background tab of its own and takes its text, links and GET forms.
2. The model answers in JSON: done (answer + items copied from the text), open (pages to read next: detail
   pages, the next result page, a search URL built from a form), or agent (it needs clicks, typing, a login).
3. Pages to open are read in parallel (4 at a time; 1 at a time with a pause on sites that ban bots), and only
   the new pages go into the next round, so prompts stay small.

It is read-only by construction: it never clicks or types, so the approval gate is never needed. It only opens
URLs from the task, from links on pages it read, or GET searches built from a page's own forms. Saved items are
checked against the text of the pages it read, like save_item.
"""
import asyncio
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field

from pydantic import BaseModel

import core

# Tasks that need the agent: anything that clicks to change something, signs in, or is about the human's tabs.
ACTS = re.compile(
    r"\b(sign ?up|sign ?in|log ?in|register|buy|order|purchase|pay|checkout|add to (?:cart|basket)|book|reserve|"
    r"send|message|e-?mail|reply|post|publish|tweet|comment|like|follow|connect|invite|apply|subscribe|"
    r"fill|submit|upload|download|delete|remove|cancel|install|enable|disable|create|make an? account|set ?up|"
    r"change|update|edit|rename|share|accept|approve|vote|rate|review it|click|type|enter|select|choose|"
    r"turn (?:on|off)|save (?:it|this|them) to|generate)\b", re.I)
HUMAN_TABS = re.compile(r"\b(this|my|the open|current|open) (page|tab|tabs|window)\b|\balready open\b", re.I)
SLOW_SITES = ("linkedin.com",)  # site rules: a person's pace, one page at a time (AGENTS.md)

MAX_ROUNDS = 6
MAX_OPEN = 12          # pages per round
PARALLEL = 4
PAGE_CHARS = 30000     # text per page sent to the model
ROUND_CHARS = 90000    # text per round


def route(task):
    """'read' when a quick read can try it: it names a page to start from and asks for nothing but reading."""
    # The prompt optimizer's safety line ("confirm before sending…") is a guardrail, not something to do.
    task = "\n".join(l for l in (task or "").splitlines() if not re.match(r"\W*limits\b", l, re.I))
    if not task.strip() or HUMAN_TABS.search(task) or ACTS.search(task):
        return "agent"
    return "read" if core.start_url(task) else "agent"


# ---------------------------------------------------------------- reading pages (no clicks, no typing)

READ_JS = r"""(async () => {
  const seen = new Set(), lines = [];
  const take = () => { for (const l of (document.body ? document.body.innerText : '').split('\n')) {
    const t = l.trim(); if (t && !seen.has(t)) { seen.add(t); lines.push(t); } } };
  const links = new Map();
  const grab = () => { for (const a of document.querySelectorAll('a[href]')) {
    const h = a.href; if (!h.startsWith('http') || links.has(h)) continue;
    links.set(h, (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 80)); } };
  take(); grab();
  // Virtualised lists only hold the rows near the screen: scroll through and collect as we go.
  let last = -1;
  for (let k = 0; k < 60 && scrollY !== last && lines.join('\n').length < 300000; k++) {
    last = scrollY; scrollBy(0, innerHeight * 0.9);
    dispatchEvent(new Event('scroll'));  // background tabs get no rendering frames, so no scroll events
    await new Promise(r => setTimeout(r, 120)); take(); grab();
  }
  scrollTo(0, 0);
  const forms = [...document.querySelectorAll('form')].filter(f => (f.method || 'get').toLowerCase() === 'get')
    .map(f => ({action: f.action, fields: [...f.elements].filter(e => e.name)
      .map(e => e.name + (e.type === 'hidden' ? '=' + e.value : ''))})).slice(0, 5);
  return JSON.stringify({url: location.href, title: document.title, text: lines.join('\n'),
                         links: [...links].slice(0, 300), forms});
})()"""


async def read_page(browser, url, settle=0.5):
    """Open url in a background tab of our own, read everything on it, close the tab."""
    root = browser._cdp_client_root
    target = (await root.send.Target.createTarget(params={"url": url, "background": True}))["targetId"]
    try:
        session = (await root.send.Target.attachToTarget(params={"targetId": target, "flatten": True}))["sessionId"]

        async def js(expr, wait=False):
            r = await root.send.Runtime.evaluate(params={"expression": expr, "returnByValue": True, "awaitPromise": wait},
                                                 session_id=session)
            return r.get("result", {}).get("value")
        for _ in range(60):  # up to ~15 s to load
            await asyncio.sleep(0.25)
            if await js("document.readyState") == "complete":
                break
        await asyncio.sleep(settle)  # client-rendered pages paint after load
        page = json.loads(await js(READ_JS, wait=True) or "{}")
    finally:
        try:
            await root.send.Target.closeTarget(params={"targetId": target})
        except Exception:  # noqa: BLE001  already gone
            pass
    page.setdefault("url", url)
    return page


def render(page):
    """The page as the model sees it: text, then links and forms (so it can open pages and build searches)."""
    text = page.get("text", "")
    if len(text) > PAGE_CHARS:
        text = text[:PAGE_CHARS] + f"\n[… {len(text) - PAGE_CHARS} more characters cut]"
    links = "\n".join(f"- {t or '(no text)'} -> {h}" for h, t in page.get("links", []))
    forms = "\n".join(f"- GET {f['action']} fields: {', '.join(f['fields'])}" for f in page.get("forms", []))
    return (f"=== PAGE {page.get('url')}  (title: {page.get('title', '')})\n{text}\n--- links\n{links or '(none)'}"
            + (f"\n--- forms\n{forms}" if forms else ""))


# ---------------------------------------------------------------- the model's part

class Field(BaseModel):
    field: str
    value: str


class Item(BaseModel):
    name: str
    details: list[Field]


class Reply(BaseModel):
    status: str                  # "done" | "open" | "agent"
    items: list[Item] = []       # results found on the pages given in THIS message, copied exactly
    open: list[str] = []         # status "open": URLs to read next
    answer: str = ""             # status "done": the final answer for the human
    reason: str = ""             # why "agent", or what is still missing


SYSTEM = """You read web pages for a human and answer their task. You cannot click or type: Python opens pages
for you. Every message gives you the task and the full text, links and forms of the pages opened since your
last reply. Reply in JSON only.

- items: save results NOW, from the pages in THIS message: you will not see these pages again (only a list of
  the items you saved). Every result the task asks for that appears on them (one per person/product/row, name
  plus each requested field, using the task's field names), values copied exactly as written on the page.
  Earlier items are kept; send only new ones. Never invent values; leave out a field the page doesn't show.
  Save matching results while you still open more pages; don't wait until you have seen everything.
- status "open" + open: URLs to read next, taken from the links shown, or a GET search URL you build from a
  form shown (its action plus its field names, keywords URL-encoded). Open every page you need at once: all
  detail pages, all result pages you can see links for. At most 12 per reply.
- status "done" + answer: when the task is complete. The answer lists every result with its fields, then what
  you could not do. Write it for the human.
- status "agent" + reason: when the task needs a click, typing in a box that isn't a GET form, a login, a
  cookie wall, a page that doesn't load, or anything you can't do by reading. Keep any items you found.
- If the task names a filter, option or search syntax the site doesn't have (or a search returns nothing),
  try once without it, then skip that part and say so in the answer. Don't guess hidden URL parameters.
- Search engines treat quotes and brackets differently: prefer plain keywords in a built search URL."""


@dataclass
class QuickResult:
    status: str                       # "done" | "agent" (hand to the full agent) | "error"
    answer: str = ""
    items: list = field(default_factory=list)
    notes: str = ""                   # for the agent when handed over: what was read and found
    rounds: int = 0
    pages: int = 0
    seconds: float = 0.0
    model_seconds: float = 0.0


def _plain(s):
    return re.sub(r"[^a-z0-9$.%@:/]+", " ", str(s).lower()).strip()


async def quick_read(task, model_id, browser, on_progress=None, max_rounds=MAX_ROUNDS, fallback_id=None):
    """Try the task by reading only. Never raises for a site problem: returns status 'agent' instead."""
    t0, model_time = time.time(), 0.0
    llm = core.make_llm(model_id)
    start = core.start_url(task)
    seen, texts, items, cache, resent = set(), [], {}, {}, set()
    from browser_use.llm.messages import AssistantMessage, SystemMessage, UserMessage
    history = [SystemMessage(content=SYSTEM)]
    to_open, rounds, reply = [start], 0, None

    def progress(msg):
        if on_progress:
            on_progress(msg)

    def result(status, answer="", reason=""):
        out = list(items.values())
        notes = ""
        if status != "done":
            notes = (f"A quick read (no clicks) already opened {len(seen)} page(s): " + ", ".join(list(seen)[:12])
                     + f". It saved {len(out)} item(s) checked against those pages"
                     + (f": {', '.join(i['fields']['name'] for i in out[:40])}" if out else "")
                     + f". It stopped because: {reason or 'it ran out of rounds'}. Continue from there; don't redo saved items.")
        return QuickResult(status, answer, out, notes, rounds, len(seen), round(time.time() - t0, 1), round(model_time, 1))

    while rounds < max_rounds:
        rounds += 1
        wanted = [u for u in dict.fromkeys(to_open) if u and u.startswith("http")]
        urls = [u for u in wanted if u not in seen][:MAX_OPEN]
        again = [cache[u] for u in wanted if u in cache and u not in resent][:MAX_OPEN]  # it asked to see a page again
        resent.update(p["url"] for p in again)
        if not urls and not again:
            return result("agent", reason=f"nothing new to open ({reply.reason if reply else ''})")
        slow = any(s in urllib.parse.urlsplit(u).netloc for u in urls for s in SLOW_SITES)
        if slow:
            urls = urls[:5]
        sem = asyncio.Semaphore(1 if slow else PARALLEL)

        async def one(u):
            async with sem:
                try:
                    page = await read_page(browser, u)
                except Exception as e:  # noqa: BLE001  a page that fails is reported, not fatal
                    page = {"url": u, "title": "", "text": f"(could not open: {type(e).__name__})", "links": []}
                if slow:
                    await asyncio.sleep(2.0)
                return page
        progress(f"quick read: opening {len(urls)} page(s)")
        pages = await asyncio.gather(*(one(u) for u in urls))
        seen.update(urls)
        for u, p in zip(urls, pages):
            seen.add(p.get("url", ""))
            cache[u] = {**p, "url": u}
            texts.append(_plain(p.get("text", "")))
        pages = list(pages) + again
        body, budget = [], ROUND_CHARS
        for p in pages:
            r = render(p)[:budget]
            budget -= len(r)
            body.append(r)
            if budget <= 0:
                body.append("[more pages were opened but cut for length; ask for them again if needed]")
                break
        saved = f"Items saved so far ({len(items)}): " + "; ".join(items)[:3000] if items else "No items saved yet."
        history.append(UserMessage(content=f"TASK: {task}\n\n{saved}\nRound {rounds} of {max_rounds}."
                                     + (" LAST ROUND: you can't open more pages. Save every result on these pages and reply "
                                        "done (say in the answer what is missing), or agent if clicks are truly needed."
                                        if rounds == max_rounds else "") + "\n\n" + "\n\n".join(body)))
        t1 = time.time()
        try:
            reply = (await llm.ainvoke(history, output_format=Reply)).completion
        except Exception as e:  # noqa: BLE001  rate limit, bad JSON: the fallback model gets one try
            try:
                if not fallback_id or fallback_id == model_id:
                    raise
                reply = (await core.make_llm(fallback_id).ainvoke(history, output_format=Reply)).completion
            except Exception:  # noqa: BLE001
                model_time += time.time() - t1
                return result("agent", reason=f"the model failed ({type(e).__name__})")
        model_time += time.time() - t1
        # Keep the conversation short: earlier pages are replaced by what the model took from them.
        history[-1] = UserMessage(content=f"TASK: {task}\n(round {rounds}: read {', '.join(urls)})")
        history.append(AssistantMessage(content=reply.model_dump_json()))
        page_text = " ".join(texts)
        for it in reply.items:
            fields = {"name": it.name, **{f.field: f.value for f in it.details if f.value}}
            missing = [k for k, v in fields.items() if _plain(v) and _plain(v) not in page_text]
            if "name" in missing:
                continue  # a result whose name isn't on any page read is not kept
            items[it.name.strip().lower()] = {"fields": fields, "unverified": missing}
        progress(f"quick read: round {rounds}, {len(items)} item(s), {reply.status}")
        if reply.status == "done":
            return result("done", reply.answer)
        if reply.status != "open":
            return result("agent", reason=reply.reason)
        to_open = reply.open
    return result("agent", reason="it ran out of rounds")
