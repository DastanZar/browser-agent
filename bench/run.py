"""Benchmark: fixed tasks on the local test site, scored against answer keys.

    python bench/run.py --model qwen3.8-flash [--fast] [--no-save-item] [--tasks people,catalog] [--repeat 2]

Runs each task in a fresh headless Chromium against bench/testsite.py, then scores what the agent returned (its
saved items and its final answer) against the right answers. Results go to bench/results/ (git-ignored):
one JSON line per run, plus each run's history for tools/analyze_run.py.

Change one thing at a time, and run this before and after. Live sites come after this, not instead of it.
"""
import argparse
import asyncio
import inspect
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

import testsite as bench_site  # noqa: E402

RESULTS = Path(os.environ.get("BENCH_RESULTS") or ROOT / "bench" / "results")  # set it to compare two checkouts


# ---------------------------------------------------------------- tasks and answer keys

def tasks(base):
    S = bench_site
    return {
        # Modelled on the real LinkedIn prompt, including its two bad instructions.
        "people": {
            "task": f'Open {base}/people . Search for ("Acme Mobility") AND ("Recruiter" OR "Talent Acquisition"). '
                    "Filter by Experience level: Mid-Senior. Collect the name, title and location of every person "
                    "who currently works at Acme Mobility and whose title contains \"Recruiter\" or \"Talent "
                    "Acquisition\", across all result pages. Return them as a list.",
            "answer": [{"name": p[0], "title": p[1], "location": p[3]} for p in S.PEOPLE if S.is_target(p[1], p[2])],
            "universe": [p[0] for p in S.PEOPLE],
        },
        "catalog": {
            "task": f"Open {base}/catalog and list every product priced under $20.00, with its name and price.",
            "answer": [{"name": p["name"], "price": p["price"]} for p in S.PRODUCTS if p["cents"] < 2000],
            "universe": [p["name"] for p in S.PRODUCTS],
        },
        "companies": {
            "task": f"Open {base}/companies. For each company listed, open its page and note the year it was "
                    "founded and its headquarters city. Return all of them.",
            "answer": [{"name": c, "founded": str(y), "hq": hq} for c, y, hq in S.COMPANIES],
            "universe": [c for c, _, _ in S.COMPANIES],
        },
        "lookup": {  # a short task: catches speed regressions
            "task": f"Open {base}/catalog/item/17 and tell me its price and rating.",
            "answer": [{"name": S.PRODUCTS[16]["name"], "price": S.PRODUCTS[16]["price"], "rating": S.PRODUCTS[16]["rating"]}],
            "universe": [p["name"] for p in S.PRODUCTS],
            "name_optional": True,
        },
    }


# ---------------------------------------------------------------- scoring

def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w$.]+", " ", str(s).lower())).strip()


def has_name(text, name):
    """Whole-name match ('Mug 1' must not match 'Mug 17')."""
    return re.search(r"(?<![\w])" + re.escape(norm(name)) + r"(?![\w])", text) is not None


def segments(text, universe):
    """Split the answer text at each known name: name -> the text up to the next known name."""
    t = norm(text)
    hits = sorted((m.start(), n) for n in universe
                  for m in re.finditer(r"(?<![\w])" + re.escape(norm(n)) + r"(?![\w])", t))
    out = {}
    for k, (pos, n) in enumerate(hits):
        end = hits[k + 1][0] if k + 1 < len(hits) else len(t)
        out.setdefault(n, "")
        out[n] += " " + t[pos:end]
    return out


def score(spec, final_text, items):
    """Right = the person/product is reported with every field correct. Wrong = reported but not in the key.
    Items saved with save_item and the final text both count (a run may use either)."""
    universe, answer = spec["universe"], spec["answer"]
    fields = [k for k in answer[0] if k != "name"]
    item_text = [norm(" ".join(str(v) for v in it.get("fields", it).values())) for it in items or []]
    seg = segments(final_text or "", universe)
    # Names the agent lists as left out ("EXCLUDED:", "Skipped:", "Not included") are not answers.
    kept = re.split(r"(?im)^[\W_]*(exclu\w*|not included|skipped|filtered out|left out|ignored)", final_text or "")[0]
    seg_kept = {n: t for n, t in segments(kept, universe).items()
                if not re.search(r"\bexclu|\bnot included|\bskipped|\bleft out|\bno longer at", t[:120])}  # same sentence
    right = partial = 0
    for a in answer:
        sources = [t for t in item_text if has_name(t, a["name"])] + ([seg_kept[a["name"]]] if a["name"] in seg_kept else [])
        if spec.get("name_optional") and not sources:
            sources = [norm(final_text or "")] + item_text
        if any(all(norm(a[f]) in s for f in fields) for s in sources):
            right += 1
        elif sources:
            partial += 1
    keys = {a["name"] for a in answer}
    reported = set(seg_kept) | {n for n in universe for t in item_text if has_name(t, n)}
    wrong = len(reported - keys)
    invented = sum(1 for t in item_text if not any(has_name(t, n) for n in universe)) if not spec.get("name_optional") else 0
    n = len(answer)
    return {"right": right, "partial": partial, "missed": n - right - partial, "wrong": wrong, "invented": invented,
            "of": n, "recall": round(right / n, 3),
            "precision": round(right / max(1, right + partial + wrong + invented), 3)}


# ---------------------------------------------------------------- running

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_site():
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(bench_site.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return f"http://127.0.0.1:{port}"


async def human(kind, text):
    """Nobody is watching a benchmark: answers stay neutral and approvals are refused."""
    return {"ask": "I'm not available. Use your best judgement and note any assumption.",
            "confirm": "no", "handover": ""}.get(kind, "")


async def run_one(name, spec, model, fallback, fast, save_item, max_steps, tag):
    import core
    from browser_use import Browser
    chrome = os.environ.get("CHROME_PATH") or next(
        (str(p) for p in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None)
    browser = Browser(executable_path=chrome, headless=True, user_data_dir=tempfile.mkdtemp(prefix="bench-"),
                      chromium_sandbox=False, keep_alive=False)
    kwargs = {"flash_mode": fast}
    if "save_items" in inspect.signature(core.make_agent).parameters:
        kwargs["save_items"] = save_item
    elif save_item:
        print("  (this version has no save_item tool; running without it)")
    t0 = time.time()
    status, final, items, steps, history, fell_back = "ok", "", [], 0, None, False
    try:
        agent = core.make_agent(spec["task"], model, fallback, browser, human, **kwargs)
        history = await agent.run(max_steps=max_steps)
        final = history.final_result() or ""
        steps = len(history.history)
        items = list(getattr(agent, "saved_items", []) or [])
        status = "done" if history.is_done() else "not finished"
        fell_back = bool(getattr(agent, "is_using_fallback_llm", False))  # the model changed mid-run
    except Exception as e:  # noqa: BLE001  a crash is a result too
        status = f"crashed: {type(e).__name__}: {str(e)[:120]}"
    finally:
        try:
            await browser.kill()
        except Exception:  # noqa: BLE001
            pass
    secs = round(time.time() - t0, 1)
    row = {"tag": tag, "task": name, "model": model, "fast": fast, "save_item": save_item, "status": status,
           "fell_back": fell_back, "steps": steps, "seconds": secs, "items_saved": len(items), **score(spec, final, items),
           "when": datetime.now().isoformat(timespec="seconds")}
    RESULTS.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}-{tag}-{name}"
    row["answer_file"] = f"{stamp}.answer.json"
    if history is not None:
        history.save_to_file(RESULTS / f"{stamp}.history.json")
    (RESULTS / f"{stamp}.answer.json").write_text(json.dumps({"final": final, "items": items}, indent=1))
    with open(RESULTS / "results.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def rescore():
    specs = tasks("http://x")
    rows = [json.loads(line) for line in open(RESULTS / "results.jsonl")] if (RESULTS / "results.jsonl").exists() else []
    answers = sorted(RESULTS.glob("*.answer.json"))
    print(f"{'tag':<12} {'task':<10} {'status':<14} {'right':>6} {'part':>4} {'wrong':>5} {'inv':>3} {'saved':>5} {'steps':>5} {'secs':>6} fb")
    for r in rows:
        f = RESULTS / r["answer_file"] if r.get("answer_file") else None
        if f is None:  # older rows: the answer file written closest in time to the row
            when = datetime.fromisoformat(r["when"])
            cands = list(RESULTS.glob(f"*-{r['tag']}-{r['task']}.answer.json"))
            f = min(cands, key=lambda a: abs((datetime.strptime(a.name[:15], "%Y%m%d-%H%M%S") - when).total_seconds()),
                    default=None)
        if f:
            ans = json.loads(f.read_text())
            r.update(score(specs[r["task"]], ans["final"], ans["items"]))
        print(f"{r['tag']:<12} {r['task']:<10} {r['status'][:14]:<14} {r['right']:>3}/{r['of']:<2} {r['partial']:>4} {r['wrong']:>5} "
              f"{r['invented']:>3} {r['items_saved']:>5} {r['steps']:>5} {r['seconds']:>6.0f} {'yes' if r.get('fell_back') else ''}")
    print("\nper configuration (all runs): right answers / possible, wrong+invented, mean steps, mean seconds")
    for tag in dict.fromkeys(r["tag"] for r in rows):
        rs = [r for r in rows if r["tag"] == tag]
        per_task = ", ".join(f"{t} {sum(r['right'] for r in rs if r['task'] == t)}/{sum(r['of'] for r in rs if r['task'] == t)}"
                             for t in dict.fromkeys(r["task"] for r in rs))
        print(f"  {tag:<12} {sum(r['right'] for r in rs):>3}/{sum(r['of'] for r in rs):<3} "
              f"wrong {sum(r['wrong'] + r['invented'] for r in rs):<2} steps {sum(r['steps'] for r in rs) / len(rs):5.1f}  "
              f"{sum(r['seconds'] for r in rs) / len(rs):6.0f}s  runs {len(rs):<2} ({per_task})")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3.8-flash")
    ap.add_argument("--fallback", default="deepseek-v4.1-flash")
    ap.add_argument("--fast", action="store_true", help="Browser Use flash mode (short prompt, no thinking)")
    ap.add_argument("--no-save-item", action="store_true")
    ap.add_argument("--tasks", default="people,catalog,companies,lookup")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--tag", default="", help="label for this configuration in results.jsonl")
    ap.add_argument("--rescore", action="store_true", help="score the saved answers again (after a scorer fix) and print a summary")
    args = ap.parse_args()
    if args.rescore:
        return rescore()
    base = start_site()
    specs = tasks(base)
    tag = args.tag or f"{args.model}{'-fast' if args.fast else ''}{'-nosave' if args.no_save_item else ''}"
    for _ in range(args.repeat):
        for name in args.tasks.split(","):
            row = await run_one(name, specs[name], args.model, args.fallback, args.fast, not args.no_save_item,
                                args.max_steps, tag)
            print(f"{tag:<34} {name:<10} {row['status'][:30]:<30} right {row['right']}/{row['of']}  wrong {row['wrong']}  "
                  f"invented {row['invented']}  steps {row['steps']}  {row['seconds']}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
