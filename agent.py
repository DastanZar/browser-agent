"""Terminal version of the browser agent (the dashboard is dashboard.py).

    python agent.py "Find the 3 cheapest flights BLR->DEL on Friday and put them in a table"
    python agent.py --file tasks/my-task.md --model glm-5.3-flash
    python agent.py --chat                      # keep giving it follow-up tasks in one session

It pauses only for ask_human / hand_over / confirm (see core.py). Models come from models.json;
the key from BAI_API_KEY or ~/.config/bai/key.
Env: CDP=auto|mine|agent|<url>, MAX_STEPS=100, ALLOWED_DOMAINS, AUTO_CONFIRM=1, NTFY_TOPIC,
LLM_VISION=0, SECRET_<NAME>=value (typed without the model seeing it: <secret>NAME</secret>).
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import core
import quickread

HEADERS = {"ask": "AGENT QUESTION", "handover": "YOUR TURN IN THE BROWSER", "confirm": "APPROVAL NEEDED"}
HINTS = {"ask": "your answer> ", "handover": "do it in Chrome, then press Enter (or type a note)> ",
         "confirm": "approve? [y/N or instructions]> "}


def notify(text):
    print("\a", end="", flush=True)  # terminal bell
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=text[:300].encode(), headers={"Title": "Browser agent needs you"}),
                timeout=5)
        except Exception as e:  # a failed push must never kill the run
            print(f"(ntfy failed: {e})")


async def terminal_human(kind, text):
    notify(text)
    print(f"\n{'=' * 70}\n{HEADERS[kind]}\n{text}\n{'-' * 70}")
    return await asyncio.to_thread(input, HINTS[kind])


def print_items(items, items_file):
    """Save the items next to the log and print them (⚠ marks a value not found on the page)."""
    if not items:
        return
    items_file.write_text(json.dumps(items, indent=1), encoding="utf-8")
    print(f"\n=== saved items: {len(items)} (also in {items_file}; ⚠ = not found on the page) ===")
    for it in items:
        print("  " + "  |  ".join(f"{v}{' ⚠' if k in it['unverified'] else ''}" for k, v in it["fields"].items()))


async def main():
    ap = argparse.ArgumentParser(description="General-purpose browser agent")
    ap.add_argument("task", nargs="?", help="what to do, in plain words")
    ap.add_argument("--file", help="read the task from a file")
    ap.add_argument("--chat", action="store_true", help="after each task, ask for the next one")
    ap.add_argument("--model", default=core.CONFIG["default"], choices=list(core.MODELS))
    ap.add_argument("--fallback", default=core.CONFIG["fallback"], choices=list(core.MODELS))
    ap.add_argument("--fast", action="store_true", help="Browser Use flash mode: quicker, but no step-by-step checks; "
                    "short tasks only (it lost accuracy on long ones, see docs/reviews/2026-10-07-claude-accuracy.md)")
    ap.add_argument("--no-fast", action="store_true", help=argparse.SUPPRESS)  # old flag; full mode is now the default
    ap.add_argument("--no-quick", action="store_true", help="skip the quick read (read-only tasks with a URL are tried "
                    "first by reading the pages in Python, then handed to the agent if they need clicks)")
    ap.add_argument("--browser", default=None, help="auto | mine | agent | <cdp url>  (default: $CDP or auto)")
    args = ap.parse_args()
    task = Path(args.file).read_text() if args.file else args.task
    if not task and args.chat:
        task = input("task> ").strip()
    if not task:
        ap.error("give a task, --file, or --chat")

    cdp_url, where = core.resolve_cdp(args.browser)
    print(f"browser: {where}   model: {args.model} (fallback {args.fallback})")
    browser = core.make_browser(cdp_url)
    await browser.start()
    task_text, quick_items = task, []
    if not args.no_quick and quickread.route(task) == "read":
        try:
            res = await quickread.quick_read(task, args.model, browser, on_progress=print,
                                          fallback_id=args.fallback)
        except Exception as e:  # noqa: BLE001  a quick-read problem never stops the task
            print(f"quick read failed ({type(e).__name__}: {e}); using the full agent")
            res = None
        if res is not None and res.status == "done":
            Path("runs").mkdir(exist_ok=True)
            out = Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S-%f}.quick.json"
            out.write_text(json.dumps({"task": task, "answer": res.answer, "rounds": res.rounds, "pages": res.pages,
                                       "seconds": res.seconds, "model_seconds": res.model_seconds}, indent=1), encoding="utf-8")
            print(f"\n=== result (done by quick read; log: {out}) ===")
            print(res.answer or "(no answer text)")
            print_items(res.items, out.with_name(out.name.replace(".quick.json", ".items.json")))
            if args.chat:
                print("(quick read done; the next task starts a fresh agent)")
                # follow-up turns need an agent: fall through only when the user gives one
                nxt = (await asyncio.to_thread(input, "\nnext task (blank to quit)> ")).strip()
                if not nxt:
                    return 0
                task, task_text, quick_items = nxt, nxt, []
            else:
                return 0
        elif res is not None:
            print(f"quick read handed over to the agent: {res.notes}")
            task_text, quick_items = task + "\n\n" + res.notes, res.items
    hint = await core.visible_tab_hint(browser)
    agent = core.make_agent(task_text + ("\n\n" + hint if hint else ""), args.model, args.fallback, browser, terminal_human,
                            flash_mode=args.fast)
    agent.saved_items.extend(quick_items)

    Path("runs").mkdir(exist_ok=True)
    ok = True
    while True:
        history = await agent.run(max_steps=int(os.environ.get("MAX_STEPS", "100")))
        out = Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        history.save_to_file(out)
        ok = bool(history.is_successful())
        print(f"\n=== result ({'done' if ok else 'not finished'}; log: {out}) ===")
        print(history.final_result() or "(no final answer; see the log above)")
        if agent.saved_items:
            print_items(agent.saved_items, out.with_suffix(".items.json"))
        if not args.chat:
            break
        nxt = (await asyncio.to_thread(input, "\nnext task (blank to quit)> ")).strip()
        if not nxt:
            break
        agent.add_new_task(nxt)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
