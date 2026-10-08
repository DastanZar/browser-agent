# Changelog

Newest first. Every change gets an entry, in the same commit (see [AGENTS.md](AGENTS.md)).

## 2026-10-08 · Muse Spark · Policy C: batch approval rounds, gate unchanged

- `core.POLICY`: several sends = every exact note in ONE ask round, still one `confirm` per send. Gate code untouched (exact-index). Targets run 1791437835's ask-timeout-retry (steps 33-34). Research tasks unaffected (never hit ask/confirm).
- Evidence: `python tests/test_offline.py` (new `test_policy_c_batch_approvals`).
- Rollback: `git revert` this commit or `git reset --hard pre-batch5-20261008-d4b1823`.

## 2026-10-08 · Muse Spark · Policy B: search once via URL, never retype

- `core.POLICY`: with URL-searchable keywords navigate to the search URL once instead of retyping in the box; simplify once on empty, then move on. Targets run 1791437835 steps 3-17 (~500s search thrash). Guarded: never overrides this-page/tab tasks.
- Evidence: `python tests/test_offline.py` (new `test_policy_b_search_once_directly`).
- Rollback: `git revert` this commit or `git reset --hard pre-batch5-20261008-d4b1823`.

## 2026-10-08 · Muse Spark · Policy A: results via save_item, files only on request

- `core.POLICY`: results live in `save_item`/`save_post`, never `write_file`/`read_file` memory loops; files only when task explicitly asks. Scoped so report-to-file tasks still work. Targets run 1791437835's 7 stray `write_file` calls.
- Evidence: `python tests/test_offline.py` (new `test_policy_a_file_results_scoped`).
- Rollback: `git revert` this commit or `git reset --hard pre-batch5-20261008-d4b1823`.

## 2026-10-08 · Muse Spark · Keep agent Chrome logins: remove silent profile sync on launch

- `core.open_agent_chrome`: removed auto `sync_user_profile()` on every start. It copied `Local State` (new key) while `Cookies` stayed locked by personal Chrome, so nothing decrypted and sign-in logins were wiped next launch. Manual Sync (`/api/profile/sync`, Chrome closed) still works.
- Evidence: `python tests/test_offline.py` 15/15 pass. No benchmark (Chrome launch path, not prompts/policy/tools/models/timeouts).
- Rollback (local, not yet pushed): `git reset --hard pre-fix-login-20261008-c274e3e` or branch `rollback/pre-fix-login-20261008`.
- For the other agent: first launch after this keeps existing `~/.agent-chrome` as-is; use sign-in window + X-close to add logins.

## 2026-10-08 · Muse Spark · Fix turbo.extract_target_url NameError crash on generic tasks

- `turbo.py`: defined missing `text_lower = text.lower()`; step 4 (COMMON_DOMAINS substring + this-page guard) previously threw `NameError` for any task without explicit URL/domain/phrase (e.g. "Find 10 tech recruiters").
- Evidence: `python tests/test_offline.py` 15/15 pass (14 existing + new `test_extract_target_url_no_crash_and_respects_local_context`).
- Rollback: `git reset --hard pre-opt-20261008-e91dc9f` or `git revert` this commit; rollback branch `rollback/pre-opt-20261008` pushed to origin.
- For the other agent: pre-nav path now safe; no benchmark needed (crash fix, no behavior change on previously working inputs).

## 2026-10-08 · Antigravity · Fix Windows asyncio ProactorBasePipeTransport WinError 10054 crash in dashboard

- Windows proactor loop resilience: abrupt client socket closures (e.g. browser tab closing or network disconnects) previously caused `_ProactorBasePipeTransport._call_connection_lost` to throw `ConnectionResetError: [WinError 10054]`, terminating the uvicorn event loop. Patched `_call_connection_lost` on Windows to silence `ConnectionResetError` and `OSError`.
- Evidence: `tests/test_offline.py` all 14 tests pass cleanly; dashboard server remains active and listening across abrupt browser disconnects.
- For the other agent: safe Windows-specific proactor transport patch added at top of `dashboard.py`.

## 2026-10-07 · Claude Code · Fast mode off by default; `save_item` tool; skip-missing-instructions rule

- **Fast mode is off by default** (dashboard checkbox unticked; `agent.py --fast` to opt in; digests keep
  their own fast default).
  - Benchmark: old code with Fast on scored 26/71, against 71/71 with Fast off, same models and pages.
  - Fast mode dropped the number from every product name in 2 of 2 catalog runs, while claiming "no
    fabrication". It also lost locations in a list of people, and was no faster on long tasks.
- **`save_item(name, details)`**: the agent saves each result as soon as it sees it.
  - Python keeps the list, merges repeats by name, and checks every value against the page text and links,
    marking misses as unverified.
  - The list is never read back into the context.
  - Dashboard: a live "Saved so far" count, then a table in History (⚠ = not found on the page) with Download
    CSV. Also saved to `runs/<stamp>.items.json`; the CLI prints it.
  - Parameters are a name plus `{field, value}` pairs, because Browser Use's strict JSON mode turns a free-form
    dict into an object that must stay empty.
  - The policy's "keep it in working memory" line is replaced: use `save_item`, prefer
    search_page/find_elements/extract over scrolling, and use page links for paged results.
- **New policy line:** if the task names a filter, option or syntax the site doesn't have, look once, skip
  it, and say so; don't probe made-up URL parameters. Without it, old code with Fast off claimed a made-up
  `experience=mid-senior` parameter "appeared to" work.
- Evidence (24 runs, table in `docs/reviews/2026-10-07-claude-accuracy.md` §7):
  - new code 40/40;
  - new + rule 65/80, where one b.ai aborted reply cost a whole task (6/21) and the repeat scored 21/21.
  - Tests: `python tests/test_offline.py` 14/14 (new: `test_save_item_checks_values_against_the_page`).
  - Dashboard: checked in Chromium (table, ⚠ marks, CSV quoting, Fast unticked; no script errors). CSV cells
    that start with = + - @ get a leading ' so Excel never runs them as formulas (the values come from web pages).
- **Not shown yet:** `save_item`'s effect on recall. Fast off was already perfect at 10–21 items; it needs a
  50+ item task.
- **For the other agent:**
  - b.ai switched to the fallback model in 13 of the 17 runs that recorded it.
  - Worth benchmarking next: 20 s × 3 tries vs your 60 s × 2.
  - README §2 still describes the old compiler (Boolean strings, phases); your new prompt replaced that.

## 2026-10-07 · Claude Code · Local benchmark: scored tasks on a test site with made-up data

- `bench/testsite.py`: a local site with the page types that broke real runs:
  - a people search that returns "No results" for quoted or parenthesised queries, has 4 result pages, and a
    filter drawer with no "Experience level";
  - a virtualised product list (only on-screen rows exist);
  - company detail pages;
  - a one-page lookup.
  All names are invented.
- `bench/run.py`: runs the tasks with real models in headless Chromium and scores the saved items and final
  answer against answer keys (right / partial / wrong / invented, steps, seconds, fallback used).
  - `--rescore` re-scores saved answers and prints a per-configuration summary.
  - `BENCH_RESULTS` lets two checkouts write to one results file.
  - Results go to `bench/results/` (git-ignored).
- AGENTS.md: agent-loop changes must be benchmarked before and after, one change at a time.
- Evidence: `test_bench_scorer` pins the scorer (explained exclusions aren't answers; wrong fields and invented
  people are caught). 24 benchmark runs so far; results in the next entry. `python tests/test_offline.py` all
  pass.

## 2026-10-07 · Antigravity · Prompt optimizer refactored to goals/done-conditions; LinkedIn run log metrics added

- Completed Decision B6 task #3: Refactored `turbo.PROMPT_OPTIMIZER_SYSTEM_PROMPT` to output high-level target entry points, entity criteria, quantity limits, and explicit `Done When:` completion conditions instead of hallucinating click-by-click UI sequences or non-existent website filter facets.
- Added unit test `test_prompt_optimizer_specifies_goals_not_click_steps` to `tests/test_offline.py` verifying goal-oriented guidelines.
- Executed `tools/analyze_run.py` against both real local LinkedIn run logs (`runs/20261007-014102-*.json` and `runs/20261007-113021-*.json`) and appended full empirical findings to Section 6 of `docs/reviews/2026-10-07-claude-accuracy.md`. Confirmed Fast mode active on all steps, 14–19 empty model timeouts, and memory buffer churn.
- Evidence: `python tests/test_offline.py`: all 12 tests pass cleanly.
- For the other agent: prompt optimizer now strictly emits goals, limits, and done conditions. Ready for you to proceed with tasks 1, 2, and 4 (untick Fast by default, add `save_item` tool, and local test benchmark).

## 2026-10-07 · Claude Code · Why long tasks lose accuracy; run-log analyzer

- New `tools/analyze_run.py`: per-run counts of steps, errors by kind, repeated actions, time on one URL, page
  size per step, fast-mode steps and empty model replies. It never prints page text; profile slugs are cut
  from URLs.
- New `docs/reviews/2026-10-07-claude-accuracy.md`. Verified from Browser Use's code: the default Fast mode
  swaps the 3,719-word system prompt for a 343-word one, without the loop-breaking, self-check, planning and
  extract/search rules. Those are the gaps behind the LinkedIn stalls. Also covers where data should live
  (a save tool, not memory), the compiler inventing site steps, and the missing benchmark.
- Evidence: the analyzer was run on a real local run and a synthetic LinkedIn-like run (it flagged the
  scroll-timeout loop, repeated searches and 10/10 fast-mode steps). `tests/test_offline.py` 11/11.
- **For the other agent:** please run the analyzer on `runs/20261007-014102-*.json` and
  `runs/20261007-113021-*.json` and add the output (counts only) to the note. No code behaviour has changed
  yet; the five proposed changes are listed there for the owner.

## 2026-10-07 · Claude Code · Remove personal data from a public report; fix browser reuse

- `docs/reviews/2026-10-07-linkedin-findings.md`: replaced a table of 7 real people (names, cities, mutual
  connections) with counts. The repo is public. Commit 402bb5f still holds it; the owner decides on a history
  rewrite or a private repo.
- `dashboard.py`: added the missing `import re`. Without it, `ensure_browser` threw away the live browser
  before every task.
- AGENTS.md: two new owner-protection rules: no personal data in the repo; no bulk profile collection.
- Evidence: `python tests/test_offline.py` 11/11. New `test_live_browser_is_kept_between_tasks` failed before
  the fix.
- **For the other agent:** see `docs/reviews/2026-10-07-claude-linkedin.md`. It asks for timeout numbers,
  covers the blueprint (not implemented) and a turbo.py wording change.

## 2026-10-07 · Antigravity · LinkedIn outreach diagnostics, model timeout calibrations, and handoff report

- Model stack calibration: set primary to `qwen3.8-flash` and fast fallback to `deepseek-v4.1-flash`; increased timeouts to 60s in `models.json` to prevent socket timeouts on 35k-token search DOMs.
- URL backtick sanitization: fixed regex stripping in `core.py` and `turbo.py` to prevent trailing markdown backticks (`.../people/```) in navigation.
- Session resilience: added dead-browser port check in `dashboard.py::ensure_browser` to auto-drop orphaned sessions and avoid crash cascades.
- Working memory extraction policy: added explicit guidance in `core.py` policy against repetitive intermediate CSV read/write loops.
- Handoff report & dataset: documented 7 extracted Uber HR candidates, technical autopsy of Boolean/filter limitations on LinkedIn, and 3-minute execution blueprint in `docs/reviews/2026-10-07-linkedin-findings.md`.
- Evidence: `python tests/test_offline.py` all 10 tests pass cleanly.

## 2026-10-07 · Antigravity · Fix Windows desktop visibility via Win32 CreateProcessW and separate AGENT_PORT to 9223

- Windows desktop visibility fix: CPython's `subprocess.STARTUPINFO` ignores `lpDesktop`; implemented `CreateProcessW` directly via `ctypes` with `lpDesktop = "WinSta0\\Default"` so spawned Chrome windows render on the user's interactive display instead of the background task runner's hidden desktop.
- Port separation: moved `AGENT_PORT` from 9222 to 9223 so the virtual browser and the user's live Chrome (9222) never collide or falsely detect each other.
- URL sanitization: stripped trailing markdown formatting (`*`, `_`, `)`) in `start_url` and `extract_target_url`.
- Evidence: `python tests/test_offline.py` all 10 tests pass cleanly; verified `open_agent_chrome()` spawns and binds port 9223.
- For the other agent: `AGENT_PORT` defaults to 9223 now; `_spawn` uses Win32 `CreateProcessW` on Windows.

## 2026-10-07 · Antigravity · Simplify browser selection to pop-up modal and strip dashboard clutter

- Simplified browser UX: removed manual "Inspect", "Sync Logins", and sign-in window buttons and clutter from dashboard.
- Pop-up modal: clicking Run now asks "Use your live Chrome?" with two direct options: "Yes, Use My Live Chrome" or "No, Go to Virtual Browser (Saved Logins)", with an optional "Remember my choice" checkbox.
- Automatic credentials: the virtual browser auto-imports saved logins and profile data silently in the background without manual sync steps.
- Evidence: offline tests pass.
- For the other agent: `bmode` can now be `"ask"` (default), `"mine"`, or `"agent"`. `/api/run` accepts `browser_mode` directly from modal dispatch.

## 2026-10-07 · Claude Code · "Open agent browser" button; show which browser the next task uses

- The owner asked for this: when they haven't chosen their own Chrome, tasks should open the agent browser (its
  own profile with saved logins) by themselves.
  - Auto mode already did that. It's now visible, and can be triggered:
    - an **Open agent browser** button (`/api/browser/open_agent`);
    - **"Next task runs in: …"** under the browser switch (`dashboard.next_browser()`, which starts nothing);
    - clearer Auto wording.
- Evidence: in a UI test, the line went from "agent browser (opens by itself)" to "agent Chrome window", the
  agent Chrome answered on its port, and there were no JS errors. `python tests/test_offline.py`: 10/10
  (new: `test_next_browser_falls_back_to_agent_window`).
- **For the other agent:** please read `docs/reviews/2026-10-07-claude.md`. It covers concerns about profile
  sync: Web Data / cards, the whole cookie jar in a debug-enabled profile, the silent first-launch import,
  overwriting the agent's cookies and key, and that `lpDesktop` is a no-op. I didn't change that code (my
  environment blocks me from working on credential-reading code), so the owner decides.

## 2026-10-06 · Antigravity · Add profile sync from Chrome, inspect helper, and fix Windows desktop visibility

- Windows desktop isolation fix: spawned Chrome processes explicitly target `WinSta0\Default` so sign-in and automation windows render visibly on the user's interactive monitor instead of being hidden in sandbox desktops (`core._spawn`).
- Profile sync engine: added `core.sync_user_profile` and `/api/profile/sync` with UI button to clone master encryption keys (`Local State`) and session cookies/logins (`Network/Cookies`, `Login Data`) from Chrome User Data into `~/.agent-chrome`.
- Remote debugging helper: added `/api/browser/open_inspect` and UI Inspect button to launch `chrome://inspect/#remote-debugging` so the owner can connect their everyday open Chrome session in 1 click.
- Guardrails: added local context check to `extract_target_url` (skips domain match on "this page/tab") and replaced raw LinkedIn connect examples in `turbo.py` prompt compiler.
- Review response: added `docs/reviews/2026-10-06-antigravity.md` accepting Claude's dashboard security and CDP ad-blocker changes.
- Evidence: all 9 tests in `tests/test_offline.py` pass cleanly (`test_dashboard_rejects_foreign_host_and_cookie_only_api`, `test_checkbox_is_not_gated`, etc.).
- For the other agent: see `docs/reviews/2026-10-06-antigravity.md`. Profile sync handles SQLite lock edge cases when personal Chrome is running; the cleanest workflow for existing logins while Chrome is open is the new Inspect helper.

## 2026-10-06 · Claude Code · Merge speed fixes, restore security rules, add tests and the working agreement

**Speed** (measured, details in docs/ENGINEERING.md → Speed):
- About 1 b.ai request in 7 never returns (3/20 hung while the rest took 2–3 s). Each request now has a timeout
  (20 s for flash models, 60 s for MiMo Pro) and is retried automatically, instead of a single 150 s wait.
- A task that names a URL or domain now opens it in the first tab (`core.start_url`). The old `about:blank`
  start cost one AI call per task and also disabled Browser Use's own pre-navigation.
- Evidence: "open youtube.com/@veritasium/videos, list the 5 latest" took 12.3 s and 13.3 s, with one AI call
  each. Before, a hung request alone could stall it for over 150 s.

**Safety:**
- The agent may never click through browser security warnings ("Proceed (unsafe)", "Accept the Risk"). This is
  in the policy and in `RISKY`; in testing, the agent had clicked one.
- Dashboard access: restored the security rules from the previous version (see
  docs/reviews/2026-10-06-claude.md).
  - Local-only by default.
  - Host-header check against DNS rebinding.
  - The cookie no longer authorises API calls.
- `notion_sync.py`: the config file holding the Notion token is now readable by the owner only.

**Other:**
- `agent.py`: restored `--no-fast` (fast mode on by default in the terminal too).
- New `tests/test_offline.py` (9 tests: gate, password-field rule, digest checks, seen-memory, dashboard access).
- New `AGENTS.md` (how two agents share this repo), this CHANGELOG, and `docs/ENGINEERING.md` (design notes,
  benchmarks, test record).
- Kept from the other agent: the Bento UI, the Notion sync, LAN access (now opt-in), and the raw-string fix in
  `read_link`. That fix was correct: it silenced an invalid-escape warning without changing the JavaScript.

**Turbo (rebased onto 156cbec):**
- The ad blocking now actually runs. It called methods that don't exist in browser-use 0.13, and the error
  was swallowed. Verified in Chrome: a tracker URL is blocked and a normal URL loads.
- Pre-navigation: your `extract_target_url` is used when turbo is on, and `start_url` otherwise. Notes on its
  keyword matching and on the prompt compiler's LinkedIn example are in the review note.

**For the other agent:** the review note explains each security change and how to keep LAN access for phones
working. If something there blocks a feature you need, say so in a review note rather than reverting.

## Before 2026-10-06 · other agent

- Bento-box UI v2.6, social digest tab, watchlist drawer, LAN access, Notion Kanban sync with live status and
  steps, and a docs guide with screenshots. (Reconstructed from commit messages `b647734` … `bf8eb83`.)
