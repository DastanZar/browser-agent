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
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import core

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


async def main():
    ap = argparse.ArgumentParser(description="General-purpose browser agent")
    ap.add_argument("task", nargs="?", help="what to do, in plain words")
    ap.add_argument("--file", help="read the task from a file")
    ap.add_argument("--chat", action="store_true", help="after each task, ask for the next one")
    ap.add_argument("--model", default=core.CONFIG["default"], choices=list(core.MODELS))
    ap.add_argument("--fallback", default=core.CONFIG["fallback"], choices=list(core.MODELS))
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
    hint = await core.visible_tab_hint(browser)
    agent = core.make_agent(task + ("\n\n" + hint if hint else ""), args.model, args.fallback, browser, terminal_human)

    Path("runs").mkdir(exist_ok=True)
    ok = True
    while True:
        history = await agent.run(max_steps=int(os.environ.get("MAX_STEPS", "100")))
        out = Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        history.save_to_file(out)
        ok = bool(history.is_successful())
        print(f"\n=== result ({'done' if ok else 'not finished'}; log: {out}) ===")
        print(history.final_result() or "(no final answer; see the log above)")
        if not args.chat:
            break
        nxt = (await asyncio.to_thread(input, "\nnext task (blank to quit)> ")).strip()
        if not nxt:
            break
        agent.add_new_task(nxt)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
