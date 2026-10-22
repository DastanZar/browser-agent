"""Offline regression tests: no browser, no API key, no network.

    python tests/test_offline.py          (or: python -m pytest tests)

Each test pins down a safety rule or a bug that was actually hit. Run before every push.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BROWSER_AGENT_HOME"] = tempfile.mkdtemp()  # digest state goes to a scratch folder
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

import core    # noqa: E402
import digest  # noqa: E402


class FakeNode:
    def __init__(self, tag, text="", **attrs):
        self.tag_name, self._text, self.attributes = tag, text, attrs

    def get_all_children_text(self):
        return self._text


class FakeState:
    def __init__(self, nodes, url="https://example.com/"):
        self.url = url
        self.dom_state = type("D", (), {"selector_map": dict(enumerate(nodes))})()


# ---------------------------------------------------------------- task start

def test_start_url_from_task():
    assert core.start_url("Open https://www.youtube.com/@veritasium/videos and list") == "https://www.youtube.com/@veritasium/videos"
    assert core.start_url("the three channels: open youtube.com and check") == "https://youtube.com"
    assert core.start_url("go to console.cloud.google.com/apis?project=x, enable it") == "https://console.cloud.google.com/apis?project=x"
    assert core.start_url("email me at a@b.com") is None
    assert core.start_url("Summarise this page") is None


# ---------------------------------------------------------------- approval gate

def test_risky_words():
    risky = ["Create account and pay", "Send", "Delete project", "Connect", "Like", "Post",
             "Proceed to www.youtube.com (unsafe)", "Accept the Risk and Continue"]
    safe = ["Save", "Create", "Enable API", "Next", "Search", "Advanced", "Back to safety", "Liked by 3 people"]
    for t in risky:
        assert core.RISKY.search(t), t
    for t in safe:
        assert not core.RISKY.search(t), t


def test_checkbox_is_not_gated():
    state = FakeState([FakeNode("input", "", type="checkbox"), FakeNode("button", "Create account and pay")])
    gate = core.Gate(human=None)
    assert gate._risky("click", {"index": 0}, state) == ""
    assert "Create account and pay" in gate._risky("click", {"index": 1}, state)


def test_secret_only_into_password_field():
    state = FakeState([FakeNode("textarea", ""), FakeNode("input", "", type="password"), FakeNode("input", "", type="email")])
    misuse = core.Gate._secret_misuse
    assert misuse("input", {"index": 0, "text": "<secret>site_password</secret>"}, state)   # a post box: refused
    assert not misuse("input", {"index": 1, "text": "<secret>site_password</secret>"}, state)
    assert not misuse("input", {"index": 2, "text": "<secret>site_username</secret>"}, state)
    assert misuse("input", {"index": 2, "text": "<secret>site_password</secret>"}, state)   # password into email field


# ---------------------------------------------------------------- digest: text comes from the page

PAGE = ("Priya Sharma · 2h\nExcited to share that I've joined Acme Robotics as Head of Product!\nView post\n"
        "Priya Sharma · 9h\nOur team just open-sourced FastQueue, a tiny job queue for Python.\nView post")
WATCH = {"id": "w1", "name": "t", "url": "https://x.com/priya"}


def test_verify_fills_text_and_flags_inventions():
    p = digest.verify({"author": "Priya", "quote": "Excited to share that I've joined Acme", "summary": "Joined Acme Robotics."}, PAGE)
    assert p["check"] == "verbatim" and p["text"].startswith("Excited to share")
    fake = digest.verify({"author": "Priya", "quote": "Big news: we raised a seed round led by Meridian", "summary": "Seed round led by Meridian Ventures."}, PAGE)
    assert fake["check"] == "unverified"
    named = digest.verify({"author": "Priya", "quote": "Our team just open-sourced FastQueue", "summary": "FastQueue, backed by Initrode."}, PAGE)
    assert "Initrode" not in named["summary"]  # a name that isn't on the page gets replaced by the real text


def test_record_merges_duplicates_and_drops_fake_post_links():
    posts = [
        {"author": "Priya", "when": "2h", "quote": "Excited to share that I've joined Acme", "summary": "a", "url": WATCH["url"], "links": []},
        {"author": "Priya S.", "when": "2h", "quote": "Excited to share that I have joined Acme Robotics", "summary": "b", "url": "", "links": []},
        {"author": "Priya", "when": "9h", "quote": "Our team just open-sourced FastQueue", "summary": "c", "url": "https://bench.example/a",
         "links": [{"url": "https://bench.example/a", "title": "B", "summary": "benchmarks"}]},
    ]
    e = digest.record(dict(WATCH, id="w-merge"), posts, "n", PAGE)
    assert len(e["items"]) == 2                    # the 2h post saved twice became one item
    assert all(i["url"] == "" for i in e["items"])  # watched page / outbound link aren't post links


def test_seen_by_link_or_text():
    w = dict(WATCH, id="w-seen")
    p = {"author": "Priya", "when": "9h", "quote": "Our team just open-sourced FastQueue", "summary": "s", "links": []}
    assert [i["new"] for i in digest.record(w, [dict(p, url="https://x.com/post/2")], "n", PAGE)["items"]] == [True]
    assert [i["new"] for i in digest.record(w, [dict(p, url="")], "n", PAGE)["items"]] == [False]
    assert [i["new"] for i in digest.record(w, [dict(p, url="https://x.com/post/2", author="P.")], "n", PAGE)["items"]] == [False]


def test_failed_run_keeps_since_marker():
    w = dict(WATCH, id="w-fail")
    digest.record(w, [], "ok run", PAGE, ok=True)
    before = digest._read(digest.SEEN, {})[w["id"]]["last_run"]
    digest.record(w, [], "failed run", PAGE, ok=False)
    assert digest._read(digest.SEEN, {})[w["id"]]["last_run"] == before


# ---------------------------------------------------------------- dashboard access rules

def test_dashboard_rejects_foreign_host_and_cookie_only_api():
    import dashboard
    from starlette.testclient import TestClient
    c = TestClient(dashboard.app, base_url=f"http://127.0.0.1:{dashboard.PORT}", client=("127.0.0.1", 50000))
    assert c.get("/").status_code == 200
    rebind = c.get("/", headers={"host": f"evil.example:{dashboard.PORT}"})  # DNS-rebinding attempt
    assert rebind.status_code == 403
    assert c.get("/api/state").status_code == 403                             # no token
    assert c.get("/api/state", headers={"X-Token": dashboard.TOKEN}).status_code == 200
    c.cookies.set("agy_token", dashboard.TOKEN)
    assert c.post("/api/new", json={}).status_code == 403                     # cookie alone can't drive the API
    assert dashboard.HOST == "127.0.0.1" or os.environ.get("DASHBOARD_HOST")  # local-only unless opted in


def test_next_browser_falls_back_to_agent_window():
    import dashboard
    os.environ["CHROME_USER_DATA_DIR"] = tempfile.mkdtemp()  # no DevToolsActivePort: your Chrome isn't connected
    dashboard.studio.browser, dashboard.studio.browser_mode = None, "auto"
    assert dashboard.next_browser().startswith("agent browser")
    dashboard.studio.browser_mode = "mine"
    assert dashboard.next_browser().startswith("your Chrome")
    dashboard.studio.browser_mode = "auto"


def test_live_browser_is_kept_between_tasks():
    import asyncio
    import socket
    import dashboard
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()  # stands in for a Chrome that is still answering on its debugging port
    live = type("B", (), {"cdp_url": f"http://127.0.0.1:{srv.getsockname()[1]}"})()
    dropped = []

    async def drop():
        dropped.append(1)
        dashboard.studio.browser = None

    real_drop, dashboard.studio.drop_browser, dashboard.studio.browser = dashboard.studio.drop_browser, drop, live
    try:
        asyncio.run(dashboard.studio.ensure_browser())
        assert dropped == [] and dashboard.studio.browser is live  # reused, not reconnected
    finally:
        srv.close()
        dashboard.studio.drop_browser, dashboard.studio.browser = real_drop, None


def test_prompt_optimizer_specifies_goals_not_click_steps():
    import turbo
    prompt = turbo.PROMPT_OPTIMIZER_SYSTEM_PROMPT
    assert "NEVER INVENT UI STEPS OR MECHANICS" in prompt
    assert "Goal:" in prompt
    assert "Done When:" in prompt
    assert "Limits & Safety:" in prompt


class FakeSession:
    """Answers save_item's page read with fixed text and links."""
    def __init__(self, text, links=()):
        page = [text, "\n".join(links)]

        class Send:
            class Runtime:
                @staticmethod
                async def evaluate(params, session_id):
                    return {"result": {"value": page}}
        self.cdp = type("C", (), {"cdp_client": type("CC", (), {"send": Send})(), "session_id": "s"})()

    async def get_or_create_cdp_session(self):
        return self.cdp


def test_save_item_checks_values_against_the_page():
    import asyncio
    items = []
    act = core.build_tools(None, items=items).registry.registry.actions["save_item"]
    page = FakeSession("Mira Okafor\nTechnical Recruiter at Acme Mobility\nAustin, Texas\nConnect")

    def save(name, **details):
        params = act.param_model(name=name, details=[{"field": k, "value": v} for k, v in details.items()])
        return asyncio.run(act.function(params=params, browser_session=page)).extracted_content

    assert "Not found" not in save("Mira Okafor", title="Technical Recruiter", location="Austin, Texas")
    assert "Not found on this page: location" in save("Tobias Wren", location="Denver, Colorado")  # invented
    save("mira  okafor", location="Austin, Texas")                     # same person again: updated, not added
    assert len(items) == 2 and items[0]["fields"]["title"] == "Technical Recruiter" and items[0]["unverified"] == []
    assert items[1]["unverified"] == ["location", "name"]


def test_policy_a_file_results_scoped():
    assert "Results live in save_item/save_post, never in files" in core.POLICY
    assert "Files only when the task explicitly asks for a file" in core.POLICY


def test_policy_b_search_once_directly():
    assert "Search once, directly" in core.POLICY
    assert "Never type the same query twice" in core.POLICY


def test_policy_c_batch_approvals():
    assert "Batch approvals" in core.POLICY
    assert "still one confirm per send" in core.POLICY


def test_policy_d_connect_chain():
    assert "LinkedIn Connect chain" in core.POLICY
    assert "after 3+ retries on one profile, stop and report" in core.POLICY


def test_policy_e_tab_hygiene():
    assert "Tab hygiene on lists" in core.POLICY
    assert "Keep the page only when a follow-up needs it" in core.POLICY


def test_bench_scorer():
    sys.path.insert(0, str(ROOT / "bench"))
    import run as bench
    spec = bench.tasks("http://x")["people"]
    rows = "\n".join(f"{i}. {a['name']} — {a['title']} — {a['location']}" for i, a in enumerate(spec["answer"], 1))
    excluded = "\n\nEXCLUSIONS (not current staff):\n- Theo Nakamura — Recruiter (formerly Acme Mobility)\n- Hugo Lambert — Recruiting Operations Analyst"
    assert bench.score(spec, rows + excluded, [])["wrong"] == 0                  # explained exclusions aren't answers
    assert bench.score(spec, rows + "\n11. Theo Nakamura — Recruiter", [])["wrong"] == 1
    inline = rows + "\nTheo Nakamura (Recruiter) was excluded because he is no longer at Acme Mobility."
    assert bench.score(spec, inline, [])["wrong"] == 0
    bad = bench.score(spec, "", [{"fields": {"name": "Mira Okafor", "title": "Technical Recruiter", "location": "Austin, TX"}},
                                 {"fields": {"name": "Jane Fakename", "title": "Recruiter"}}])
    assert (bad["right"], bad["partial"], bad["invented"]) == (0, 1, 1)          # wrong field; invented person


def test_extract_target_url_no_crash_and_respects_local_context():
    import turbo
    # Previously crashed with NameError: text_lower not defined when no URL/domain/phrase matched.
    assert turbo.extract_target_url("Find 10 tech recruiters and list them") is None
    assert turbo.extract_target_url("") is None
    assert turbo.extract_target_url(None) is None
    assert turbo.extract_target_url("Open https://www.youtube.com/@veritasium/videos and list") == \
        "https://www.youtube.com/@veritasium/videos"
    assert turbo.extract_target_url("check linkedin for hiring managers") == "https://www.linkedin.com"
    # Local tab context must not trigger a navigation away.
    assert turbo.extract_target_url("Summarise this page about github") is None


# ---------------------------------------------------------------- quick read (read-only fast path)

def test_route_sends_only_read_tasks_to_quick_read():
    import quickread
    assert quickread.route("Open https://x.com/catalog and list every product under $20") == "read"
    assert quickread.route("Sign up on example.com and pick a plan") == "agent"           # changes something
    assert quickread.route("Find 10 recruiters") == "agent"                               # no page to start from
    assert quickread.route("Summarise this page") == "agent"                              # the human's own tab
    assert quickread.route("Open https://x.com/a and send a message to Bob") == "agent"
    # a local test site has no .com: explicit URLs of any host still count as a start page
    assert core.start_url("Open http://127.0.0.1:5000/people . Search") == "http://127.0.0.1:5000/people"


def test_hedged_request_beats_a_hung_call():
    import asyncio
    import time
    import browser_use

    async def scenario(hedge_after, behaviours):
        calls = []

        async def fake(self, messages, output_format=None, **kwargs):
            calls.append(1)
            return await behaviours[len(calls) - 1]()   # one behaviour per call, in the order they start

        real, browser_use.ChatOpenAI.ainvoke = browser_use.ChatOpenAI.ainvoke, fake
        try:
            llm = core.HedgedChatOpenAI(hedge_after=hedge_after, model="m", api_key="x")
            t0 = time.time()
            try:
                out = await llm.ainvoke([])
            except Exception as e:  # noqa: BLE001
                out = e
            took = time.time() - t0
            await asyncio.sleep(0)  # let a cancelled call finish unwinding
            leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return out, took, getattr(llm, "hedges", 0), len(calls), leftover
        finally:
            browser_use.ChatOpenAI.ainvoke = real

    async def hang():
        await asyncio.sleep(30)

    async def fast():
        return "fast"

    async def slow_then_fail():
        await asyncio.sleep(0.4)  # still running when the hedge goes out
        raise RuntimeError("first failed")

    async def fail():
        raise ValueError("second failed")

    out, took, hedges, n, leftover = asyncio.run(scenario(0.2, [hang, fast]))   # a: the hung call loses
    assert out == "fast" and took < 2 and hedges == 1 and n == 2 and not leftover
    out, _, hedges, n, leftover = asyncio.run(scenario(None, [fast, fast]))     # b: no hedging, one plain call
    assert out == "fast" and hedges == 0 and n == 1 and not leftover
    out, _, hedges, n, leftover = asyncio.run(scenario(0.2, [slow_then_fail, fail]))  # c: both fail
    assert isinstance(out, (RuntimeError, ValueError)) and hedges == 1 and n == 2 and not leftover


def _chromium():
    from pathlib import Path
    return os.environ.get("CHROME_PATH") or next(
        (str(p) for p in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None)


def test_quick_read_reads_all_rows_and_opens_detail_pages_in_parallel():
    import asyncio
    import re
    chrome = _chromium()
    if not chrome:
        print("skip  (no chromium in /opt/pw-browsers and no CHROME_PATH) ", end="")
        return
    sys.path.insert(0, str(ROOT / "bench"))
    import quickread
    import run as bench
    import testsite
    from browser_use import Browser
    base = bench.start_site()

    class FakeLLM:
        """Round 1 opens every company page the model was shown; round 2 reports them, plus one invented name."""
        def __init__(self):
            self.rounds = 0

        async def ainvoke(self, messages, output_format=None):
            self.rounds += 1
            if self.rounds == 1:
                urls = re.findall(r"-> (http\S*/companies/[a-z0-9-]+)", messages[-1].text)
                return type("R", (), {"completion": quickread.Reply(status="open", open=urls)})()
            items = [quickread.Item(name=c, details=[quickread.Field(field="founded", value=str(y)),
                                                      quickread.Field(field="hq", value=city)])
                     for c, y, city in testsite.COMPANIES]
            items.append(quickread.Item(name="Nonexistent Corp", details=[quickread.Field(field="hq", value="Atlantis")]))
            return type("R", (), {"completion": quickread.Reply(status="done", answer="ok", items=items)})()

    async def go():
        browser = Browser(executable_path=chrome, headless=True, user_data_dir=tempfile.mkdtemp(prefix="qr-"),
                          chromium_sandbox=False, keep_alive=False)
        real_make_llm = core.make_llm
        try:
            await browser.start()
            page = await quickread.read_page(browser, base + "/catalog")
            missing = [p["name"] for p in testsite.PRODUCTS if p["name"] not in page["text"]]
            assert not missing, f"virtualised rows not read: {missing[:5]} ({len(missing)} of {len(testsite.PRODUCTS)})"
            llm = FakeLLM()
            core.make_llm = lambda model_id: llm
            return await quickread.quick_read(
                "Open " + base + "/companies. For each company listed, open its page and note the year it was "
                "founded and its headquarters city.", "qwen3.8-flash", browser)
        finally:
            core.make_llm = real_make_llm
            try:
                await browser.kill()
            except Exception:  # noqa: BLE001
                pass

    res = asyncio.run(go())
    assert res.status == "done" and res.rounds == 2, (res.status, res.rounds, res.notes)
    assert res.pages >= 1 + len(testsite.COMPANIES), res.pages            # the list page + every detail page
    assert len(res.items) == len(testsite.COMPANIES)                      # "Nonexistent Corp" isn't on any page: dropped
    assert all(i["unverified"] == [] for i in res.items), [i for i in res.items if i["unverified"]]
    by_name = {i["fields"]["name"]: i["fields"] for i in res.items}
    for c, y, city in testsite.COMPANIES:
        assert by_name[c] == {"name": c, "founded": str(y), "hq": city}, by_name.get(c)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{'all passed' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)
