# Autonomous Browser Agent Suite (Port 8770)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![UI: Bento Box SaaS](https://img.shields.io/badge/UI-Bento%20Box%20SaaS-indigo.svg)](#operations-console-preview)
[![Engine: CDP + Browser-Use](https://img.shields.io/badge/Engine-CDP%20%2B%20Browser--Use-orange.svg)](#key-capabilities)

An autonomous web operations suite with a developer-grade Bento Box console. Execute complex browser tasks across authenticated web applications (LinkedIn, Google Cloud, AWS, GitHub), maintain persistent sessions safely, monitor feeds with zero-hallucination verification, and access your agent securely from your workstation, LAN, or mobile device.

---

## Operations Console Preview

### 1. Command Dispatcher & Active Pipeline
The primary interface provides 1-click starter prompts, workflow templates, inference model controls, multi-turn conversational thread memory, real-time live execution telemetry, and persistent profile authentication.

![Operations Console Overview](docs/images/dashboard-overview.png)

### 2. Personal Intelligence Digest & Watchlist
Read-only, human-paced feed scrolling with DeepSeek v4.1 Flash. The agent deduplicates posts via SHA-1 fingerprints, reads post links in background CDP tabs, and verifies extracted summaries against verbatim DOM body text.

![Personal Intelligence Digest](docs/images/social-digest.png)

### 3. Execution History & Interactive Summaries
Persistent turn history with real-time state caching, status chips (`DONE`, `FAILED`, `STOPPED`), timing metrics, and non-jittery expandable execution summaries.

![Execution History and Summary](docs/images/execution-summary.png)

---

## Key Capabilities

### ⚡ 1. High-Speed Fast Mode (`flash_mode=True`)
- Suppresses verbose model reasoning monologues. The agent communicates concise, high-velocity tool actions directly over CDP.
- **Empirical Execution Benchmarks**:
  - Open Wikipedia & extract featured article: **31.7s** (down from 170s+ in normal mode).
  - Multi-turn follow-up queries: **32.9s**.

### 🧠 2. Multi-Turn Follow-Up Memory
- Preserves context, active tabs, and conversational thread history across multiple instructions.
- Execute chains like:
  1. *"Search LinkedIn for AI agent engineering updates."*
  2. *"Now summarize the third result and extract author details."*
- Click **New Conversation →** at any time to cleanly reset context and start fresh in a new tab.

### 🛡️ 3. Safe Persistent Authentication
- **Bot Detection Bypass**: `open_signin_window` launches a plain, non-automated Chrome profile in `~/.agent-chrome` without `--remote-debugging-port`. Sites like Google, LinkedIn, and banking portals allow standard login without bot-blocking flags.
- **Cookie Flush Guarantee**: The dashboard never force-terminates the sign-in window. Users log in, tick "keep me signed in", and close via the window's standard **✕** button, allowing Chrome to cleanly flush session cookies to disk without write race conditions.
- **Automation Interlock**: If a task starts while the sign-in window is open, the agent pauses with a handover prompt and waits for the user to close it.

### 📰 4. Social Digest & Anti-Hallucination Engine
- **Human-Paced Scrolling**: `scroll_feed` scrolls incrementally, pauses up to 2.4s for lazy-loaded feeds, and senses the true bottom of the page.
- **Background Link Reading**: `read_link` opens outbound links in an isolated CDP tab, captures the article title and text, closes the tab, and preserves the timeline scroll position without reload.
- **Anti-Hallucination Verification**: `verify(post, page_text)` compares model-reported quotes and named entities against raw `document.body.innerText` captured directly over CDP. Hallucinations are either replaced with verbatim text or flagged with red `unverified` pills.
- **Zero-Duplicate Fingerprinting**: Deduplicates posts using SHA-1 text n-grams and sanitized canonical URLs in `seen.json`.
- **Read-Only Safety Gate**: `Gate(read_only=True)` blocks all likes, comments, follows, connects, or posts during digests.

### 🌐 5. Home LAN & Mobile Access
- Binds to `0.0.0.0:8770` by default.
- **Zero-Config Localhost**: Workstation browser connects directly without security prompts.
- **Secure Network Access**: Connections across LAN / WiFi / Tailscale require the per-session token (`?token=...`). Unauthorized probes receive **HTTP 403 Forbidden**.
- Direct LAN URL with security token is printed in the terminal on startup.

---

## Quick Start

### Windows (Recommended)
Double-click **`Start Browser Agent.cmd`** or run from PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File dashboard.ps1
```
*First run creates `.venv` and installs all dependencies automatically.*

### macOS / Linux
```bash
./dashboard.sh
```

### Direct Python Execution
```bash
# 1. Activate virtual environment
.\.venv\Scripts\Activate.ps1   # Windows
source .venv/bin/activate       # macOS/Linux

# 2. Launch dashboard
python dashboard.py
```

Console URL:
- Local Access: http://127.0.0.1:8770/
- LAN Access: `http://<YOUR_LAN_IP>:8770/?token=<TOKEN>`

---

## Inference Models (`models.json`)

| Model | Mode | Vision | Optimal Use Case |
| :--- | :---: | :---: | :--- |
| **MiMo v2.6 Pro** *(Default)* | Pro | Enabled | Complex workflows, multi-step planning, high-accuracy reasoning |
| **DeepSeek v4.1 Flash** *(Fallback)* | Flash | Disabled | Ultra-fast digest extraction, text analysis, fallback execution |
| **Qwen 3.8 Flash** | Flash | Enabled | Spatial UI understanding, form completion |
| **MiMo v2.6 Flash** | Flash | Enabled | Rapid single-turn lookups |
| **GLM 5.3 Flash** | Flash | Enabled | General browser interaction |

---

## Human-in-the-Loop Safety Controls

The suite pauses and notifies the operator only when human agency is essential:

| Pause Trigger | Event | Action Required |
| :--- | :--- | :--- |
| **`ask_human`** | Ambiguity, choices, user input needed | Enter answer in dashboard input |
| **`hand_over`** | 2FA, passkey, CAPTCHA, login screen | Complete action in Chrome, click *Continue Execution* |
| **`confirm`** | High-impact actions (payment, delete, post, submit) | Click *Authorize* or *Decline* |

*Every pause triggers an audio tone, browser notification, and tab title pulse. Set `NTFY_TOPIC` for mobile alerts via [ntfy.sh](https://ntfy.sh).*

---

## REST API Reference

| Endpoint | Method | Parameters | Description |
| :--- | :---: | :--- | :--- |
| `/api/state` | GET | Header `X-Token` | Current system telemetry, active run, queue, model configurations |
| `/api/run` | POST | `{ task, model, fallback, follow_up, fast }` | Dispatches a new autonomous browser execution |
| `/api/control` | POST | `{ action: "pause"|"resume"|"stop"|"cancel", id? }` | Execution pipeline control |
| `/api/answer` | POST | `{ prompt_id, answer }` | Responds to an active human-in-the-loop prompt |
| `/api/signin` | POST | `{ url }` | Launches dedicated non-automated Chrome profile for persistent login |
| `/api/library` | GET | Header `X-Token` | Fetches watchlist targets, scheduler settings, and recent digest logs |
| `/api/watches` | POST | `{ action: "add"|"toggle"|"remove"|"schedule"|"run", ... }` | Manages social digest targets and triggers on-demand runs |
| `/api/new` | POST | `{}` | Resets multi-turn conversational context |

---

## Portable Distribution Bundle

For offline machines, remote servers, or home setups, use the standalone portable archive:
- **Archive Path**: `browser-automation-suite-portable.zip`
- **Installation**: Extract to any directory, run `Start Browser Agent.cmd`. No manual setup required.

---

## Repository & Git Maintenance

This repository is maintained with clean git hygiene:
- Credentials, local Chrome profiles (`~/.agent-chrome`), API keys, and runtime logs are strictly gitignored.
- Remote repository synchronization:
  ```powershell
  git add .
  git commit -m "feat: description of update"
  git push origin main
  ```
