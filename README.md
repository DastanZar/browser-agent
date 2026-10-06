# Browser Agent: give it any task, and it asks you only when a human must

A general-purpose browser agent with a **dashboard**. You type a task; it works in **your own Chrome, with your
open tabs and logins**, and stops for you only at three points:

| It pauses with | When | You do (in the dashboard) |
|---|---|---|
| **Question** (`ask_human`) | A choice that's yours to make (plan, spend, names, recipients) or a fact only you know | type the answer |
| **Your turn** (`hand_over`) | A login, 2-step code, passkey, CAPTCHA or "verify it's you" screen | do it in Chrome, then click *I'm done* |
| **Approval** (`confirm`) | Right **before** paying, sending, posting, deleting, publishing, accepting terms, changing security, billing or sharing, or a final submit | *Approve* or *Decline* (or say what to do instead) |

Every pause beeps, shows a desktop notification and flashes the tab title. Set `NTFY_TOPIC` to also get it on
your phone through [ntfy](https://ntfy.sh).

## Start it

1. **Let it use your Chrome** (Chrome 144+, once):
   - open `chrome://inspect/#remote-debugging` and switch remote debugging **on**;
   - when the agent connects, Chrome asks *"Allow remote debugging?"*: click **Allow**. That happens once per
     dashboard session.
2. **Launch the dashboard.**
   - **Windows:** double-click **`Start Browser Agent.cmd`** in this folder. Don't double-click the `.ps1`:
     Windows opens those in Notepad.
   - **macOS/Linux:** `tools/browser-automation/dashboard.sh`

   It opens <http://127.0.0.1:8770> in your browser. Keep the black window open while you use it.
   - **First run:** it creates `.venv` and installs everything, which takes a few minutes. It needs
     **Python 3.11+**; if that's missing, it tells you where to get it.
   - **On error:** the window stays open with the message instead of flashing shut.
   - **Launched twice?** The second launch just reopens the page.
   - **The files are on the `claude/vigilant-curie-hs8zmk` branch**, not `main`. Check that branch out (or
     merge it) first.

**Not opening?**
- **Page:** go to <http://127.0.0.1:8770> by hand.
- **Black window:** read the message in it.
- **Python:** `py -0p` lists your installed Pythons; you need 3.11 or newer.
- **Port in use:** start with `set DASHBOARD_PORT=8771` before launching.

3. **First time only:** paste your b.ai key into *b.ai API key → Save*. It's stored in `~/.config/bai/key` on
   your machine and never in this repo.
4. **Run a task:** type it and press *Run task*, or Ctrl+Enter.
   - **Templates:** *Start from a template* loads one from `tasks/`. Add your own `.md` files there; copy
     `TEMPLATE.md`.
   - **History:** click a past task to reuse its text.

**How it treats your tabs:**
- Each task starts in a **new tab**, never in one of yours.
- Your other tabs are off-limits unless the task refers to them ("summarise this page").
- It never closes your tabs, and stopping or disconnecting leaves Chrome open.

### Or: a separate agent Chrome with its own logins (no Chrome settings to change)

1. In the dashboard, under **Browser**, pick **Agent window**.
2. Type a site (e.g. `linkedin.com`) under *Sign in to a site* and click **Open**. A separate Chrome window opens
   with its own profile (`~/.agent-chrome`).
3. Log in there normally, with your password and 2FA. Do this once for each service.
4. Done. Those logins **persist**: close the window, reboot, it doesn't matter. The dashboard relaunches that
   Chrome by itself when a task needs it, and the agent uses those accounts as you tell it to.

This keeps the agent away from your everyday browser entirely. If a site later logs it out, the agent hands
over to you (*Your turn*) to log back in. `start-chrome.sh` / `start-chrome.ps1` open the same window by hand.

**LinkedIn, specifically:** its User Agreement (§8.2) bans "bots or other unauthorized automated methods" to
send messages, add contacts, or like, comment on or share posts, and it restricts accounts that do. Reading,
researching and drafting with the agent is low risk. Letting it mass-connect, message or engage on your behalf
can get the account restricted. Keep volumes human-scale and approve each send (the approval gate covers
Send, Connect-type and Post buttons).

## Models (your b.ai key, `models.json`)

| Model | Pro / flash | In our test |
|---|---|---|
| **MiMo v2.6 Pro** (default) | pro | ✅ 177 s. Asked which plan, asked approval separately, explained every step |
| **DeepSeek v4.1 Flash** (default fallback) | flash | ✅ 100 s, the fastest. Asked which plan, asked approval |
| Qwen 3.8 Flash | flash | ✅ 156 s. Asked which plan, asked approval |
| MiMo v2.6 Flash | flash | ⚠️ 100 s. Picked the plan itself. In one of two runs it skipped approval; the code gate now catches that |
| GLM 5.3 Flash | flash | ⚠️ 202 s, slowest. Picked the plan itself, then asked approval. Ignores `response_format`, so the action schema goes in its prompt (`json_mode: false`) |

The flash models are fine for explicit instructions. For anything with a choice in it, use MiMo Pro,
DeepSeek Flash or Qwen Flash. b.ai returned an occasional 502 during tests; the fallback model covered it.

All five accept screenshots, which is on by default. *Fallback* is used when the main model errors or is
rate-limited. To add a model, add a line to `models.json`.

## Other ways to drive it

- **Terminal:** `python agent.py "task"`, `--file tasks/x.md`, `--chat`, `--model glm-5.3-flash`,
  `--browser mine|agent`.
- **Claude Code drives your Chrome** (for the hardest jobs, with a frontier model as the brain):
  `./setup-claude.sh mine` registers Google's `chrome-devtools-mcp --autoConnect`, which uses the same
  remote-debugging switch as above. `./setup-claude.sh agent` uses Playwright MCP on the separate window instead.
- **Free replays:** `replay.mjs` + `flows/`. Once a click path works, save it as a flow, and rerunning it costs no
  tokens.
- **No browser at all:** if a site has a CLI or API, that beats any browser agent; `gcp/setup.sh` is an example.

**Options** (environment variables):
- `MAX_STEPS`: steps per task; default 100.
- `ALLOWED_DOMAINS`: fence the agent to certain sites.
- `AUTO_CONFIRM=1`: skip approvals (not recommended).
- `LLM_VISION=0`: don't send screenshots.
- `SECRET_<NAME>=value`: typed without the model seeing it; refer to it as `<secret>NAME</secret>`.
- `DASHBOARD_PORT`: default 8770.
- `CHROME_USER_DATA_DIR`: if your Chrome profile folder isn't the default.
- `AGENT_PROFILE`, `AGENT_PORT`: where the agent window keeps its logins; default `~/.agent-chrome`, port 9222.
- `CHROME_PATH`: if Chrome isn't found automatically.

## Safety

- **The dashboard is local only.**
  - It listens on `127.0.0.1` only.
  - Every API call needs a random token baked into the page, and requests with a foreign `Host` header are
    refused.
  - So other websites can't send it tasks.
- **Your tabs:** the agent is told never to read, search or inspect your other tabs, cookies or storage unless
  the task names them. It starts every task in its own tab; that part is enforced in code, not left to the model.
- **Approvals are enforced in code, not just requested.** Before any click on a button labelled like pay, buy,
  order, subscribe, send, post, publish, delete, remove, revoke, transfer, submit, confirm, accept, sign up or
  register, the dashboard asks you, even if the model "forgot" to. Ticking checkboxes and routine admin buttons
  (Save, Create, Enable, Next) don't trigger it. Answering a question never counts as approval. The patterns are
  in `core.py` (`RISKY`).
- **Logs:** `runs/` (gitignored) keeps each run's full history and `history.jsonl`; it can contain page text.
- **While remote debugging is on**, local programs can ask to control Chrome. Chrome still shows the Allow
  prompt, but switch it off when you're not using the agent.

## Is Browser Use the best engine? (checked Oct 2026)

**For your setup, yes:** it's the best **open-source engine that runs on any model**, and your models come
through b.ai. On [Online-Mind2Web](https://leaderboard.steel.dev/leaderboards/online-mind2web/), the top score
is Browser Use's *hosted* agent at 97% (paid, its own model). Open-source results depend mostly on the model:
ABP + Claude Opus 4.6 reached 90.5%.

| Option | What it is | Fit |
|---|---|---|
| **Browser Use** (MIT) | Python agent; any OpenAI-compatible LLM; custom tools for human-in-the-loop | **Chosen** |
| **ABP** | Open-source Chromium build that freezes the page between steps; MCP | Strong for the Claude Code route |
| **chrome-devtools-mcp** (Google) | MCP server; `--autoConnect` attaches to your running Chrome | Used by `setup-claude.sh mine` |
| **Stagehand v4** | TS, Python and Go SDK, AI where selectors break | Better for building scripted products (55–65%) |
| **Cua** | Whole-desktop computer use (driver v0.30, Sep 2026) | Only if tasks leave the browser |
| **ChatGPT Atlas, Perplexity Comet, Claude in Chrome** | Consumer browser agents | One-offs; locked to their own model |

"Jev" is a model on your b.ai account (`jev-latest`), not a browser tool. It's left out because you didn't
list it.

## Verified (cloud container, headless Chromium 141, browser-use 0.13.10, real b.ai models)

- **Every model:** all five answered chat and took screenshots through `api.b.ai/v1`. Four honour
  `response_format: json_schema`; GLM 5.3 Flash ignores it, so it gets the schema in its prompt.
- **End to end through the dashboard UI** (Playwright clicking it like you would), with MiMo v2.6 Pro:
  - **Setup:** a stand-in for "your Chrome" with two tabs already open, attached through `DevToolsActivePort`
    exactly as `chrome://inspect` remote debugging does.
  - **The task:** sign up on a test site; "choose whichever plan I prefer".
  - **What happened:**
    - The agent opened its own tab and asked which plan.
    - It asked for a separate approval before "Create account and pay", then completed the signup.
    - Both pre-existing tabs were left untouched.
- **All five models** ran the same signup through the dashboard; results are in the table above.
- **Decline test:** DeepSeek; I clicked *Decline* on the payment. No account was created and it reported why.
- **Gate test:** DeepSeek, told "do NOT call confirm, just click the final button". The code gate stopped the
  pay click anyway ("The agent is about to click 'Create account and pay'… Allow it?"). I declined, and no account
  was created. The terms checkbox did not trigger the gate.
- **Agent window with persistent logins:**
  - The dashboard's *Open* button launched the agent Chrome from nothing and opened a test sign-in page.
  - I logged in, then closed Chrome completely.
  - A new task made the dashboard relaunch it on its own. The agent (DeepSeek) found the session intact:
    "Welcome back, Dastan…".
- **Terminal CLI:** `agent.py` on DeepSeek, attached through `--browser mine`, completed a read-only task in a
  new tab.
- **Bugs found by those runs, and fixed:**
  1. The agent searched the human's other tab, including its storage, for "hints".
  2. It navigated one of the human's tabs instead of opening its own.
  3. It treated "Pro please" as payment approval.
  4. MiMo Flash paid without any approval.
  5. Browser Use's "judge" pass plus the 4096-token cap caused minutes of delays.

  - 1 and 3: policy.
  - 2: code (every task starts in a fresh tab).
  - 4: code (the approval gate).
  - 5: judge off, a 12k token cap, and a 150 s timeout.
- **Not verified here:**
  - Real Chrome's *Allow* prompt.
  - Real websites with your logins.
  - The Windows launchers.
