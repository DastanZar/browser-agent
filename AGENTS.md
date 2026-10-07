# Working agreement for agents (and humans) on this repo

Two AI agents improve this product in parallel, each instructed by the owner. This file is how we avoid
undoing each other's work. Read it at the start of every session.

## Every session

1. **Sync first:** `git pull --rebase origin main` before you change anything. Read new entries in `CHANGELOG.md`
   and `docs/reviews/` since your last session.
2. **Change, then prove it:**
   - Run `python tests/test_offline.py`; all tests must pass.
   - If you touched the agent loop (prompts, policy, tools, models, timeouts), run the benchmark before and after,
     one change at a time: `python bench/run.py --model <id> [--fast] --tag <name>`. Put both score lines in
     the CHANGELOG. Live sites come after the benchmark, not instead of it.
3. **Log it:** add an entry at the top of `CHANGELOG.md` (format below) in the same commit as the change.
4. **Push small:** one topic per commit, a clear message, `git pull --rebase` again, then `git push origin main`.
   - Never force-push.
   - Never rewrite the other agent's commits.

## When you disagree with something the other agent did

- Don't silently revert it. Fix it, and explain why in a short note in `docs/reviews/<date>-<agent>.md`: what
  you changed, the evidence (a test, a measurement, a log), and what you kept.
- If the evidence isn't conclusive, leave the code alone and write the note instead. The owner decides.
- Measurements beat claims. "Faster" needs before/after numbers on the same task.

## Rules that protect the owner (don't weaken these without the owner's explicit OK)

- **Network access:**
  - The dashboard listens on `127.0.0.1` only. LAN access is opt-in (`DASHBOARD_HOST=0.0.0.0`).
  - The Host-header check stays: it stops DNS rebinding.
  - API calls need the `X-Token` header. A cookie alone must never authorise an API call.
- **The approval gate (`core.Gate`)** stays in code. Pay, send, delete, publish, accept terms, and browser
  security warnings ("Proceed (unsafe)") always need a human. Read-only digests refuse those clicks outright.
- **Passwords:** saved passwords may only be typed into password fields. Never log or commit secrets: API keys
  live in `~/.config/...` files or environment variables.
- **The human's own tabs:** never navigated or read unless the task names them. Each task starts in its own tab.
- **The sign-in window** is never force-closed (forcing it loses fresh logins; this was tested).
- **Digest text** shown to the owner must come from the page (`digest.verify`), never from model memory.
- **No personal data in the repo.** This repo is public. Names, profiles, contact details, or the owner's
  connections copied from sites never go into commits, reports or test fixtures. Use counts or made-up
  examples. Run logs stay in `runs/` (git-ignored).
- **Site rules:** no bulk collection of people's profiles (lead lists across companies). LinkedIn's User
  Agreement §8.2 bans bots and scraping, and it puts the owner's account at risk. The agent helps with what a
  person would do by hand, at a person's pace.

## CHANGELOG entry format

```
## YYYY-MM-DD · <agent name> · <one-line summary>
- What changed and why (one line each).
- Evidence: test names, or numbers ("YouTube task 12.3 s, was 148 s").
- Open questions / follow-ups for the other agent.
```

## Where things are

| File | What |
|---|---|
| `core.py` | Models, Chrome connection, policy prompt, approval gate, tools (`scroll_feed`, `read_link`, `save_post`…) |
| `dashboard.py` / `dashboard.html` | Local web console (Starlette) and its UI |
| `digest.py` | Watchlist, read-only digest runs, check against page text, seen-memory |
| `notion_sync.py` | Optional Notion Kanban sync |
| `agent.py` | Terminal version |
| `tests/test_offline.py` | Regression tests (no network, no key) |
| `bench/` | Benchmark: local test site with made-up data (`testsite.py`) + scored tasks (`run.py`); results in `bench/results/` (git-ignored) |
| `tools/analyze_run.py` | Where a run's steps went: errors, loops, page size, fast mode |
| `docs/ENGINEERING.md` | Why things are the way they are, with benchmarks and test logs |
