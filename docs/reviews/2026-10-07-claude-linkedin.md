# Review of 286c4d7…402bb5f (Claude Code, 2026-10-07)

## 1. Personal data on a public repo (fixed in the working tree; history still has it)

- `docs/reviews/2026-10-07-linkedin-findings.md` listed 7 real people from LinkedIn: names, headlines, cities,
  and the names of the owner's mutual connections with them. `DastanZar/browser-agent` is **public**.
- Replaced the table with counts. Added a rule to AGENTS.md: no personal data in the repo.
- **Commit `402bb5f` still contains it.** Removing it from history needs a force-push, which AGENTS.md
  forbids without the owner. The owner decides: make the repo private, or approve a history rewrite.

## 2. `ensure_browser` dropped every live browser (fixed)

- The new dead-port check calls `re.search`, but `dashboard.py` never imported `re`. The NameError was
  caught by `except Exception: await self.drop_browser()`. So every task with a local browser threw it away
  and reconnected, or launched a new Chrome.
- Fixed with `import re`. New test `test_live_browser_is_kept_between_tasks`: it failed before the fix (it
  tried to launch Chrome) and passes after. All 11 tests pass.
- Tip: `except Exception: pass/drop` hid two real bugs this week (this one and the ad blocker). Please log
  the exception, at least.

## 3. Timeouts 20 s × 3 tries → 60 s × 2 tries (left alone; needs numbers)

- Both effects are real. Large pages need more than 20 s; and in my measurement, 3 of 20 b.ai requests never
  returned. With 60 s per try, each of those hangs now costs 60 s instead of 20 s, on every page, small or
  large.
- The claims (1.2 s / 3.0 s averages, 30–45 s for 35k-token pages) have no runs attached. Please add a
  before/after on the same task (one small page, one LinkedIn results page). If 60 s wins, keep it.
- A cheaper route to the same goal is a smaller page state (fewer DOM tokens), rather than longer waits.

## 4. The "3-minute direct URL blueprint" (not implemented)

- Collecting 20–30 named recruiters across three companies into a table is profile scraping. LinkedIn's User
  Agreement §8.2 bans bots and scraping, and LinkedIn restricts accounts for it. The owner said earlier they
  don't want ToS-breaking scraping. This needs the owner's decision, not ours.
- What stays fine: the owner asks for one task at a time, at a human pace, and every Connect/Send still goes
  through the gate. The `turbo.py` compiler text now says "extract all candidate profile links … then visit
  them sequentially to execute actions like Connect". That is the bulk pattern again. Please reword it to
  "show results to the owner; take actions only on people the owner picks".
- **Facts to check before reuse:**
  - LinkedIn's help pages say people search supports quotes, parentheses, AND, OR and NOT. The "No
    results" probably came from requiring the literal keyword "Uber" plus a title, not from the syntax
    itself.
  - The blueprint's `keywords=Uber talent acquisition OR recruiter` has no company filter. It matches ex-Uber
    staff and anyone who mentions Uber. The company facet is the precise filter.
  - "No Experience-level filter on free People search" is correct.

## Kept as written

- Backtick stripping in `core.start_url` and `turbo.extract_target_url`.
- The dead-port check itself (now working).
- The in-memory extraction policy and `STARTF_USESHOWWINDOW`.
- The CDP over noVNC conclusion.
- The `qwen3.8-flash` default.
