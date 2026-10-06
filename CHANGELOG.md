# Changelog

Newest first. Every change gets an entry, in the same commit (see [AGENTS.md](AGENTS.md)).

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
