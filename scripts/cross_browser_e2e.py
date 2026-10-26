"""Cross-browser and cross-device end-to-end check of the Verdict AI web page.

Runs the same checklist in real browser engines (Chromium, Firefox, WebKit, installed Chrome and Edge) and in
device profiles (iPhone, Pixel, Galaxy, iPad, desktop; light and dark). WebKit stands in for Safari.
Usage:  python scripts/cross_browser_e2e.py [--port 8002] [--out runs/compat]
Starts its own server, so it never touches one you already have running.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCX = ROOT / "Banking_Domain_Sample_Requirements.docx"
IGNORED_ERRORS = ("favicon.ico",)

# (label, engine, channel, device name or None, color scheme)
PROFILES = [
    ("Chromium desktop light", "chromium", None, None, "light"),
    ("Chromium desktop dark", "chromium", None, None, "dark"),
    ("Firefox desktop light", "firefox", None, None, "light"),
    ("Firefox desktop dark", "firefox", None, None, "dark"),
    ("WebKit (Safari engine) desktop light", "webkit", None, None, "light"),
    ("WebKit (Safari engine) desktop dark", "webkit", None, None, "dark"),
    ("Google Chrome (installed)", "chromium", "chrome", None, "light"),
    ("Microsoft Edge (installed)", "chromium", "msedge", None, "light"),
    ("iPhone 15 (WebKit) light", "webkit", None, "iPhone 15", "light"),
    ("iPhone 15 (WebKit) dark", "webkit", None, "iPhone 15", "dark"),
    ("iPhone SE (WebKit)", "webkit", None, "iPhone SE", "light"),
    ("iPad Pro 11 (WebKit)", "webkit", None, "iPad Pro 11", "light"),
    ("Pixel 7 (Android Chrome) light", "chromium", None, "Pixel 7", "light"),
    ("Pixel 7 (Android Chrome) dark", "chromium", None, "Pixel 7", "dark"),
    ("Galaxy S9+ (Android)", "chromium", None, "Galaxy S9+", "light"),
    ("Galaxy Tab S4 (Android tablet)", "chromium", None, "Galaxy Tab S4", "light"),
    ("Firefox Android-size viewport", "firefox", None, None, "light"),
]
SMALL_VIEWPORT = {"Firefox Android-size viewport": {"width": 412, "height": 915}}


def cached_build(engine: str) -> Path | None:
    import os
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    pattern = {"firefox": "firefox-*/firefox/firefox.exe", "webkit": "webkit-*/Playwright.exe"}.get(engine)
    found = sorted(base.glob(pattern), key=lambda p: int(re.search(r"-(\d+)", p.parts[-3 if engine == "firefox" else -2]).group(1))) if pattern else []
    return found[-1] if found else None


def wait_js(page, expr: str, timeout_ms: int = 10000) -> None:
    """Poll a page expression. Playwright's own wait_for_function evals a string, which the page's CSP forbids."""
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        if page.evaluate(expr):
            return
        time.sleep(0.2)
    raise TimeoutError(f"condition not met: {expr[:60]}")


def wait_for_server(url: str, tries: int = 40) -> None:
    for _ in range(tries):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.5)
    raise SystemExit("server did not start")


def run_profile(pw, base: str, label: str, engine: str, channel: str | None, device: str | None, scheme: str, out: Path) -> dict:
    res: dict = {"profile": label, "checks": {}, "notes": []}

    def check(name: str, ok: bool, note: str = "") -> None:
        res["checks"][name] = bool(ok)
        if note and not ok:
            res["notes"].append(f"{name}: {note}")

    try:
        launcher = getattr(pw, engine)
        browser = launcher.launch(channel=channel) if channel else launcher.launch()
    except Exception as exc:  # the build this Playwright expects is missing: try the newest cached build
        exe = cached_build(engine)
        try:
            if not exe:
                raise exc
            browser = launcher.launch(executable_path=str(exe))
            res["notes"].append(f"used cached {exe.parent.parent.name} (not the exact build Playwright expects)")
        except Exception as exc2:
            res["error"] = f"could not launch: {str(exc2).splitlines()[0][:120]}"
            return res
    ctx_args: dict = {"color_scheme": scheme}
    if device:
        ctx_args.update(pw.devices[device])
    if label in SMALL_VIEWPORT:
        ctx_args["viewport"] = SMALL_VIEWPORT[label]
    ctx = browser.new_context(**ctx_args)
    page = ctx.new_page()
    errors: list[str] = []
    dialogs: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" and not any(i in m.text for i in IGNORED_ERRORS) and "status of 4" not in m.text else None)
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    res["viewport"] = page.viewport_size
    res["user_agent"] = page.evaluate("navigator.userAgent")[:90]
    res["touch"] = bool(page.evaluate("navigator.maxTouchPoints > 0"))

    t0 = time.time()
    page.goto(base, wait_until="load")
    wait_js(page, "typeof loadSampleData === 'function' && typeof lucide !== 'undefined'", 20000)
    res["load_seconds"] = round(time.time() - t0, 2)
    check("title", "Verdict AI" in page.title())
    check("domain_dropdown_has_14", page.locator("#domainSelect option").count() == 14)

    # theme
    html_dark = page.evaluate("document.documentElement.classList.contains('dark')")
    check("theme_follows_device_scheme", html_dark == (scheme == "dark"), f"dark class={html_dark} for scheme={scheme}")
    page.get_by_role("button", name=re.compile("Toggle Theme")).click()
    flipped = page.evaluate("document.documentElement.classList.contains('dark')")
    check("theme_toggle_flips", flipped != html_dark)
    page.reload(wait_until="load")
    wait_js(page, "typeof loadSampleData === 'function'", 20000)
    persisted = page.evaluate("document.documentElement.classList.contains('dark')")
    check("theme_choice_persists_across_reload", persisted == flipped, f"after reload dark={persisted}, expected {flipped}")

    # layout
    sw, iw = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    check("no_horizontal_overflow_on_load", sw <= iw + 1, f"scrollWidth {sw} > viewport {iw}")

    # sample data, per domain
    page.select_option("#domainSelect", "banking/retail/checking")
    page.get_by_role("button", name="Load Sample Data").click()
    wait_js(page, "currentRequirements.length > 0", 10000)
    ids = page.evaluate("currentRequirements.map(r => r.id)")
    check("sample_banking_loads_bank_requirements", ids and all(i.startswith("BANK") for i in ids), str(ids))
    sw, iw = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    check("no_horizontal_overflow_with_data", sw <= iw + 1, f"scrollWidth {sw} > viewport {iw}")

    # analyze + domain review
    page.locator("#analyzeReqsBtn").click()
    try:
        wait_js(page, "currentDomainReview !== null", 15000)
        check("analyze_shows_domain_review", page.locator("#domainReviewPanel").is_visible())
    except Exception:
        check("analyze_shows_domain_review", False, "panel did not appear")

    # run gate
    page.get_by_role("button", name=re.compile("Run Verdict Gate")).click()
    try:
        wait_js(page, "currentEvaluation && currentEvaluation.gate_results && currentEvaluation.gate_results.length > 0", 20000)
        check("gate_runs_and_shows_results", page.evaluate("!document.getElementById('resultsSection').classList.contains('hidden')"))
    except Exception:
        check("gate_runs_and_shows_results", False, "no results")

    # wrong domain message
    before = len(dialogs)
    page.select_option("#domainSelect", "insurance/life/term")
    page.wait_for_timeout(1200)
    check("wrong_domain_message_shown", len(dialogs) > before and "banking" in dialogs[-1], f"dialogs={dialogs[before:]}")
    page.select_option("#domainSelect", "banking/retail/checking")

    # upload a real DOCX through the file chooser
    with page.expect_file_chooser() as fc:
        page.get_by_text("Upload Files").first.click()
    fc.value.set_files(str(DOCX))
    wait_js(page, "currentRequirements.length === 10", 15000)
    check("docx_upload_parses_10_requirements", page.evaluate("currentRequirements.length") == 10)

    # CSV export download
    try:
        with page.expect_download(timeout=8000) as dl:
            page.get_by_role("button", name="CSV").first.click()
        check("csv_export_downloads", dl.value.suggested_filename.endswith(".csv"), dl.value.suggested_filename)
    except Exception as exc:
        check("csv_export_downloads", False, str(exc).splitlines()[0][:100])

    # security: response headers and script injection through requirement text, scenarios and toasts
    hdrs = page.evaluate("fetch('/').then(r => Object.fromEntries([...r.headers.entries()]))")
    check("security_headers_present", all(h in hdrs for h in ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy")), str(sorted(hdrs)))
    page.evaluate("window.__xss = 0")
    page.evaluate("""async () => {
        const evil = '<img src=x onerror="window.__xss++">', svg = '<svg onload="window.__xss++"></svg>';
        currentAnalyses = {}; currentCases = [];
        currentRequirements = [{id: 'HOME-REQ-X', title: evil, text: svg + evil, acceptance_criteria: evil, domain: 'banking/retail/checking'}];
        renderRequirementsList();
        showToast(evil, 'info');
        document.getElementById('analyzeReqsBtn').click();
        await new Promise(r => setTimeout(r, 2500));
    }""")
    check("xss_payloads_do_not_execute", page.evaluate("window.__xss") == 0, f"{page.evaluate('window.__xss')} payloads ran")
    page.evaluate("currentRequirements = []; renderRequirementsList()")

    # touch targets and keyboard
    small = page.evaluate("""() => Array.from(document.querySelectorAll('button, select, [role=button]'))
        .filter(e => e.offsetParent !== null).map(e => e.getBoundingClientRect())
        .filter(r => r.height < 24 || r.width < 24).length""")
    total = page.evaluate("Array.from(document.querySelectorAll('button, select')).filter(e => e.offsetParent !== null).length")
    res["tap_targets_under_24px"] = f"{small}/{total}"
    check("tap_targets_meet_wcag_24px", small == 0, f"{small} of {total} controls are under 24px")
    page.keyboard.press("Tab")
    check("keyboard_tab_moves_focus", page.evaluate("document.activeElement && document.activeElement !== document.body"))

    check("no_js_errors", not errors, "; ".join(errors[:3]))
    shot = out / (re.sub(r"[^a-z0-9]+", "_", label.lower()) + ".png")
    try:
        page.screenshot(path=str(shot), full_page=False)
        res["screenshot"] = str(shot.relative_to(ROOT))
    except Exception:
        pass
    ctx.close()
    browser.close()
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--out", default="runs/compat")
    args = ap.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "verdict_ai.web.app:create_app", "--factory", "--port", str(args.port), "--log-level", "warning"], cwd=ROOT)
    results = []
    try:
        wait_for_server(base)
        with sync_playwright() as pw:
            for label, engine, channel, device, scheme in PROFILES:
                try:
                    results.append(run_profile(pw, base, label, engine, channel, device, scheme, out))
                except Exception as exc:
                    results.append({"profile": label, "error": f"run failed: {str(exc).splitlines()[0][:160]}", "checks": {}, "notes": []})
                r = results[-1]
                failed = [k for k, v in r["checks"].items() if not v]
                print(f"{r['profile']:42s} {'ERROR ' + r['error'] if 'error' in r else f'{sum(r['checks'].values())}/{len(r['checks'])} ok  load {r.get('load_seconds')}s'}" + (f"  FAIL: {failed}" if failed else ""), flush=True)
    finally:
        server.terminate()
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
