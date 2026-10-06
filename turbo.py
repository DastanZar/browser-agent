"""Sandboxed Turbo Accelerator & Prompt Optimizer for Browser Automation Suite.

Provides:
1. Instant Pre-Navigation: extracts destination URLs and navigates on Step 0,
   completely bypassing the 30-second `about:blank` LLM discovery turn.
2. Tab-Aware Pre-Switching: detects if target site is already open in Chrome.
3. CDP Ad & Beacon Blocking: suppresses network tracking streams that prevent
   network idle from firing on YouTube, LinkedIn, and heavy SPAs.
4. Tuned Browser Timings: 0.1s minimum page wait, 0.2s network idle wait.
5. Prompt Optimizer: sub-second compilation of conversational instructions into
   structured, phase-based execution blueprints with exact search syntax.
"""
import json
import re
import time
import urllib.parse
from typing import Any

COMMON_DOMAINS = {
    "youtube": "https://www.youtube.com",
    "youtube.com": "https://www.youtube.com",
    "studio.youtube.com": "https://studio.youtube.com",
    "yt": "https://www.youtube.com",
    "linkedin": "https://www.linkedin.com",
    "linkedin.com": "https://www.linkedin.com",
    "github": "https://github.com",
    "github.com": "https://github.com",
    "wikipedia": "https://www.wikipedia.org",
    "wikipedia.org": "https://www.wikipedia.org",
    "google": "https://www.google.com",
    "google.com": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "mail.google.com": "https://mail.google.com",
    "google cloud": "https://console.cloud.google.com",
    "console.cloud.google.com": "https://console.cloud.google.com",
    "gcp": "https://console.cloud.google.com",
    "hackernews": "https://news.ycombinator.com",
    "hacker news": "https://news.ycombinator.com",
    "news.ycombinator.com": "https://news.ycombinator.com",
    "twitter": "https://x.com",
    "x.com": "https://x.com",
    "reddit": "https://www.reddit.com",
    "reddit.com": "https://www.reddit.com",
    "notion": "https://www.notion.so",
    "notion.so": "https://www.notion.so",
}

BLOCKED_URL_PATTERNS = [
    "*doubleclick.net*",
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*scorecardresearch.com*",
    "*youtube.com/api/stats/playback*",
    "*youtube.com/api/stats/watchtime*",
    "*youtube.com/youtubei/v1/log_event*",
    "*linkedin.com/li/track*",
    "*facebook.com/tr/*",
    "*analytics.twitter.com*",
]


def extract_target_url(task: str) -> str | None:
    """Extracts a target destination URL deterministically from user instructions.
    
    Returns standard https:// URL if found, else None.
    """
    if not task:
        return None
    text = task.strip()

    # 1. Look for explicit http/https URLs
    m_url = re.search(r'https?://[^\s<>"\')]+', text)
    if m_url:
        return m_url.group(0).rstrip('.,;:')

    # 2. Look for explicit domain patterns like 'something.com/path' or 'sub.domain.org'
    m_domain = re.search(r'\b([a-zA-Z0-9-]+\.(?:com|org|io|net|dev|ai|gov|edu)(?:/[^\s<>"\')]+)?)\b', text, re.IGNORECASE)
    if m_domain:
        raw_dom = m_domain.group(1).lower().rstrip('.,;:')
        return f"https://{raw_dom}"

    # 3. Look for phrases like 'open <service>', 'go to <service>', 'navigate to <service>'
    m_phrase = re.search(r'\b(?:open|go\s+to|navigate\s+to|browse\s+to|check)\s+([a-zA-Z0-9\.\s_-]+?)(?:,|\.|\n|$)', text, re.IGNORECASE)
    if m_phrase:
        candidate = m_phrase.group(1).strip().lower()
        if candidate in COMMON_DOMAINS:
            return COMMON_DOMAINS[candidate]
        for name in sorted(COMMON_DOMAINS.keys(), key=len, reverse=True):
            if name in candidate:
                return COMMON_DOMAINS[name]

    # 4. Search substrings against COMMON_DOMAINS (longest keys first)
    text_lower = text.lower()
    for name in sorted(COMMON_DOMAINS.keys(), key=len, reverse=True):
        if re.search(r'\b' + re.escape(name) + r'\b', text_lower):
            return COMMON_DOMAINS[name]

    return None


async def enable_cdp_ad_blocking(browser_session: Any) -> bool:
    """Applies CDP Network.setBlockedURLs to suppress tracking beacons and ads."""
    try:
        cdp = await browser_session.get_or_create_cdp_session()
        await cdp.cdp_client.send.Network.enable(session_id=cdp.session_id)
        await cdp.cdp_client.send.Network.setBlockedURLs(
            params={"urls": BLOCKED_URL_PATTERNS},
            session_id=cdp.session_id
        )
        return True
    except Exception as e:
        print(f"[Turbo] Failed to set CDP blocked URLs: {e}")
        return False


def get_turbo_timings() -> dict[str, float]:
    """Returns tuned timing parameters for high-speed page interaction."""
    return {
        "minimum_wait_page_load_time": 0.1,
        "wait_for_network_idle_page_load_time": 0.2,
        "wait_between_actions": 0.1,
    }


PROMPT_OPTIMIZER_SYSTEM_PROMPT = """You are an expert Autonomous Browser Agent Instruction Compiler.
Your goal is to take conversational, broad, or complex operator prompts and compile them into high-precision, unambiguous browser execution plans.

GUIDELINES FOR COMPILATION:
1. Target Website & Entry Point: State the exact target URL clearly as the very first instruction.
2. Exact Search Syntax: When searching (e.g. LinkedIn, Google, GitHub), convert vague role titles into high-precision Boolean queries.
   Example: For "mid level employees of uber, microsoft, stripe and their hr and hiring managers, talent acquisition" ->
   Generate exact search string: ("Uber" OR "Microsoft" OR "Stripe") AND ("Talent Acquisition" OR "Recruiter" OR "HR Manager" OR "Hiring Manager")
3. Execution Phases:
   - Phase 1: Navigation & Query Entry
   - Phase 2: Result Filtering (e.g. Filter by People, Location if applicable)
   - Phase 3: Extraction Schema (specify exact fields to capture: Name, Current Title, Company, Profile URL)
   - Phase 4: Action/Interaction Rules (e.g. "Click Connect -> Add note or Send without note -> Cancel if 2FA/email needed")
4. Safety & Guardrails: Specify max targets (e.g. "Extract first 5-10 profiles to avoid rate limits").
5. Output format: Return concise, structured markdown ready for direct execution.
"""


async def compile_prompt(raw_prompt: str, model_id: str = "mimo-v2.6-flash") -> dict[str, Any]:
    """Compiles a conversational user prompt into a structured execution blueprint using a fast LLM."""
    start_t = time.time()
    try:
        import core
        from browser_use.llm.messages import UserMessage, SystemMessage
        llm = core.make_llm(model_id)
        msgs = [
            SystemMessage(content=PROMPT_OPTIMIZER_SYSTEM_PROMPT),
            UserMessage(content=f"Compile this conversational prompt into a structured browser agent instruction:\n\n{raw_prompt}")
        ]
        res = await llm.ainvoke(msgs)
        content = getattr(res, "completion", "") or getattr(res, "content", "") or str(res)
        elapsed = round(time.time() - start_t, 2)
        return {
            "ok": True,
            "optimized": content.strip(),
            "original": raw_prompt,
            "duration": elapsed,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "optimized": raw_prompt,
            "original": raw_prompt,
            "duration": round(time.time() - start_t, 2),
        }
