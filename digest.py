"""Watchlist digest: the agent reads pages you'd otherwise doomscroll and brings back only what's new.

A watch is a page (a friend's LinkedIn activity, an X list, a subreddit, a blog) plus an optional
focus ("only job changes and launches"). A digest run opens each watch in turn, read-only, scrolls
like a person would, and returns the posts as structured text (author, when, summary, text, link).
Items it has shown you before are recognised and marked as not new.

Text, not screenshots: it is searchable, tiny, and the model can summarise it. The agent saves each
item as it sees it (save_post: author, time, a short quote, a summary); the full text is then taken
from page text we capture ourselves, never from the model's memory.

This is personal, low-volume reading in your own logged-in browser, at human pace. It is still
automated access, which some sites' terms (LinkedIn's §8.2, for one) prohibit; keep the watchlist
short and the schedule daily.
"""
import difflib
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from pathlib import Path

APP_DIR = Path(os.environ.get("BROWSER_AGENT_HOME") or Path.home() / ".browser-agent")

WATCHES = APP_DIR / "watches.json"
SEEN = APP_DIR / "seen.json"
DIGESTS = APP_DIR / "digests.jsonl"
SETTINGS = APP_DIR / "settings.json"


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, data):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- watches and settings

def list_watches():
    return _read(WATCHES, [])


def add_watch(name, url, focus="", max_scrolls=5, open_links=3):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    rows = list_watches()
    rows.append({"id": uuid.uuid4().hex[:8], "name": name.strip() or url, "url": url, "focus": focus.strip(),
                 "max_scrolls": max(1, min(int(max_scrolls or 5), 20)),
                 "open_links": max(0, min(int(open_links if open_links is not None else 3), 10)), "enabled": True})
    _write(WATCHES, rows)


def update_watch(watch_id, **fields):
    rows = list_watches()
    for r in rows:
        if r["id"] == watch_id:
            r.update({k: v for k, v in fields.items() if k in ("enabled", "name", "focus", "max_scrolls", "open_links")})
    _write(WATCHES, rows)


def remove_watch(watch_id):
    _write(WATCHES, [r for r in list_watches() if r["id"] != watch_id])


def settings():
    return {"daily_at": "", **_read(SETTINGS, {})}


def save_settings(**fields):
    _write(SETTINGS, {**settings(), **fields})


# ---------------------------------------------------------------- the task the agent gets

def task_for(watch):
    last = _read(SEEN, {}).get(watch["id"], {}).get("last_run")
    since = f"since {last[:16].replace('T', ' ')} UTC" if last else "from roughly the last 7 days"
    focus = f"\nOnly save items about: {watch['focus']}." if watch.get("focus") else ""
    n_links = watch.get("open_links", 3)
    links_rule = ("- Don't open links in posts." if not n_links else
                  f"- If a post links to an article, page, video or another post (not just a profile or hashtag), read it\n"
                  f"  with read_link (pass the link's text or address), up to {n_links} links in total, and include\n"
                  f"  link_url / link_title / link_summary when you save that post. read_link leaves this page where\n"
                  f"  it is: carry on from the same spot afterwards.")
    return f"""Read-only digest. Open {watch['url']} and save the recent posts and activity {since}.{focus}

How to work:
- Reading only: never like, comment, react, follow, connect, message, share or post, or change settings.
- As soon as an item is on screen, call save_post for it (once per item): author, when (as shown), quote
  (its first 10-20 words exactly as shown), a one or two sentence summary of what's new, and its link if
  you can see it. The full text is taken from the page automatically, so you never need to copy it,
  remember it, or go back up the page for it.
- Start with the items at the top, then use scroll_feed to move down (at most {watch['max_scrolls']} times).
  Stop when items are older than the period above, or scroll_feed says you're at the bottom.
{links_rule}
- Use page_links if you need a post's real link; don't click into posts.
- If you're logged out or blocked, don't try to get around it: say so when you finish.

When done, finish with a one-line note (e.g. "saved 6 items, stopped at a 9-day-old post")."""


# ---------------------------------------------------------------- remembering what you've seen

def _fingerprint(post, use_url=True):
    if use_url and post.get("url"):
        return "u:" + re.sub(r"[?#].*$", "", post["url"].strip().lower())
    # The text comes from the page, so it's stable between runs; the model's wording of the author isn't.
    body = re.sub(r"\W+", " ", ((post.get("text") or post.get("author", "") + " " + post.get("summary", ""))[:200]).lower())
    return "t:" + hashlib.sha1(body.strip().encode()).hexdigest()[:16]


def _norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _grams(text, n=4):
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


_COMMON = set("""a an and the she he they it its this that these those her his their our we you i in on at of for
to with from by also new post posts posted shares shared says said announces announced update today yesterday
note notes nothing no one two""".split())


def verify(post, page_text):
    """Fill in a saved post's full text from what was really on the page, and check the model's claims.

    The model only gives a short quote; the post's text is the line(s) of captured page text that
    contain it. post["check"]: "verbatim" (quote found as written), "corrected" (found only by a
    fuzzy match: the model misquoted) or "unverified" (nothing on the page matched). Summaries that
    name people, companies or numbers absent from the page are replaced by the real text."""
    lines = [l.strip() for l in (page_text or "").splitlines() if len(l.strip()) > 2]
    quote = _norm(post.get("quote") or post.get("text") or "")
    probe = quote[:80]
    hits = [l for l in lines if probe and probe in _norm(l)]
    if hits:
        post["text"], post["check"] = max(hits, key=len)[:1500], "verbatim"
    else:
        best, score = "", 0.0
        for l in lines:
            if len(l) < 15:
                continue
            r = difflib.SequenceMatcher(None, quote[:200], _norm(l)[:max(200, len(quote))]).ratio()
            if r > score:
                best, score = l, r
        if score >= 0.6:
            post["text"], post["check"] = best[:1500], "corrected"
        else:
            post["text"], post["check"] = post.get("quote", ""), "unverified"
    corpus = _norm(page_text)
    claims = {re.sub(r"['’]s$", "", c) for c in re.findall(r"\b(?:[A-Z][\w&.'’-]+|\d[\d,.%]*)", post.get("summary", ""))}
    allowed = corpus + " " + _norm(post.get("author"))
    missing = [c for c in claims if c.lower() not in _COMMON and c.lower().rstrip(".") not in allowed]
    real = re.sub(r"\s+", " ", post.get("text", "")).strip()
    excerpt = real[:220] + ("…" if len(real) > 220 else "")
    if post["check"] == "unverified":
        post["summary"] = "Couldn't find this on the page; open the link before relying on it. Model's summary: " + post.get("summary", "")
    elif missing:
        post["summary"], post["summary_replaced"] = excerpt, missing[:5]
    for link in post.get("links") or []:  # linked pages were captured by read_link, so check those summaries too
        names = {re.sub(r"['’]s$", "", c) for c in re.findall(r"\b(?:[A-Z][\w&.'’-]+|\d[\d,.%]*)", link.get("summary", ""))}
        bad = [c for c in names if c.lower() not in _COMMON and c.lower().rstrip(".") not in corpus]
        if bad:
            link["summary"], link["check"] = "(summary withheld: it named things that weren't on the linked page)", "unverified"
    return post


def record(watch, posts, notes="", page_text="", ok=True):
    """Check the saved posts against the page, mark which are new, and append the run to the digest log."""
    seen = _read(SEEN, {})
    mine = seen.setdefault(watch["id"], {"hashes": [], "last_run": None})
    known = set(mine["hashes"])
    items = []
    def page_of(u):
        return re.sub(r"[?#].*$", "", (u or "").strip()).rstrip("/").lower()

    # Fill in text from the page first, then merge items that turn out to be the same post
    # (models sometimes save one post twice with slightly different quotes).
    merged = []
    for post in posts or []:
        d = verify(dict(post), page_text)
        if any(page_of(l.get("url")) == page_of(d.get("url")) for l in d.get("links") or []):
            d["url"] = ""  # that's the post's outbound link, not the post itself
        twin = next((m for m in merged if m["check"] != "unverified" and _norm(m["text"]) == _norm(d["text"])), None)
        if twin:
            for l in d.get("links") or []:
                if not any(page_of(x["url"]) == page_of(l["url"]) for x in twin["links"]):
                    twin["links"].append(l)
            twin["url"] = twin.get("url") or d.get("url", "")
            continue
        d["links"] = [l for l in d.get("links") or [] if page_of(l.get("url")) != page_of(watch["url"])]
        merged.append(d)
    urls = [page_of(p.get("url")) for p in merged if p.get("url")]
    for d in merged:
        # A URL identifies a post only if it's unique in this run and isn't just the watched page.
        u = page_of(d.get("url"))
        own = bool(u) and urls.count(u) == 1 and u != page_of(watch["url"])
        if not own and u == page_of(watch["url"]):
            d["url"] = ""
        # Remember a post by its link AND by its page text, and count it as seen if either matches:
        # the model doesn't always report the link, but the text (taken from the page) is stable.
        fps = {_fingerprint(d, use_url=False)} if d["check"] != "unverified" else set()
        if own:
            fps.add(_fingerprint(d, use_url=True))
        if not fps:
            fps = {_fingerprint(d, use_url=False)}
        d["new"] = not (fps & known)
        known |= fps
        items.append(d)
    mine["hashes"] = list(known)[-2000:]
    if ok:  # a failed run must not move the "since" marker, or the next run would skip what it missed
        mine["last_run"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    _write(SEEN, seen)
    entry = {"at": time.time(), "watch_id": watch["id"], "watch": watch["name"], "url": watch["url"],
             "items": items, "notes": notes}
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with DIGESTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recent(limit=30):
    if not DIGESTS.exists():
        return []
    rows = []
    for line in DIGESTS.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows[::-1]
