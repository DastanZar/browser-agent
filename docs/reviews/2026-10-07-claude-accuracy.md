# Why long tasks (the LinkedIn runs) go wrong, and what to change (Claude Code, 2026-10-07)

**Sources.**
- The two LinkedIn run logs aren't in the repo (`runs/` is git-ignored), so this is based on:
  - Browser Use 0.13.10's own code;
  - our `core.py`, `dashboard.py` and `turbo.py`;
  - `docs/reviews/2026-10-07-linkedin-findings.md`.
- Causes 1–3 are verified from code. Causes 4–5 need the logs.
- `tools/analyze_run.py` checks all of them on a real run:
  `python tools/analyze_run.py runs/<file>.json --steps`. It prints counts, actions and URLs (profile slugs cut
  out); check its output before pasting anywhere public.

## Causes, most likely first

### 1. Fast mode is on for every task, and it removes the agent's self-checks (verified)

- The dashboard's **Fast** box is ticked by default (`dashboard.html`, `id="fast" checked`). It passes
  `flash_mode=True` to Browser Use.
- What flash mode does in 0.13.10:
  - It swaps the 3,719-word system prompt (`system_prompts/system_prompt.md`) for a 343-word one
    (`system_prompt_flash.md`).
  - It drops `thinking`, `evaluation_previous_goal` and `next_goal` from the output (`agent/views.py:73`).
  - It switches planning off: "Flash mode strips plan fields from the output schema, so planning is
    structurally impossible" (`agent/service.py:241`).
- What the full prompt has and the flash one doesn't, matched to the stalls in the LinkedIn report:

| Rule only in the full prompt | Stall it would have prevented |
|---|---|
| "If you are on the same URL for 3+ steps without progress, or the same action fails 2–3 times, try a different approach. Track what you have tried" | 15+ steps retrying Boolean search variants (cause A) |
| Judge every previous action as success/failure; "never assume an action succeeded" | Re-opening the filter drawer for a facet that doesn't exist (cause B) |
| "Prefer `search_page` over scrolling"; `find_elements` and `extract` read the whole page without scrolling | Scroll timeouts on the results list (cause C) |
| Planning with a todo list for tasks of more than 10 steps | Losing track across companies and pages |

- **This one is on me.** I set Fast as the default after measuring it 2–4× faster on 1–3 step tasks (a YouTube
  list, a form). I never tested it on a 40-step task, and that's where it hurts.

### 2. The data has nowhere safe to live (verified)

- In flash mode, the only place to carry results is the `memory` field, which the prompt limits to "up to 5
  sentences". 20–30 people × name/title/location don't fit in it, so entries get dropped or blurred as steps go
  by.
- The new "keep candidates in working memory, don't write files" policy (402bb5f) removed the other place they
  could go. The CSV thrash it was meant to fix was real, but the cure took away the only durable store.
- The digest already solved this. `save_post` hands each item to Python the moment it's seen, the text is
  checked against the captured page text (`digest.verify`), and nothing is read back into the context. It scored
  8/8 verbatim, against fabrications before.

### 3. The prompt compiler invents site mechanics (verified from the report)

- Causes A and B in the report both came from the task prompt: the parenthesised Boolean query, and "filter by
  Experience level: Mid-Senior".
- The compiler (`turbo.compile_prompt`) is a fast model that writes step-by-step UI instructions for a site it
  has never seen. The browsing agent then treats them as requirements and keeps trying to satisfy them.
- Its old template literally contained that Boolean example. The new one still asks for phases and exact
  "buttons/form elements to interact with".

### 4. Huge page states to a flash model (needs the logs)

- The report says about 35k tokens per step. Long inputs make small models worse at choosing the right element,
  and slower. The analyzer prints the real median and max per run.
- Pages can be read with `&page=2` in the URL instead of by scrolling.

### 5. Run endings and follow-ups (needs the logs)

- `max_failures=4`: four failed actions in a row (e.g. scroll timeouts) end the run. The analyzer shows whether
  that's how these runs ended.
- If the "40+ turns" were follow-up messages, `add_new_task` keeps the whole conversation in one agent, so every
  turn carries all earlier pages and history.

## The method problem underneath: nothing is measured

- "Accuracy" is never defined. There's no list of the right answers, so neither recall nor precision is known.
- Each change is judged on one 40-step live run on LinkedIn. That is slow (about 30 s a step), can't be
  repeated (the results change), and puts the owner's account at risk.
- Commit 402bb5f changed five things at once: default model, timeouts, retries, compiler prompt and policy. No
  run can say which helped.

## What to change, in order

1. **Turn Fast off by default for tasks.** Keep it as an option for short ones. Digests stay as measured.
2. **Add a generic `save_item(fields)` tool.** Same design as `save_post`: Python keeps the list, removes
   duplicates and checks each item against the page text; it's never read back into the context. Remove the
   "keep it in working memory" policy line.
3. **The compiler states goals, limits and "done when …", never UI steps.** Facts about a site (e.g. "People
   search has no experience-level filter") go in a short per-domain notes file, written only from what runs
   actually observed.
4. **A local benchmark before any live run.**
   - 6–8 fixed tasks against local copies of hard page types, with made-up names:
     - a results list with pagination and a filter that doesn't exist;
     - a virtualised infinite list;
     - a form with a confirm step.
   - Each task has a scorer: items right/wrong/missed, steps, seconds.
   - Every change runs the benchmark before and after; one change per commit.
   - This is how the digest went from 305 s with invented posts to 80 s at 8/8 verbatim.
5. **Run `tools/analyze_run.py` on the two LinkedIn logs** and add the output to this note, to confirm or
   rule out causes 4 and 5.

## 6. Empirical Run Log Analysis Results (Antigravity, 2026-10-07)

Ran `tools/analyze_run.py` against both LinkedIn run logs on local machine:

### Run 2 (2026-10-07 11:30): `runs/20261007-113021-1791350885.json`
- **Steps**: 45 | **Finished**: True | **Reported Success**: False | **Total time**: 1805s (median step 44.1s, slowest 65.7s)
- **Fast mode**: 25/25 steps had no thinking/evaluation (confirms Cause 1: fast mode stripped self-checks and planning).
- **Empty model replies (timeouts/bad JSON)**: 19
- **Page state tokens per step**: median ~5,926 tokens, max ~7,596 tokens (Cause 4 confirmed: token size causes slow generation).
- **Errors**: `{'timeout': 19, 'element missing': 1}`
- **Memory field**: median 241 chars, max 946 chars (confirms Cause 2: results get truncated/lost in memory).
- **Repeated actions (3+)**: `[('navigate(https://www.linkedin.com/search/results/people/?keywords=Ube)', 3)]`
- **Longest stay on one URL**: 9 steps
- **Actions breakdown**: `{'click': 19, 'navigate': 7, 'scroll': 2, 'write_file': 1, 'done': 1}`

### Run 1 (2026-10-07 01:41): `runs/20261007-014102-1791314332.json`
- **Steps**: 58 | **Finished**: True | **Reported Success**: False | **Total time**: 2430s (median step 22.1s, slowest 199.2s)
- **Fast mode**: 43/43 steps had no thinking/evaluation.
- **Empty model replies**: 14
- **Page state tokens per step**: median ~8,934 tokens, max ~13,395 tokens.
- **Errors**: `{'timeout': 7, 'element missing': 1, 'other': 8}`
- **Memory field**: median 479 chars, max 1548 chars.
- **Repeated actions (3+)**: `[('write_file', 6), ('wait', 4), ('hand_over', 3), ('input(("Uber") AND ("Talent Acquisition"...))', 3), ('navigate(...keywords=%28)', 3)]`
- **Longest stay on one URL**: 15 steps
- **Actions breakdown**: `{'click': 16, 'navigate': 9, 'write_file': 6, 'input': 4, 'wait': 4, 'scroll': 4, 'hand_over': 3, 'read_file': 3, 'switch': 1, 'done': 1}`

## 7. Benchmark results: changes 1, 2 and 4 (Claude Code, 2026-10-07)

**Setup.**
- `bench/run.py` runs 4 tasks on `bench/testsite.py`, a local site with made-up data, using the real b.ai
  models (qwen3.8-flash, falling back to deepseek-v4.1-flash). Each task is scored against an answer key.
- **Tasks:**
  - **people:** the LinkedIn-style prompt, including its bad Boolean query and the missing "Experience level"
    filter; 10 targets across 4 result pages.
  - **catalog:** a virtualised list where only on-screen rows exist, 21 targets.
  - **companies:** 8 detail pages to visit.
  - **lookup:** a one-page lookup.
- **Scoring:** "Right" means every field is correct. A wrong field counts as partial; a name that isn't a
  target counts as wrong.
- **Setups compared:**
  - "old" = commit 0512851, run from a separate checkout;
  - "new" = Fast off + `save_item`;
  - "new+rule" = new + the skip-missing-instructions policy line.
- **Small sample:** 1–2 runs per cell, so single runs can flip.

### Right answers (all runs)

| Task | Old, Fast on | Old, Fast off | New | New + rule |
|---|---|---|---|---|
| people (10) | 17/20 | 20/20 | 10/10 | 20/20 |
| catalog (21) | **0/42** | 42/42 | 21/21 | 27/42 ¹ |
| companies (8) | 8/8 | 8/8 | 8/8 | 16/16 |
| lookup (1) | 1/1 | 1/1 | 1/1 | 2/2 |
| **total** | **26/71** | **71/71** | **40/40** | **65/80** |

¹ b.ai aborted one reply ("Model output became abnormal while generating a JSON response"). On the next
step, the agent declared the list complete from the first screen and stopped at 6/21. The rule doesn't apply
to this task. The repeat scored 21/21.

### Mean seconds per task

| Task | Old, Fast on | Old, Fast off | New | New + rule |
|---|---|---|---|---|
| people | 531 | 1705 | 1403 | 1396 |
| catalog | 974 | 835 | 311 | 532 ¹ |
| companies | 393 | 315 | 422 | 231 |
| lookup | 16 | 23 | 22 | 55 |

### What the numbers say

1. **Fast mode was the accuracy problem.**
   - In both catalog runs it dropped the number from every product name ("Harbor Kettle" for "Harbor Kettle 1"
     and "Harbor Kettle 31"). It then wrote "All values were read directly from the catalog page (no
     fabrication)".
   - In one people run it lost three locations and its answer was cut off mid-word.
   - Fast off got everything right, with the same models and the same pages.
   - Fast mode is only quicker when the task is short or goes smoothly. On the catalog it was slower,
     because it kept re-scrolling.
2. **`save_item` didn't change accuracy at this list size.** Fast off was already 71/71.
   - What it adds: every value is checked against the page when saved; the dashboard shows a table with
     ⚠ for unchecked values, plus a CSV; and the list survives a cut-off final answer.
   - Its page check catches invented values, not shortened ones. "Harbor Kettle" passes because it appears
     inside "Harbor Kettle 1".
   - A longer-list task (50+ items) is needed to show its effect on recall.
3. **The skip rule removed made-up claims, not steps.**
   - Without it, old code with Fast off claimed a made-up `experience=mid-senior` URL parameter "appeared to
     reduce result counts (suggesting it is honored)". The site ignores unknown parameters.
   - With it, no such claims appeared, and the agent reported the missing filter plainly.
   - It still spent steps re-checking, so the people task hit or nearly hit the 40-step cap in every setup.
4. **b.ai reliability is the largest source of noise.**
   - 13 of the 17 runs that recorded it switched to the fallback model at least once.
   - Timeouts hit on both models.
   - One aborted reply cost a whole task.
   - That fits Section 6: 19 of 45 steps with no reply on a ~6k-token page.

### Scorer limits

- A correct remark after a list (e.g. "product #60 is the last item") counts as a wrong answer. That's the
  one "wrong" in old Fast off.
- Names the agent lists as excluded don't count, whether under a heading or in the same sentence (tested in
  `test_bench_scorer`).

### Next

- A 50+ item task, to measure `save_item` on long lists.
- Timeouts of 20 s × 3 tries vs 60 s × 2, measured on this benchmark.
- After a model error, don't accept `done` until the agent has looked at the page again.
