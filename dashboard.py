"""Local dashboard for the browser agent: type tasks, watch it work, answer it when it needs you.

    python dashboard.py            # opens http://127.0.0.1:8770 in your browser

Only reachable from this machine. Every API call needs the per-session token embedded in the
page, and the Host header is checked, so other websites can't drive your browser through it.
"""
import asyncio
import base64
import contextlib
import itertools
import json
import os
import secrets
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

import socket

import core
import digest
import notion_sync
import turbo

# Local-only by default. LAN access (e.g. from a phone) is opt-in: DASHBOARD_HOST=0.0.0.0.
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
LAN = HOST not in ("127.0.0.1", "localhost", "::1")
PORT = int(os.environ.get("DASHBOARD_PORT", "8770"))
TOKEN = os.environ.get("DASHBOARD_TOKEN") or secrets.token_urlsafe(24)

def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
RUNS_DIR = core.HERE / "runs"
HISTORY = RUNS_DIR / "history.jsonl"
MAX_STEPS = int(os.environ.get("MAX_STEPS", "100"))
DIGEST_GAP = int(os.environ.get("DIGEST_GAP", "20"))          # seconds between pages, to read at a human pace
UNATTENDED_WAIT = int(os.environ.get("UNATTENDED_WAIT", "1200"))  # how long a digest waits for you before moving on
_ids = itertools.count(int(time.time()))


class Run:
    def __init__(self, task, model, fallback, follow_up=False, fast=True, watch=None, turbo=True, optimized=False):
        self.id, self.task, self.model, self.fallback = next(_ids), task, model, fallback
        self.follow_up, self.fast = follow_up, fast
        self.watch = watch                      # set for digest runs
        self.kind = "digest" if watch else "task"
        self.status, self.steps, self.result, self.ok = "queued", [], None, None
        self.started = self.ended = None
        self.prompt, self.future, self.shot = None, None, None
        self.notion_page_id = None
        self.turbo, self.optimized = turbo, optimized

    def public(self, full=True):
        d = {k: getattr(self, k, None) for k in ("id", "kind", "task", "model", "fallback", "follow_up", "fast", "turbo", "optimized", "status", "result", "ok",
                                                 "started", "ended", "notion_page_id")}
        if full:
            d.update(steps=self.steps[-200:], prompt=self.prompt, has_shot=self.shot is not None)
        return d


class Studio:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.runs: list[Run] = []
        self.current: Run | None = None
        self.agent = None          # the agent currently running (for pause/stop)
        self.convo = None          # the last agent, kept so a follow-up task continues its conversation
        self.convo_model = None
        self.convo_fast = None
        self.convo_turns = 0
        self.browser = None
        self.browser_mode = os.environ.get("CDP", "auto")
        self.browser_where = "not connected"
        self.history = self._load_history()
        self.last_digest_end = 0.0
        self.batch_new = 0

    @staticmethod
    def _load_history():
        if not HISTORY.exists():
            return []
        rows = []
        for line in HISTORY.read_text(encoding="utf-8").splitlines()[-50:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return rows[::-1]

    async def ensure_browser(self):
        if self.browser is not None:
            return
        run = self.current
        if core.signin_open() and run is not None:
            # Wait for the human to close the sign-in window normally (that saves their logins).
            run.status, run.prompt = "waiting", {"id": secrets.token_hex(4), "kind": "handover", "since": time.time(),
                                                 "text": "Close the sign-in Chrome window (click its X) so it saves your "
                                                         "logins. The task starts by itself right after."}
            notify_phone(run.prompt["text"])
            while core.signin_open() and run.status != "stopped":
                await asyncio.sleep(1)
            run.prompt = None
            if run.status == "waiting":
                run.status = "running"
        cdp_url, where = core.resolve_cdp(self.browser_mode)
        browser = core.make_browser(cdp_url)
        await browser.start()  # your Chrome may ask "Allow remote debugging?" once: click Allow
        self.browser, self.browser_where = browser, where

    async def drop_browser(self):
        if self.browser is not None:
            try:
                await self.browser.stop()  # disconnects; keep_alive leaves Chrome and its tabs alone
            except Exception:
                pass
        self.browser, self.browser_where = None, "not connected"
        self.new_conversation()  # a conversation is tied to its browser connection

    async def human(self, run, kind, text):
        loop = asyncio.get_running_loop()
        run.future = loop.create_future()
        run.prompt = {"id": secrets.token_hex(4), "kind": kind, "text": text, "since": time.time()}
        run.status = "waiting"
        notify_phone(text)
        if getattr(run, "notion_page_id", None):
            asyncio.create_task(asyncio.to_thread(notion_sync.update_task_card_status, run.notion_page_id, "Needs Human"))
        try:
            if run.kind == "digest":  # nobody may be watching: don't wait forever
                try:
                    return await asyncio.wait_for(asyncio.shield(run.future), UNATTENDED_WAIT)
                except asyncio.TimeoutError:
                    return ("no" if kind == "confirm" else
                            "(No answer: the human is away. Skip whatever needs them, note it, and finish.)")
            return await run.future
        finally:
            run.prompt, run.future = None, None
            if run.status == "waiting":
                run.status = "running"
                if getattr(run, "notion_page_id", None):
                    asyncio.create_task(asyncio.to_thread(notion_sync.update_task_card_status, run.notion_page_id, "In Progress"))

    def on_step(self, state, output, n):
        run = self.current
        if run is None:
            return
        if state is not None and getattr(state, "screenshot", None):
            run.shot = state.screenshot
        actions = []
        for a in (output.action if output else []) or []:
            d = a.model_dump(exclude_none=True)
            actions += [f"{k}({_short(v)})" for k, v in d.items()]
        run.steps.append({"n": n, "t": time.time(), "url": getattr(state, "url", ""),
                          "goal": getattr(output, "next_goal", "") or "",
                          "eval": getattr(output, "evaluation_previous_goal", "") or "",
                          "actions": actions})

    async def ask(self, kind, text):
        # Bound to whichever task is running, so one agent can serve several follow-up tasks.
        return await self.human(self.current, kind, text)

    def new_conversation(self):
        self.convo, self.convo_model, self.convo_fast, self.convo_turns = None, None, None, 0

    async def worker(self):
        while True:
            run = await self.queue.get()
            if run.status == "stopped":
                continue
            if run.kind == "digest":  # read one page at a time, at a human pace
                await asyncio.sleep(max(0.0, self.last_digest_end + DIGEST_GAP - time.time()))
            self.current, run.status, run.started = run, "running", time.time()
            if notion_sync.load_config().get("auto_sync") and notion_sync.load_config().get("token") and not getattr(run, "notion_page_id", None):
                try:
                    run.notion_page_id = await asyncio.to_thread(notion_sync.create_task_card, run)
                except Exception as e:
                    print(f"Notion card create error: {e}")
            try:
                await self.ensure_browser()
                if run.kind == "digest":
                    await self.run_digest(run)
                elif (run.follow_up and self.convo is not None and self.convo_model == run.model
                        and self.convo_fast == run.fast):
                    agent = self.convo
                    agent.add_new_task(run.task)  # keeps everything it saw and did in earlier tasks
                else:
                    run.follow_up = False
                    hint = await core.visible_tab_hint(self.browser)
                    agent = core.make_agent(run.task + ("\n\n" + hint if hint else ""), run.model,
                                            run.fallback, self.browser, self.ask, on_step=self.on_step,
                                            flash_mode=run.fast, turbo_mode=run.turbo)
                    self.convo, self.convo_model, self.convo_fast, self.convo_turns = agent, run.model, run.fast, 0
                if run.kind == "task":
                    self.agent = agent
                    history = await agent.run(max_steps=MAX_STEPS)
                    self.convo_turns += 1
                    RUNS_DIR.mkdir(exist_ok=True)
                    history.save_to_file(RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{run.id}.json")
                    run.result = history.final_result() or "(no final answer; see the steps)"
                    run.ok = bool(history.is_successful())
                    if run.status != "stopped":
                        run.status = "done" if run.ok else "unfinished"
            except Exception as e:
                run.status, run.ok = "failed", False
                run.result = f"{type(e).__name__}: {e}"
                self.new_conversation()  # don't continue from a broken state
                if "connect" in str(e).lower() or "websocket" in str(e).lower():
                    await self.drop_browser()  # reconnect on the next task
            finally:
                if getattr(run, "notion_page_id", None):
                    try:
                        await asyncio.to_thread(notion_sync.finalize_task_card, run.notion_page_id, run)
                    except Exception as e:
                        print(f"Notion finalize error: {e}")
                run.ended, self.agent, self.current = time.time(), None, None
                if run.kind == "digest":
                    self.last_digest_end = run.ended
                    if not any(r.kind == "digest" and r.status == "queued" for r in self.runs):
                        if self.batch_new:
                            notify_phone(f"Digest ready: {self.batch_new} new item(s). Open the dashboard to read them.")
                        self.batch_new = 0
                RUNS_DIR.mkdir(exist_ok=True)
                with HISTORY.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(run.public(full=False)) + "\n")
                self.history.insert(0, run.public(full=False))
                del self.history[50:]

    async def run_digest(self, run):
        watch = run.watch
        if notion_sync.load_config().get("auto_sync") and notion_sync.load_config().get("token") and not getattr(run, "notion_page_id", None):
            try:
                run.notion_page_id = await asyncio.to_thread(notion_sync.create_task_card, run)
            except Exception as e:
                pass
        seen_text = []  # what was really on the page, captured by us at every step
        saved = []      # posts the agent saves one by one with save_post

        async def capture(state, output, n):
            self.on_step(state, output, n)
            seen_text.append(await core.page_text(self.browser))

        agent = core.make_agent(digest.task_for(watch), run.model, run.fallback, self.browser, self.ask,
                                on_step=capture, read_only=True, flash_mode=run.fast, capture=seen_text,
                                collector=saved)
        self.agent = agent
        before = {t.target_id for t in self.browser.session_manager.get_all_page_targets()}
        try:
            history = await agent.run(max_steps=10 + 3 * watch["max_scrolls"] + 2 * watch.get("open_links", 3))
        finally:
            seen_text.append(await core.page_text(self.browser))  # the final screen, before the tab closes
            for t in self.browser.session_manager.get_all_page_targets():  # close the tabs this digest opened
                if t.target_id not in before:
                    try:
                        await self.browser._cdp_client_root.send.Target.closeTarget(params={"targetId": t.target_id})
                    except Exception:
                        pass
        notes = history.final_result() or ""
        ok = bool(saved) or bool(history.is_successful())
        entry = digest.record(watch, saved, notes, "\n".join(seen_text), ok=ok)
        new = sum(1 for i in entry["items"] if i["new"])
        self.batch_new += new
        run.ok = ok
        run.result = f"{new} new of {len(entry['items'])} item(s)" + (f". Notes: {notes}" if notes else "")
        if run.status != "stopped":
            run.status = "done" if run.ok else "unfinished"

    def queue_digest(self, model, fallback, fast=True):
        watches = [w for w in digest.list_watches() if w.get("enabled")]
        for w in watches:
            run = Run(f"Digest: {w['name']}", model, fallback, fast=fast, watch=w)
            self.runs.append(run)
            self.queue.put_nowait(run)
        return len(watches)

    async def scheduler(self):
        """Runs the daily digest at the time set in the dashboard, while the dashboard is open."""
        while True:
            await asyncio.sleep(30)
            st = digest.settings()
            at, today = st.get("daily_at") or "", datetime.now().strftime("%Y-%m-%d")
            if at and datetime.now().strftime("%H:%M") >= at and st.get("last_auto") != today:
                digest.save_settings(last_auto=today)
                self.queue_digest(core.CONFIG["digest_model"], core.CONFIG["digest_fallback"])


def _short(v, n=120):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[: n - 1] + "…"


def notify_phone(text):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    import urllib.request

    def send():
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=text[:300].encode(), headers={"Title": "Browser agent needs you"}),
                timeout=5)
        except Exception:
            pass
    asyncio.get_running_loop().run_in_executor(None, send)


studio = Studio()


# ---------------------------------------------------------------- HTTP

def allowed_hosts():
    """Host headers we answer to. Checking it blocks DNS rebinding: a website pointing its own domain at
    127.0.0.1 would otherwise get the page, and the token inside it, as a "local" request."""
    names = {"127.0.0.1", "localhost", "[::1]"}
    if LAN:
        names |= {get_lan_ip(), socket.gethostname(), socket.gethostname() + ".local"}
    names |= {h.strip() for h in os.environ.get("DASHBOARD_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return {f"{n}:{PORT}".lower() for n in names}


def host_ok(request: Request):
    return request.headers.get("host", "").lower() in allowed_hosts()


def is_valid_token(request: Request, allow_cookie=False):
    """API calls must carry X-Token (or ?token for <img> URLs). The cookie is accepted only for loading
    the page itself: a cookie rides along on requests from other sites, a custom header can't."""
    token = request.headers.get("x-token") or request.query_params.get("token")
    if not token and allow_cookie:
        token = request.cookies.get("agy_token")
    return bool(token and secrets.compare_digest(token, TOKEN))


def is_local_request(request: Request):
    client_host = getattr(request.client, "host", "") if request.client else ""
    return client_host in ("127.0.0.1", "::1", "localhost")


def guard(handler):
    async def wrapped(request: Request):
        if not host_ok(request):
            return Response("bad host", status_code=403)
        if not is_valid_token(request):
            return Response("bad token", status_code=403)
        return await handler(request)
    return wrapped


async def page(request: Request):
    if not host_ok(request):
        return Response("bad host", status_code=403)
    # Allow local connections directly; for LAN/external connections, require valid token
    if not is_local_request(request) and not is_valid_token(request, allow_cookie=True):
        token_hint = request.query_params.get("token")
        if not token_hint:
            return Response("Access denied: missing token for network access. Append ?token=<YOUR_TOKEN> to the URL.", status_code=403)
        return Response("Access denied: invalid security token.", status_code=403)
    html = (core.HERE / "dashboard.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
    response = HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})
    if LAN:  # lets a phone reload the page after opening the ?token= link once
        response.set_cookie("agy_token", TOKEN, httponly=True, samesite="strict")
    return response


def templates():
    out = []
    for p in sorted((core.HERE / "tasks").glob("*.md")):
        out.append({"name": p.stem, "text": p.read_text(encoding="utf-8")})
    return out


@guard
async def state(request: Request):
    cur = studio.current
    return JSONResponse({
        "models": core.CONFIG["models"], "default": core.CONFIG["default"], "fallback": core.CONFIG["fallback"],
        "key_set": bool(core.api_key()),
        "notion": notion_sync.get_public_status(),
        "browser": {"mode": studio.browser_mode, "where": studio.browser_where},
        "current": cur.public() if cur else None,
        "paused": bool(studio.agent and getattr(studio.agent.state, "paused", False)),
        "conversation": {"active": studio.convo is not None, "model": studio.convo_model, "turns": studio.convo_turns},
        "queue": [r.public(full=False) for r in studio.runs if r.status == "queued"],
        "recent": [r.public() for r in studio.runs[-10:]][::-1],
        "history": studio.history,
        "templates": templates(),
    })


@guard
async def run_task(request: Request):
    body = await request.json()
    task = (body.get("task") or "").strip()
    model = body.get("model") or core.CONFIG["default"]
    fallback = body.get("fallback") or None
    if not task:
        return JSONResponse({"error": "empty task"}, status_code=400)
    if model not in core.MODELS or (fallback and fallback not in core.MODELS):
        return JSONResponse({"error": "unknown model"}, status_code=400)
    if not core.api_key():
        return JSONResponse({"error": "save your b.ai API key first"}, status_code=400)
    run = Run(task, model, fallback, follow_up=bool(body.get("follow_up")), fast=bool(body.get("fast", True)),
              turbo=bool(body.get("turbo", True)), optimized=bool(body.get("optimized", False)))
    studio.runs.append(run)
    await studio.queue.put(run)
    return JSONResponse({"id": run.id})


@guard
async def optimize_prompt_endpoint(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    model = body.get("model") or "mimo-v2.6-flash"
    if not prompt:
        return JSONResponse({"error": "empty prompt"}, status_code=400)
    if not core.api_key():
        return JSONResponse({"error": "save your b.ai API key first"}, status_code=400)
    try:
        res = await turbo.compile_prompt(prompt, model_id=model)
        if not res.get("ok"):
            return JSONResponse({"error": res.get("error", "Compilation failed")}, status_code=500)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@guard
async def answer(request: Request):
    body = await request.json()
    run = studio.current
    if run and run.prompt and run.prompt["id"] == body.get("prompt_id") and run.future is None:
        return JSONResponse({"error": "close the sign-in Chrome window first; the task continues by itself"}, status_code=409)
    if not run or not run.prompt or run.prompt["id"] != body.get("prompt_id") or run.future.done():
        return JSONResponse({"error": "no such question (already answered?)"}, status_code=409)
    run.future.set_result(str(body.get("answer", "")))
    return JSONResponse({"ok": True})


@guard
async def control(request: Request):
    body = await request.json()
    action = body.get("action")
    agent, run = studio.agent, studio.current
    if action == "pause" and agent:
        agent.pause()
    elif action == "resume" and agent:
        agent.resume()
    elif action == "stop" and run:
        run.status = "stopped"
        if agent:
            agent.stop()
            if getattr(agent.state, "paused", False):
                agent.resume()
        if run.future and not run.future.done():
            run.future.set_result("STOP. The human cancelled this task. Do nothing else and finish now.")
    elif action == "cancel":
        rid = body.get("id")
        for r in studio.runs:
            if r.id == rid and r.status == "queued":
                r.status = "stopped"
    else:
        return JSONResponse({"error": "nothing to do"}, status_code=400)
    return JSONResponse({"ok": True})


@guard
async def set_browser(request: Request):
    mode = (await request.json()).get("mode")
    if mode not in ("auto", "mine", "agent"):
        return JSONResponse({"error": "mode must be auto, mine or agent"}, status_code=400)
    if studio.current:
        return JSONResponse({"error": "wait for the current task to finish"}, status_code=409)
    studio.browser_mode = mode
    await studio.drop_browser()
    try:
        await studio.ensure_browser()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "where": studio.browser_where})


@guard
async def new_conversation(request: Request):
    if studio.current:
        return JSONResponse({"error": "wait for the current task to finish"}, status_code=409)
    studio.new_conversation()
    return JSONResponse({"ok": True})


@guard
async def signin_window(request: Request):
    url = ((await request.json()).get("url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if studio.current:
        return JSONResponse({"error": "wait for the current task to finish"}, status_code=409)
    try:
        await studio.drop_browser()  # the agent reconnects on the next task
        await asyncio.to_thread(core.open_signin_window, url or None)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@guard
async def profile_sync_endpoint(request: Request):
    if studio.current:
        return JSONResponse({"error": "wait for the current task to finish"}, status_code=409)
    try:
        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        prefer = body.get("profile") if isinstance(body, dict) else None
    except Exception:
        prefer = None
    try:
        ok, msg = await asyncio.to_thread(core.sync_user_profile, prefer)
        return JSONResponse({"ok": ok, "message": msg})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=500)


@guard
async def open_inspect_endpoint(request: Request):
    try:
        await asyncio.to_thread(webbrowser.open, "chrome://inspect/#remote-debugging")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------- watchlist and digest

@guard
async def library(request: Request):
    return JSONResponse({"watches": digest.list_watches(), "settings": digest.settings(), "digests": digest.recent(30)})


@guard
async def watches_action(request: Request):
    body = await request.json()
    action = body.get("action")
    try:
        if action == "add":
            if not (body.get("url") or "").strip():
                return JSONResponse({"error": "a watch needs a URL"}, status_code=400)
            digest.add_watch(body.get("name", ""), body["url"].strip(), body.get("focus", ""), body.get("max_scrolls", 5),
                             body.get("open_links", 3))
        elif action == "toggle":
            digest.update_watch(body["id"], enabled=bool(body.get("enabled")))
        elif action == "remove":
            digest.remove_watch(body["id"])
        elif action == "schedule":
            at = (body.get("daily_at") or "").strip()
            if at and not (len(at) == 5 and at[2] == ":" and at.replace(":", "").isdigit()):
                return JSONResponse({"error": "use HH:MM, e.g. 08:30"}, status_code=400)
            digest.save_settings(daily_at=at)
        elif action == "run":
            if not core.api_key():
                return JSONResponse({"error": "save your b.ai API key first"}, status_code=400)
            n = studio.queue_digest(core.CONFIG["digest_model"], core.CONFIG["digest_fallback"],
                                    fast=bool(body.get("fast", True)))
            return JSONResponse({"ok": True, "queued": n})
        else:
            return JSONResponse({"error": "unknown action"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@guard
async def set_key(request: Request):
    key = ((await request.json()).get("key") or "").strip()
    if len(key) < 10:
        return JSONResponse({"error": "that doesn't look like a key"}, status_code=400)
    core.save_api_key(key)
    return JSONResponse({"ok": True})


@guard
async def shot(request: Request):
    run = studio.current
    if not run or not run.shot:
        return Response(status_code=204)
    data = base64.b64decode(run.shot)
    kind = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return Response(data, media_type=kind, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- notion integration

@guard
async def notion_config(request: Request):
    body = await request.json()
    token = body.get("token")
    database_id = body.get("database_id")
    auto_sync = body.get("auto_sync")
    try:
        cfg = notion_sync.save_config(token=token, database_id=database_id, auto_sync=auto_sync)
        test_info = None
        if cfg.get("token"):
            test_info = await asyncio.to_thread(notion_sync.test_connection, cfg.get("token"), cfg.get("database_id"))
        return JSONResponse({"ok": True, "test": test_info, "status": notion_sync.get_public_status()})
    except Exception as e:
        return JSONResponse({"error": str(e), "status": notion_sync.get_public_status()}, status_code=400)


@guard
async def notion_setup(request: Request):
    body = await request.json()
    parent_id = body.get("parent_id") or ""
    title = body.get("title") or "Browser Agent Operations Kanban"
    if not parent_id:
        return JSONResponse({"error": "Parent Page ID or URL is required to auto-create a Notion Kanban board."}, status_code=400)
    try:
        res = await asyncio.to_thread(notion_sync.create_kanban_database, parent_id, title)
        return JSONResponse({"ok": True, "database": res, "status": notion_sync.get_public_status()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@guard
async def notion_sync_run(request: Request):
    body = await request.json()
    run_id = body.get("id")
    if not run_id:
        return JSONResponse({"error": "Task run ID is required."}, status_code=400)

    target_run = None
    for r in studio.runs:
        if r.id == run_id:
            target_run = r
            break

    if not target_run:
        for h in studio.history:
            if h.get("id") == run_id:
                class HistRun:
                    pass
                target_run = HistRun()
                for k, v in h.items():
                    setattr(target_run, k, v)
                if not hasattr(target_run, "steps"):
                    target_run.steps = []
                if not hasattr(target_run, "notion_page_id"):
                    target_run.notion_page_id = None
                break

    if not target_run:
        return JSONResponse({"error": "Task run not found."}, status_code=404)

    try:
        page_id = getattr(target_run, "notion_page_id", None)
        if not page_id:
            page_id = await asyncio.to_thread(notion_sync.create_task_card, target_run)
            target_run.notion_page_id = page_id
        if page_id:
            await asyncio.to_thread(notion_sync.finalize_task_card, page_id, target_run)
            # Update history cache if found
            for h in studio.history:
                if h.get("id") == run_id:
                    h["notion_page_id"] = page_id
            return JSONResponse({"ok": True, "page_id": page_id})
        return JSONResponse({"error": "Failed to create or find Notion card."}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@contextlib.asynccontextmanager
async def lifespan(app):
    worker = asyncio.get_running_loop().create_task(studio.worker())
    scheduler = asyncio.get_running_loop().create_task(studio.scheduler())
    local_url = f"http://127.0.0.1:{PORT}/"
    lan_ip = get_lan_ip()
    print("\n" + "=" * 60)
    print(f"  Browser Automation Suite (Port {PORT})")
    print(f"  Local Dashboard:    {local_url}")
    if LAN:
        print(f"  LAN Access:         http://{lan_ip}:{PORT}/?token={TOKEN}")
        print("  (Network access is ON: anyone with this link on your network can drive your browser.)")
    print("=" * 60 + "\n")
    if os.environ.get("NO_OPEN") != "1":
        # Open the page only once the server is actually listening (lifespan runs before the bind).
        threading.Thread(target=_open_when_ready, args=(local_url,), daemon=True).start()
    yield
    worker.cancel()
    scheduler.cancel()
    await studio.drop_browser()


app = Starlette(routes=[
    Route("/", page),
    Route("/api/state", state),
    Route("/api/run", run_task, methods=["POST"]),
    Route("/api/optimize", optimize_prompt_endpoint, methods=["POST"]),
    Route("/api/answer", answer, methods=["POST"]),
    Route("/api/control", control, methods=["POST"]),
    Route("/api/browser", set_browser, methods=["POST"]),
    Route("/api/key", set_key, methods=["POST"]),
    Route("/api/signin", signin_window, methods=["POST"]),
    Route("/api/profile/sync", profile_sync_endpoint, methods=["POST"]),
    Route("/api/browser/open_inspect", open_inspect_endpoint, methods=["POST"]),
    Route("/api/new", new_conversation, methods=["POST"]),
    Route("/api/library", library),
    Route("/api/watches", watches_action, methods=["POST"]),
    Route("/api/shot", shot),
    Route("/api/notion/config", notion_config, methods=["POST"]),
    Route("/api/notion/setup", notion_setup, methods=["POST"]),
    Route("/api/notion/sync", notion_sync_run, methods=["POST"]),
], lifespan=lifespan)

def _open_when_ready(url):
    for _ in range(100):
        if core.port_open(PORT):
            webbrowser.open(url)
            return
        time.sleep(0.1)


def _already_running():
    """True if a dashboard is already serving on PORT (a second launch just reopens the page)."""
    if not core.port_open(PORT):
        return False
    try:
        check_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
        with urllib.request.urlopen(f"http://{check_host}:{PORT}/", timeout=3) as r:
            return b"Browser" in r.read(4000)
    except Exception:
        return False


if __name__ == "__main__":
    local_url = f"http://127.0.0.1:{PORT}/"
    if _already_running():
        print(f"The dashboard is already running: {local_url}")
        webbrowser.open(local_url)
    elif core.port_open(PORT):
        raise SystemExit(f"Port {PORT} is used by another program. Start with DASHBOARD_PORT=8771 (or any free port).")
    else:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
