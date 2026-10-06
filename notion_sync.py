"""Notion Kanban Sync Engine for Autonomous Browser Agent.

Synchronizes task executions, real-time status transitions, step-by-step
actions, and extracted content into a self-updating Notion Kanban database.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NOTION_VERSION = "2022-06-28"
CONFIG_DIR = Path(os.environ.get("NOTION_CONFIG_DIR") or Path.home() / ".config" / "notion")
CONFIG_FILE = CONFIG_DIR / "config.json"


def clean_id(raw: str) -> str:
    """Extract and format standard 32-char UUID from raw ID or Notion URL."""
    if not raw:
        return ""
    raw = raw.strip()
    m = re.findall(r"[0-9a-fA-F]{32}", raw.replace("-", ""))
    if m:
        s = m[-1]
        return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:]}"
    m2 = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", raw)
    if m2:
        return m2.group(0)
    return raw.strip()


def load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "token": os.environ.get("NOTION_API_KEY", ""),
        "database_id": clean_id(os.environ.get("NOTION_DATABASE_ID", "")),
        "database_url": "",
        "auto_sync": True,
    }


def save_config(token: str = None, database_id: str = None, auto_sync: bool = None, database_url: str = None):
    cfg = load_config()
    if token is not None:
        cfg["token"] = token.strip()
    if database_id is not None:
        cfg["database_id"] = clean_id(database_id)
    if auto_sync is not None:
        cfg["auto_sync"] = bool(auto_sync)
    if database_url is not None:
        cfg["database_url"] = database_url
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def api_request(endpoint: str, method: str = "GET", data: dict = None, token: str = None):
    cfg = load_config()
    tok = token or cfg.get("token", "")
    if not tok:
        raise ValueError("Notion API token is not configured.")
    url = f"https://api.notion.com/v1/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {tok}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
        "User-Agent": "Browser-Agent-Notion-Sync/1.0",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_msg)
            msg = err_json.get("message", err_msg)
        except Exception:
            msg = err_msg or str(e)
        raise RuntimeError(f"Notion API Error ({e.code}): {msg}")


def test_connection(token: str = None, database_id: str = None):
    tok = token or load_config().get("token", "")
    db_id = clean_id(database_id or load_config().get("database_id", ""))
    user_info = api_request("users/me", token=tok)
    db_info = None
    if db_id:
        db_info = api_request(f"databases/{db_id}", token=tok)
    return {
        "ok": True,
        "bot_name": user_info.get("name", "Notion Integration"),
        "database_title": (db_info.get("title", [{}])[0].get("plain_text") if db_info else None),
        "database_url": (db_info.get("url") if db_info else None),
    }


def create_kanban_database(parent_page_id: str, title: str = "Browser Agent Operations Kanban", token: str = None):
    parent_id = clean_id(parent_page_id)
    if not parent_id:
        raise ValueError("A valid Notion Parent Page ID is required.")
    payload = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {
            "Task": {"title": {}},
            "Status": {
                "status": {
                    "options": [
                        {"name": "Queued", "color": "gray"},
                        {"name": "In Progress", "color": "blue"},
                        {"name": "Needs Human", "color": "yellow"},
                        {"name": "Completed", "color": "green"},
                        {"name": "Failed", "color": "red"},
                    ]
                }
            },
            "Model": {"select": {}},
            "Category": {"select": {}},
            "Duration (s)": {"number": {"format": "number"}},
            "Steps Count": {"number": {"format": "number"}},
            "Target URL": {"url": {}},
            "Execution Date": {"date": {}},
        },
    }
    db = api_request("databases", method="POST", data=payload, token=token)
    db_id = db["id"]
    db_url = db.get("url", f"https://notion.so/{db_id.replace('-', '')}")
    save_config(database_id=db_id, database_url=db_url)
    return {"database_id": db_id, "database_url": db_url, "title": title}


def _rich_text(content: str, max_len: int = 1950):
    text = str(content or "")[:max_len]
    return [{"type": "text", "text": {"content": text}}] if text else []


def _detect_status_prop_type(database_id: str, token: str = None) -> str:
    try:
        db = api_request(f"databases/{database_id}", token=token)
        status_prop = db.get("properties", {}).get("Status", {})
        return status_prop.get("type", "status")
    except Exception:
        return "status"


def create_task_card(run, token: str = None, database_id: str = None) -> str:
    cfg = load_config()
    db_id = clean_id(database_id or cfg.get("database_id", ""))
    if not db_id:
        return ""
    tok = token or cfg.get("token", "")
    if not tok:
        return ""

    status_type = _detect_status_prop_type(db_id, tok)
    task_title = run.task[:100] + ("…" if len(run.task) > 100 else "")
    category = "Social Digest" if getattr(run, "kind", "task") == "digest" else "Autonomous Task"
    now_iso = datetime.now(timezone.utc).isoformat()

    status_val = {"name": "In Progress"}
    prop_status = {status_type: status_val} if status_type in ("status", "select") else {"status": status_val}

    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Task": {"title": _rich_text(task_title)},
            "Status": prop_status,
            "Model": {"select": {"name": getattr(run, "model", "default")[:50]}},
            "Category": {"select": {"name": category}},
            "Execution Date": {"date": {"start": now_iso}},
        },
    }
    try:
        page = api_request("pages", method="POST", data=payload, token=tok)
        return page.get("id", "")
    except Exception as e:
        print(f"Notion create_task_card error: {e}")
        return ""


def update_task_card_status(page_id: str, status_name: str, token: str = None):
    if not page_id:
        return
    cfg = load_config()
    tok = token or cfg.get("token", "")
    db_id = cfg.get("database_id", "")
    status_type = _detect_status_prop_type(db_id, tok) if db_id else "status"

    payload = {
        "properties": {
            "Status": {status_type: {"name": status_name}}
        }
    }
    try:
        api_request(f"pages/{page_id}", method="PATCH", data=payload, token=tok)
    except Exception as e:
        print(f"Notion update_task_card_status error: {e}")


def finalize_task_card(page_id: str, run, token: str = None):
    if not page_id:
        return
    cfg = load_config()
    tok = token or cfg.get("token", "")
    db_id = cfg.get("database_id", "")
    status_type = _detect_status_prop_type(db_id, tok) if db_id else "status"

    status_name = "Completed" if run.ok else ("Failed" if run.status == "failed" else "Completed")
    duration = round((run.ended or time.time()) - (run.started or time.time()), 1)
    steps_count = len(getattr(run, "steps", []))

    last_url = ""
    if getattr(run, "steps", []):
        for s in reversed(run.steps):
            u = s.get("url", "")
            if u and not u.startswith("about:"):
                last_url = u[:2000]
                break

    # 1. Update page properties
    prop_updates = {
        "Status": {status_type: {"name": status_name}},
        "Duration (s)": {"number": duration},
        "Steps Count": {"number": steps_count},
    }
    if last_url and last_url.startswith(("http://", "https://")):
        prop_updates["Target URL"] = {"url": last_url}

    try:
        api_request(f"pages/{page_id}", method="PATCH", data={"properties": prop_updates}, token=tok)
    except Exception as e:
        print(f"Notion update props error: {e}")

    # 2. Append rich blocks to page content
    blocks = []

    summary_text = run.result or ("Execution completed successfully." if run.ok else "Execution finished.")
    icon_emoji = "✅" if run.ok else "❌"
    blocks.append({
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(f"Result: {summary_text}"),
            "icon": {"type": "emoji", "emoji": icon_emoji},
            "color": "green_background" if run.ok else "red_background",
        },
    })

    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich_text("🎯 Task Instruction")},
    })
    blocks.append({
        "object": "block",
        "type": "quote",
        "quote": {"rich_text": _rich_text(run.task)},
    })

    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich_text("📦 Fetched & Extracted Content")},
    })
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(summary_text)},
    })

    steps = getattr(run, "steps", [])
    if steps:
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich_text(f"👣 Execution Steps Breakdown ({len(steps)} steps)")},
        })
        step_blocks = []
        for s in steps[:40]:
            n = s.get("n", 0)
            goal = s.get("goal", "Executing step")
            acts = ", ".join(s.get("actions", []))
            u = s.get("url", "")
            txt = f"Step {n}: {goal}" + (f" [{acts}]" if acts else "") + (f" - {u}" if u else "")
            step_blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _rich_text(txt)},
            })
        blocks.extend(step_blocks)

    try:
        api_request(f"blocks/{page_id}/children", method="PATCH", data={"children": blocks[:95]}, token=tok)
    except Exception as e:
        print(f"Notion append blocks error: {e}")


def get_public_status() -> dict:
    cfg = load_config()
    tok = cfg.get("token", "")
    db_id = cfg.get("database_id", "")
    has_token = bool(tok and len(tok) > 10)
    has_db = bool(db_id and len(db_id) >= 32)
    db_url = cfg.get("database_url") or (f"https://notion.so/{db_id.replace('-', '')}" if has_db else "")
    return {
        "configured": bool(has_token and has_db),
        "has_token": has_token,
        "database_id": db_id,
        "database_url": db_url,
        "auto_sync": bool(cfg.get("auto_sync", True)),
    }

