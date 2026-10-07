"""Explain where a run's steps went: errors, loops, page size, fast mode, memory.

    python tools/analyze_run.py runs/20261007-113021-1791350885.json [--steps]

Reads the history file the dashboard saves after each task (runs/*.json). Needs no browser and no key.
Prints counts, actions and URLs (profile names cut out), never page text. Still check before pasting it anywhere public.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path


def action_name(a):
    return next(iter(a), "?") if isinstance(a, dict) else "?"


def redact(text):
    return re.sub(r"/in/[^/?#\s)]+", "/in/…", text)  # LinkedIn-style profile slugs carry names


def action_key(a):
    """Action plus its main argument, so 'scroll down' repeated 5 times shows up as a loop."""
    name = action_name(a)
    args = a.get(name) or {}
    if not isinstance(args, dict):
        return name
    arg = args.get("url") or args.get("query") or args.get("text") or args.get("index") or args.get("down")
    return f"{name}({redact(str(arg))[:60]})" if arg is not None else name


def error_kind(err):
    err = err or ""
    for pattern, label in [(r"timed? ?out|TimeoutError", "timeout"), (r"[Ss]croll", "scroll"),
                           (r"validation|Field required|Extra inputs", "bad tool call (schema)"),
                           (r"not (found|available)|index", "element missing"), (r"GateBlocked|declined|refused", "gate")]:
        if re.search(pattern, err):
            return label
    return "other"


def analyze(path, show_steps=False):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = data.get("history", data if isinstance(data, list) else [])
    out = []
    say = out.append

    durations, prompt_chars, errors, actions, urls, memory_len = [], [], Counter(), [], [], []
    no_output = fast_steps = 0
    for i, s in enumerate(steps, 1):
        mo = s.get("model_output") or {}
        meta = s.get("metadata") or {}
        dur = (meta.get("step_end_time") or 0) - (meta.get("step_start_time") or 0)
        durations.append(dur)
        prompt_chars.append(len(s.get("state_message") or ""))
        url = redact((s.get("state") or {}).get("url", ""))
        urls.append(url)
        if mo.get("memory") == "Initial navigation":
            pass  # the dashboard's own first navigation, not a model step
        elif not mo:
            no_output += 1
        elif mo.get("thinking") is None and mo.get("evaluation_previous_goal") is None:
            fast_steps += 1
        memory_len.append(len(mo.get("memory") or ""))
        acts = mo.get("action") or []
        actions.extend(action_key(a) for a in acts)
        errs = [r.get("error") for r in s.get("result") or [] if r.get("error")]
        for e in errs:
            errors[error_kind(e)] += 1
        if show_steps:
            say(f"{i:>3} {dur:6.1f}s {prompt_chars[-1] // 4:>6} tok  {url[:50]:<50} "
                f"{', '.join(action_key(a) for a in acts)[:70]}{'  ERR ' + error_kind(errs[0]) if errs else ''}")

    n = len(steps)
    model_steps = sum(1 for s in steps if (s.get("model_output") or {}).get("memory") not in (None, "Initial navigation"))
    if not n:
        return "empty history"
    final = steps[-1].get("result") or [{}]
    done = any(r.get("is_done") for r in final)
    success = any(r.get("success") for r in final)
    say(f"\nsteps: {n}   finished: {done}   reported success: {success}   total time: {sum(durations):.0f}s "
        f"(median step {sorted(durations)[n // 2]:.1f}s, slowest {max(durations):.1f}s)")
    say(f"fast mode: {fast_steps}/{model_steps} steps had no thinking/evaluation "
        f"(fast mode strips the self-check, planning and loop rules from the system prompt)")
    say(f"steps where the model returned nothing (timeout / bad JSON): {no_output}")
    say(f"page state sent per step: median ~{sorted(prompt_chars)[n // 2] // 4} tokens, max ~{max(prompt_chars) // 4} tokens")
    say(f"errors: {dict(errors) or 'none'}")
    say(f"memory field: median {sorted(memory_len)[n // 2]} chars, max {max(memory_len)} chars "
        f"(data kept only here is lost when it gets summarised)")

    # Loops: the same action repeated, and long stays on one URL.
    rep = [(k, c) for k, c in Counter(actions).most_common(5) if c >= 3]
    say(f"repeated actions (3+): {rep or 'none'}")
    longest, run_len, prev = (0, ""), 0, None
    for u in urls:
        run_len = run_len + 1 if u == prev else 1
        prev = u
        if run_len > longest[0]:
            longest = (run_len, u)
    say(f"longest stay on one URL: {longest[0]} steps ({re.sub(r'[?#].*', '', longest[1])[:80]})")
    by_kind = Counter(action_name(a) for s in steps for a in (s.get("model_output") or {}).get("action") or [])
    say(f"actions by type: {dict(by_kind.most_common())}")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(analyze(sys.argv[1], show_steps="--steps" in sys.argv))
