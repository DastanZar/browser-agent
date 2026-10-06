"""Shared pieces of the browser agent: models, Chrome connection, human-in-the-loop tools, policy.

Used by agent.py (terminal) and dashboard.py (web UI). The API key is read from BAI_API_KEY or
~/.config/bai/key and is never written anywhere else.
"""
import asyncio
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from browser_use import ActionResult, Agent, Browser, BrowserSession, ChatOpenAI, Tools
import turbo

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "models.json").read_text())
MODELS = {m["id"]: m for m in CONFIG["models"]}

POLICY = """
YOU ARE WORKING IN THE HUMAN'S OWN BROWSER (their real tabs and logins).
- Do your work in a new tab unless the task is about a page that is already open.
- The human's other tabs are private. Do not read, search, switch to, close, reload or navigate
  them unless the task explicitly refers to them. Never inspect their storage, cookies or source.
- Never touch the agent dashboard tab (http://127.0.0.1:8770 or localhost:8770).

HOW TO WORK
- Work autonomously on HOW to do the task: navigation, finding settings, filling obvious fields,
  retrying when a site errors. Pick sensible defaults for unimportant details and list them at the end.
- If a site blocks you (rate limit, error page), retry another way before giving up.

WHEN TO INVOLVE THE HUMAN (only these):
- ask_human: a choice that is the human's to make (which plan or option, how much to spend, names,
  recipients, personal details) or a fact only they know, when the task doesn't say and a wrong
  guess would matter. Ask as soon as you reach that choice; do not hunt for hints elsewhere.
  Put all the questions you have at that point into one message, with the options you can see.
- hand_over: a login, password, 2-step code, passkey, CAPTCHA or "verify it's you" screen. Never
  try to solve or bypass these yourself. After the human finishes, re-check the page and continue.
- confirm: call it IMMEDIATELY BEFORE any click that spends money, sends or posts something, deletes
  or overwrites data, changes security, sharing or billing settings, accepts terms, or submits a
  final form. Describe exactly what will happen. If the human declines, do not do it.
  This is always a separate step: answers to ask_human (e.g. picking a plan) are NOT approval, so do
  not bundle "shall I proceed?" into ask_human.

LOGINS
- If a site shows you logged out, use hand_over so the human can log in. When you or they log in,
  tick "remember me" / "keep me signed in" if offered, so the session lasts.

FINISH with: what you did, what you changed (if anything), defaults you chose, what you could not do.
"""


# ---------------------------------------------------------------- models

def api_key():
    key = os.environ.get(CONFIG["key_env"])
    if not key:
        path = Path(os.path.expanduser(CONFIG["key_file"]))
        key = path.read_text().strip() if path.exists() else ""
    return key


def save_api_key(key):
    path = Path(os.path.expanduser(CONFIG["key_file"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.strip())
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except OSError:  # Windows: the file lives in the user's own profile
        pass


def make_llm(model_id):
    if model_id not in MODELS:
        raise ValueError(f"unknown model {model_id!r}; choose from {list(MODELS)}")
    key = api_key()
    if not key:
        raise RuntimeError(f"no API key: set {CONFIG['key_env']} or save it in the dashboard")
    json_mode = MODELS[model_id]["json_mode"]
    # Reasoning models spend part of the budget thinking; 4096 (the default) truncated replies in tests.
    return ChatOpenAI(model=model_id, base_url=os.environ.get("LLM_BASE_URL", CONFIG["base_url"]), api_key=key, temperature=0.2,
                      max_completion_tokens=12000, max_retries=3,
                      dont_force_structured_output=not json_mode, add_schema_to_system_prompt=not json_mode)


# ---------------------------------------------------------------- Chrome connection

def default_chrome_dir():
    """Where Chrome keeps your normal profile; it writes DevToolsActivePort there when
    remote debugging is switched on at chrome://inspect/#remote-debugging (Chrome 144+)."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Google" / "Chrome" / "User Data"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path(os.environ.get("CHROME_CONFIG_HOME") or Path.home() / ".config") / "google-chrome"


AGENT_PORT = int(os.environ.get("AGENT_PORT", "9222"))
AGENT_PROFILE = Path(os.environ.get("AGENT_PROFILE") or Path.home() / ".agent-chrome")


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def find_chrome():
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    system = platform.system()
    if system == "Windows":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        cands = [Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe" for r in roots if r]
    elif system == "Darwin":
        cands = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        cands = [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for c in cands:
        if c and Path(c).exists():
            return str(c)
    raise RuntimeError("Chrome not found; set CHROME_PATH to chrome's executable")


_SIGNIN = None  # the plain Chrome window opened for signing in (no debugging port)


def _spawn(args):
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # keeps running after the dashboard exits
    return subprocess.Popen(args, **kwargs)


def _base_args():
    AGENT_PROFILE.mkdir(parents=True, exist_ok=True)
    return [find_chrome(), f"--user-data-dir={AGENT_PROFILE}", "--no-first-run", "--no-default-browser-check",
            *os.environ.get("CHROME_ARGS", "").split()]


def signin_open():
    """True while the sign-in window is still open. It must be closed by the user (its X button):
    that is a normal shutdown, which saves the new login. Killing it can lose the last ~30 s of
    cookies (tested), so we never force it closed."""
    global _SIGNIN
    if _SIGNIN is not None and _SIGNIN.poll() is not None:
        _SIGNIN = None
    return _SIGNIN is not None


def close_agent_chrome():
    """Ask the agent Chrome to quit cleanly over CDP (saves cookies); no-op if it isn't running."""
    if not port_open(AGENT_PORT):
        return
    try:
        import websockets.sync.client as ws_client
        info = json.load(urllib.request.urlopen(f"http://127.0.0.1:{AGENT_PORT}/json/version", timeout=5))
        with ws_client.connect(info["webSocketDebuggerUrl"], max_size=None) as conn:
            conn.send(json.dumps({"id": 1, "method": "Browser.close"}))
    except Exception:
        pass
    for _ in range(80):
        if not port_open(AGENT_PORT):
            return
        time.sleep(0.25)


def open_signin_window(url=None):
    """Open the agent's profile in a PLAIN Chrome window (no debugging port) for you to log in.
    Google and some other sites refuse sign-in in a browser that is under automation; this window
    isn't, and the logins you make here are saved in the same profile the agent uses later."""
    global _SIGNIN
    close_agent_chrome()
    if _SIGNIN is not None and _SIGNIN.poll() is None:
        _spawn([*_base_args(), url or "about:blank"])  # hands the URL to the open window as a new tab
        return
    _SIGNIN = _spawn([*_base_args(), url or "about:blank"])


def open_agent_chrome(url=None):
    """Start the agent's own Chrome (persistent profile in ~/.agent-chrome) with its debugging port
    if it isn't running, optionally opening `url`. Logins made in this profile persist."""
    if not port_open(AGENT_PORT):
        if signin_open():  # one Chrome per profile, and it must close normally to keep the login
            raise RuntimeError("Close the sign-in Chrome window (its X button) so it saves your logins, then try again.")
        _spawn([*_base_args(), f"--remote-debugging-port={AGENT_PORT}", "--remote-debugging-address=127.0.0.1",
                url or "about:blank"])
        for _ in range(60):
            if port_open(AGENT_PORT):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("the agent Chrome didn't start; is another Chrome window using that profile? Close it and retry.")
    elif url:
        req = urllib.request.Request(f"http://127.0.0.1:{AGENT_PORT}/json/new?{urllib.parse.quote(url, safe=':/?=&')}",
                                     method="PUT")
        urllib.request.urlopen(req, timeout=5).read()
    return f"http://127.0.0.1:{AGENT_PORT}"


def resolve_cdp(mode=None):
    """Return (cdp_url, description).

    mode "agent" - the agent's own Chrome window with its persistent profile (~/.agent-chrome);
                   started automatically if needed. Log in to sites there once; it stays logged in.
    mode "mine"  - your everyday Chrome and its open tabs (Chrome 144+, remote debugging on)
    mode "auto"  - "mine" if it's reachable, else "agent"
    Any other value is used as a CDP URL as-is.
    """
    mode = mode or os.environ.get("CDP", "auto")
    if mode not in ("auto", "mine", "agent"):
        return mode, f"custom endpoint {mode}"
    if mode in ("auto", "mine"):
        port_file = Path(os.environ.get("CHROME_USER_DATA_DIR") or default_chrome_dir()) / "DevToolsActivePort"
        try:
            lines = [l.strip() for l in port_file.read_text().splitlines() if l.strip()]
            port, path = int(lines[0]), lines[1]
            if not port_open(port):  # stale file left by a Chrome that has since closed
                raise OSError("not listening")
            return f"ws://127.0.0.1:{port}{path}", "your Chrome (existing tabs)"
        except (OSError, ValueError, IndexError):
            if mode == "mine":
                raise RuntimeError(
                    f"Can't reach your Chrome's debugging port ({port_file}). In Chrome 144+, open "
                    "chrome://inspect/#remote-debugging and switch on remote debugging, then retry.")
    return open_agent_chrome(), "agent Chrome window (its own logins)"


def make_browser(cdp_url):
    domains = [d.strip() for d in os.environ.get("ALLOWED_DOMAINS", "").split(",") if d.strip()]
    # keep_alive: never close your Chrome; the same session is reused across tasks, so Chrome's
    # "Allow remote debugging?" prompt appears once per dashboard session, not once per task.
    return Browser(cdp_url=cdp_url, allowed_domains=domains or None, keep_alive=True)


async def visible_tab_hint(browser):
    """Describe the tab the human is looking at, so 'this page' in a task means something."""
    try:
        for target in browser.session_manager.get_all_page_targets():
            if "127.0.0.1:8770" in target.url or "localhost:8770" in target.url:
                continue
            session = await browser.get_or_create_cdp_session(target.target_id, focus=False)
            res = await session.cdp_client.send.Runtime.evaluate(
                params={"expression": "document.visibilityState", "returnByValue": True},
                session_id=session.session_id)
            if res.get("result", {}).get("value") == "visible":
                return (f'(Context: you start in a new blank tab. The human\'s visible tab is "{target.title}" '
                        f'({target.url}, tab_id {target.target_id[-4:]}). Switch to it only if the task says '
                        '"this page", "this tab" or similar; otherwise leave it alone.)')
    except Exception:
        pass
    return ""


async def page_text(browser):
    """The visible text of the agent's current tab (for checking what a model claims it read)."""
    try:
        cdp = await browser.get_or_create_cdp_session()
        res = await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": "document.body ? document.body.innerText.slice(0, 300000) : ''", "returnByValue": True},
            session_id=cdp.session_id)
        return res.get("result", {}).get("value") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------- approval gate (enforced in code)

# Clicks on elements whose label matches this need an approval, even if the model forgot to ask.
# Deliberately leaves out everyday admin verbs (save, create, enable, next, continue) so routine
# work isn't interrupted; it targets money, messages, deletion, publishing, legal and security.
RISKY = re.compile(
    r"\b(pay|buy|purchase|order|checkout|check ?out|subscribe|upgrade|donate|tip|bid|"
    r"send|post|publish|share|tweet|reply|comment|invite|connect|follow|like|endorse|repost|"
    r"delete|remove|erase|destroy|terminate|shut ?down|revoke|reset|wipe|"
    r"transfer|withdraw|refund|cancel (?:my |the )?(?:account|subscription|plan|order)|close (?:my |the )?account|"
    r"confirm|submit|agree|accept|sign ?up|register|create (?:my |an |your )?account|book|reserve|apply)\b",
    re.I)
YES = ("y", "yes", "approve", "approved", "ok", "go", "go ahead")


class GateBlocked(Exception):
    pass


def _element_label(state, index, for_click=False):
    try:
        node = state.dom_state.selector_map.get(int(index))
    except Exception:
        node = None
    if node is None:
        return ""
    attrs = node.attributes or {}
    tag = (node.tag_name or "").lower()
    if for_click and (tag in ("label", "option", "select") or attrs.get("role") in ("checkbox", "radio", "option")
                      or (tag == "input" and attrs.get("type", "").lower() in ("checkbox", "radio"))):
        return ""  # ticking a box is reversible; the final submit is what gets gated
    parts = [node.get_all_children_text(), attrs.get("aria-label", ""), attrs.get("value", ""),
             attrs.get("title", ""), attrs.get("alt", "")]
    return " ".join(p.strip() for p in parts if p and p.strip())[:160]


class Gate:
    """Runs between the model's decision and the browser action (Browser Use step callback)."""

    def __init__(self, human, read_only=False):
        self.human, self.step, self.approved_at, self.read_only = human, 0, -99, read_only
        self.approved_labels = set()  # approvals the gate itself got cover only that exact element

    def note_approval(self):
        self.approved_at = self.step

    def _risky(self, name, params, state):
        if name == "click" and "index" in params:
            label = _element_label(state, params["index"], for_click=True)
            if label and RISKY.search(label):
                return f'click "{label}"'
        if name == "send_keys" and "enter" in str(params.get("keys", "")).lower():
            labels = [_element_label(state, i) for i in list(state.dom_state.selector_map)[:400]]
            hits = [l for l in labels if l and RISKY.search(l) and len(l) < 60]
            if hits:
                return f'press Enter on a page with a "{hits[0]}" button (may submit it)'
        if name == "evaluate" and re.search(r"\.(click|submit|requestSubmit)\s*\(|dispatchEvent", str(params.get("code", ""))):
            return "run JavaScript that clicks or submits something"
        return ""

    @staticmethod
    def _secret_misuse(name, params, state):
        """A saved password may only be typed into a password field (and a username into a plain
        input), so a page can't trick the agent into posting it somewhere visible."""
        if name != "input":
            return ""
        text = str(params.get("text", ""))
        names = re.findall(r"<secret>(.*?)</secret>", text)
        if re.fullmatch(r"[a-z0-9_]+_(?:username|password|bu_2fa_code)", text):
            names.append(text)
        if not names:
            return ""
        node = state.dom_state.selector_map.get(int(params.get("index", -1))) if state else None
        tag = (getattr(node, "tag_name", "") or "").lower()
        kind = ((getattr(node, "attributes", None) or {}).get("type") or "text").lower()
        for secret in names:
            if secret.endswith("_password") and not (tag == "input" and kind == "password"):
                return "a saved password can only be typed into a password field"
            if tag != "input" or kind not in ("text", "email", "tel", "number", "password", ""):
                return "saved login details can only be typed into a login form field"
        return ""

    async def check(self, state, output, n):
        self.step = n
        if not output or state is None:
            return
        for action in output.action or []:
            for name, params in action.model_dump(exclude_none=True).items():
                misuse = self._secret_misuse(name, params or {}, state)
                if misuse:
                    raise GateBlocked(f"REFUSED: {misuse}. Do not try this again.")
                if os.environ.get("AUTO_CONFIRM") == "1":
                    continue
                label = self._risky(name, params or {}, state)
                if label and self.read_only:
                    raise GateBlocked(f"READ-ONLY TASK: you may not {label}. Do not interact with the page; "
                                      "keep reading and scrolling, or finish.")
                # The model's own confirm() covers the next couple of steps; the gate's covers that element only.
                if not label or n - self.approved_at <= 2 or label in self.approved_labels:
                    continue
                answer = (await self.human("confirm", f"The agent is about to {label} on {state.url}. Allow it?")).strip()
                if answer.lower() in YES or answer.lower().startswith("yes"):
                    self.approved_labels.add(label)
                    continue
                raise GateBlocked(f"BLOCKED BY THE HUMAN: they did not approve this: {label}. Their reply: "
                                  f"{answer or 'no'}. Do not retry it. Use ask_human to ask what to do instead, or finish.")


# ---------------------------------------------------------------- human-in-the-loop tools

def _bare(u):
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower()).rstrip("/")


def build_tools(human, gate=None, capture=None, collector=None):
    """human: async (kind, text) -> str, with kind in {"ask", "handover", "confirm"}."""
    tools = Tools()
    opened = {}  # what read_link was asked for -> (real URL, title), to fix links the model reports later

    @tools.action("Ask the human a question only they can answer. Returns their answer.")
    async def ask_human(question: str) -> ActionResult:
        answer = (await human("ask", question)).strip()
        return ActionResult(extracted_content=f"Human answered: {answer or '(no answer)'}",
                            long_term_memory=f"Asked '{question}', human said '{answer}'")

    @tools.action("Hand the browser to the human for a login, 2-step code, passkey, CAPTCHA or "
                  "identity check. Waits until they are done.")
    async def hand_over(reason: str) -> ActionResult:
        note = (await human("handover", reason)).strip()
        return ActionResult(extracted_content="Human finished in the browser. " + (f"Note: {note}. " if note else "")
                            + "Re-check the current page before continuing.",
                            long_term_memory=f"Human handled: {reason}")

    @tools.action("Get the human's approval BEFORE an irreversible or consequential action "
                  "(pay, send, post, delete, publish, change security/billing/sharing, accept terms, final submit).")
    async def confirm(action_description: str) -> ActionResult:
        if os.environ.get("AUTO_CONFIRM") == "1":
            return ActionResult(extracted_content="Approved (auto-confirm is on).")
        answer = (await human("confirm", action_description)).strip()
        if answer.lower() in YES or answer.lower().startswith("yes"):
            if gate:
                gate.note_approval()
            return ActionResult(extracted_content="Approved. Go ahead.", long_term_memory=f"Approved: {action_description}")
        return ActionResult(extracted_content=f"NOT approved. Do not do it. Human said: {answer or 'no'}",
                            long_term_memory=f"Declined: {action_description}")

    @tools.action("Scroll a feed or long page down by about one screen, wait for new posts to load, and report "
                  "whether more content appeared and whether you're at the bottom. Use this instead of scroll "
                  "on infinite feeds (LinkedIn, X, Reddit…); repeat while it says more loaded.")
    async def scroll_feed(browser_session: BrowserSession, direction: str = "down") -> ActionResult:
        cdp = await browser_session.get_or_create_cdp_session()

        async def js(expr):
            res = await cdp.cdp_client.send.Runtime.evaluate(
                params={"expression": expr, "returnByValue": True, "awaitPromise": True}, session_id=cdp.session_id)
            return res.get("result", {}).get("value")

        before = await js("document.scrollingElement.scrollHeight")
        sign = -1 if str(direction).lower().startswith("u") else 1
        await js(f"window.scrollBy(0, {sign} * Math.round(window.innerHeight * 0.9))")
        for _ in range(8):  # give lazy feeds up to ~2.4 s to load the next batch
            await asyncio.sleep(0.3)
            if (await js("document.scrollingElement.scrollHeight")) > before:
                break
        info = await js("({h: document.scrollingElement.scrollHeight, y: Math.round(window.scrollY),"
                        " v: window.innerHeight})") or {}
        grew = info.get("h", 0) > before
        bottom = info.get("y", 0) + info.get("v", 0) >= info.get("h", 0) - 4
        msg = (f"Scrolled. {'New content loaded.' if grew else 'No new content loaded.'} "
               f"{'At the bottom of the page.' if bottom and not grew else 'More page below.'}")
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    @tools.action("List the links on the current page (text and address), optionally only those whose text or "
                  "address contains `contains`. Use it to get the real URL of posts or profiles without clicking.")
    async def page_links(browser_session: BrowserSession, contains: str = "") -> ActionResult:
        cdp = await browser_session.get_or_create_cdp_session()
        expr = """(() => { const f = %s.toLowerCase(); const out = [], seen = new Set();
          for (const a of document.querySelectorAll('a[href]')) {
            const t = (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
            const h = a.href; if (!/^https?:/.test(h) || seen.has(h)) continue;
            if (f && !(t.toLowerCase().includes(f) || h.toLowerCase().includes(f))) continue;
            seen.add(h); out.push(t + ' -> ' + h); if (out.length >= 80) break; }
          return out.join('\\n'); })()""" % json.dumps(contains or "")
        res = await cdp.cdp_client.send.Runtime.evaluate(params={"expression": expr, "returnByValue": True},
                                                         session_id=cdp.session_id)
        links = res.get("result", {}).get("value") or "(no matching links)"
        return ActionResult(extracted_content=links, include_extracted_content_only_once=True)

    @tools.action("Open a link in a background tab, read its title and text, then close that tab. Pass the link's "
                  "URL or its visible text; it is resolved on the current page. The current "
                  "page (e.g. a timeline) stays exactly where it is, so you can keep scrolling afterwards. Use it "
                  "to look at what a post links to instead of clicking the link and going back.")
    async def read_link(link: str, browser_session: BrowserSession) -> ActionResult:
        # Resolve against the current page first: a link's visible text ("fastqueue.dev/…", "t.co/…")
        # often isn't its real address, and relative links need the page's base URL.
        here = await browser_session.get_or_create_cdp_session()
        probe = json.dumps((link or "").strip())
        res = await here.cdp_client.send.Runtime.evaluate(params={"expression": r"""(() => { const q = %s, ql = q.toLowerCase();
            const as = [...document.querySelectorAll('a[href]')];
            const bare = s => (s || '').trim().toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/$/, '');
            const qb = bare(q);
            let a = as.find(x => x.href === q) || as.find(x => x.getAttribute('href') === q)
                 || as.find(x => bare(x.innerText) === qb)            // the model turned link text into a URL
                 || as.find(x => qb.length > 5 && (bare(x.innerText).includes(qb) || bare(x.href).includes(qb)
                                                   || (bare(x.innerText).length > 5 && qb.includes(bare(x.innerText)))));
            if (a) return a.href;
            try { return new URL(q, location.href).href; } catch (e) { return ''; } })()""" % probe,
            "returnByValue": True}, session_id=here.session_id)
        url = res.get("result", {}).get("value") or ""
        if not re.match(r"^https?://", url):
            return ActionResult(error=f"Couldn't find a link matching {link!r} on this page (try page_links first).")
        root = browser_session._cdp_client_root
        target = (await root.send.Target.createTarget(params={"url": url, "background": True}))["targetId"]
        try:
            session = (await root.send.Target.attachToTarget(params={"targetId": target, "flatten": True}))["sessionId"]

            async def js(expr):
                res = await root.send.Runtime.evaluate(params={"expression": expr, "returnByValue": True},
                                                       session_id=session)
                return res.get("result", {}).get("value")

            for _ in range(40):  # up to ~10 s for the page to load
                await asyncio.sleep(0.25)
                if await js("document.readyState") == "complete":
                    break
            await asyncio.sleep(0.8)  # let client-rendered pages paint their text
            title = (await js("document.title")) or ""
            final_url = (await js("location.href")) or url
            text = re.sub(r"\n{3,}", "\n\n", (await js("document.body ? document.body.innerText : ''") or "")).strip()
        except Exception as e:
            return ActionResult(error=f"Couldn't read {url}: {type(e).__name__}")
        finally:
            try:
                await root.send.Target.closeTarget(params={"targetId": target})
            except Exception:
                pass
        if capture is not None:
            capture.append(f"{title}\n{text}")  # so summaries of the linked page can be checked too
        for key in (link, url, final_url):
            opened[_bare(key)] = (final_url, title)
        for p in collector or []:  # the post may have been saved before its link was read
            for l in p["links"]:
                if _bare(l["url"]) in (_bare(link), _bare(url), _bare(final_url)):
                    l["url"], l["title"] = final_url, l.get("title") or title
        body = text[:6000] + ("\n…(truncated)" if len(text) > 6000 else "")
        return ActionResult(extracted_content=f"LINK {final_url}\nTITLE: {title}\n\n{body or '(no readable text)'}",
                            include_extracted_content_only_once=True,
                            long_term_memory=f"Read linked page: {title or final_url}")

    if collector is not None:
        async def real_link(browser_session, u):
            """The post link only if it really exists on the page (models invent anchors like #post-1)."""
            if not u:
                return ""
            here = await browser_session.get_or_create_cdp_session()
            res = await here.cdp_client.send.Runtime.evaluate(params={"expression": """(() => { const q = %s;
                const bare = s => (s || '').trim().toLowerCase().replace(/^https?:\\/\\/(www\\.)?/, '').replace(/\\/$/, '');
                const a = [...document.querySelectorAll('a[href]')].find(x => x.href === q || bare(x.href) === bare(q)
                          || x.getAttribute('href') === q);
                return a ? a.href : ''; })()""" % json.dumps(u), "returnByValue": True}, session_id=here.session_id)
            return res.get("result", {}).get("value") or ""

        def fix_link(link_url, link_title):
            if link_url and _bare(link_url) in opened:  # use the address read_link really opened
                real, title = opened[_bare(link_url)]
                return real, link_title or title
            return link_url, link_title

        @tools.action("Save one post or activity item to the digest as soon as you see it on screen. Call it once "
                      "per item; you don't need to remember items afterwards, and never need to go back up for them. "
                      "quote = the first 10-20 words of the post exactly as shown (used to find its full text). "
                      "url = the post's own link from page_links, or empty. If you read a link in the post with "
                      "read_link, call save_post again for that item with link_url and link_summary.")
        async def save_post(author: str, when: str, quote: str, summary: str, browser_session: BrowserSession,
                            url: str = "", link_url: str = "", link_title: str = "", link_summary: str = "") -> ActionResult:
            key = re.sub(r"\W+", " ", quote.lower()).strip()[:60]
            link_url, link_title = fix_link(link_url, link_title)
            link = {"url": link_url, "title": link_title, "summary": link_summary} if link_url else None
            for p in collector:
                if re.sub(r"\W+", " ", p["quote"].lower()).strip()[:60] == key:  # same item again: merge what's new
                    if link and not any(_bare(l["url"]) == _bare(link_url) for l in p["links"]):
                        p["links"].append(link)
                    elif link:
                        for l in p["links"]:
                            if _bare(l["url"]) == _bare(link_url):
                                l.update({k: v for k, v in link.items() if v})
                    return ActionResult(extracted_content="Updated that item. Keep going from here.")
            collector.append({"author": author, "when": when, "quote": quote, "summary": summary,
                              "url": await real_link(browser_session, url), "links": [link] if link else []})
            return ActionResult(extracted_content=f"Saved ({len(collector)} so far). Keep going from here.",
                                long_term_memory=f"Saved post: {author} {when}: {quote[:40]}")

    return tools


def make_agent(task, model_id, fallback_id, browser, human, on_step=None, vision=None, read_only=False, capture=None,
               collector=None, turbo_mode=False,
               **agent_kwargs):
    gate = Gate(human, read_only=read_only)

    async def step_hook(state, output, n):
        if turbo_mode and n == 1:
            try:
                curr_target = browser.session_manager.get_current_target()
                if curr_target:
                    session = await browser.session_manager.get_or_create_session(curr_target.target_id)
                    await turbo.enable_cdp_ad_blocking(session)
            except Exception:
                pass
        if on_step:
            result = on_step(state, output, n)
            if asyncio.iscoroutine(result):
                await result
        await gate.check(state, output, n)  # may wait for the human, or raise GateBlocked

    secrets = {k[7:]: v for k, v in os.environ.items() if k.startswith("SECRET_") and v}
    if vision is None:
        vision = MODELS[model_id]["vision"] and os.environ.get("LLM_VISION", "1") == "1"

    # Sandboxed Turbo Accelerator: Deterministic instant navigation on Step 0
    initial_actions = None
    if turbo_mode:
        dest_url = turbo.extract_target_url(task)
        if dest_url:
            initial_actions = [{"navigate": {"url": dest_url, "new_tab": True}}]

    # Fallback to standard about:blank behavior if turbo is disabled or no URL found
    if not initial_actions:
        initial_actions = [{"navigate": {"url": "about:blank", "new_tab": True}}]

    return Agent(
        task=task.strip(), llm=make_llm(model_id),
        fallback_llm=make_llm(fallback_id) if fallback_id and fallback_id != model_id else None,
        browser=browser, tools=build_tools(human, gate, capture, collector), extend_system_message=POLICY,
        sensitive_data=secrets or None, use_vision=vision,
        register_new_step_callback=step_hook,
        initial_actions=initial_actions,
        max_failures=4, step_timeout=6 * 3600,  # a step may wait on the human for a long time
        llm_timeout=150,  # the default 75 s was too short for flash models on long pages
        use_judge=False,  # extra LLM pass that grades the run; it added minutes and failed in tests
        **agent_kwargs,
    )
