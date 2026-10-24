"""
End-to-end browser verification script for Verdict AI Web UI using Playwright.
Tests:
1. Web server boot on localhost.
2. Load Sample Data.
3. Analyze Requirements.
4. Generate Test Cases.
5. Run Verdict Gate.
6. Verify metric cards and detailed results table.
"""
import sys
import time
import subprocess
import urllib.request
from playwright.sync_api import sync_playwright

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

def wait_for_server(timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"{BASE_URL}/api/domains")
            with urllib.request.urlopen(req, timeout=1) as res:
                if res.status == 200:
                    print(f"[OK] Verdict AI server responsive at {BASE_URL}")
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def run_e2e_browser_test():
    server_process = None
    try:
        # Check if already running, else start it
        if not wait_for_server(timeout=2):
            print(f"[INFO] Starting Verdict AI uvicorn server on port {SERVER_PORT}...")
            server_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "verdict_ai.web.app:app",
                    "--host",
                    SERVER_HOST,
                    "--port",
                    str(SERVER_PORT),
                ]
            )
            if not wait_for_server(timeout=20):
                print("[ERROR] Server failed to start within 20s.")
                sys.exit(1)

        print("[INFO] Launching Playwright browser...")
        with sync_playwright() as p:
            browser = None
            for channel in ["msedge", "chrome", None]:
                try:
                    kwargs = {"headless": True}
                    if channel:
                        kwargs["channel"] = channel
                    browser = p.chromium.launch(**kwargs)
                    print(f"[OK] Launched browser using channel: {channel or 'default-chromium'}")
                    break
                except Exception as e:
                    print(f"[WARN] Failed to launch with channel={channel}: {e}")

            if not browser:
                print("[ERROR] Could not launch any browser via Playwright.")
                sys.exit(1)

            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            # 1. Navigate to Web UI
            print(f"[TEST 1] Navigating to {BASE_URL}...")
            page.goto(BASE_URL, wait_until="networkidle")
            assert "Verdict AI" in page.title()
            print("[PASS] Title matches 'Verdict AI'")

            # 2. Click Load Sample Data
            print("[TEST 2] Clicking 'Load Sample Data'...")
            page.click("button:has-text('Load Sample Data')")
            page.wait_for_selector("#reqContainer .p-3", timeout=5000)
            page.wait_for_selector("#caseContainer .p-3", timeout=5000)
            req_badge = page.text_content("#reqCountBadge")
            case_badge = page.text_content("#caseCountBadge")
            print(f"[PASS] Sample data loaded: Requirements={req_badge}, Cases={case_badge}")
            assert "loaded" in req_badge and "0 loaded" not in req_badge
            assert "loaded" in case_badge and "0 loaded" not in case_badge

            # 3. Click Analyze Requirements
            print("[TEST 3] Clicking 'Analyze' requirements...")
            page.click("#analyzeReqsBtn")
            # Wait until summary bar is visible
            page.wait_for_selector("#reqAnalysisSummaryBar:not(.hidden)", timeout=10000)
            testable_rate = page.text_content("#summaryTestableRate")
            avg_score = page.text_content("#summaryAvgScore")
            print(f"[PASS] Requirements analyzed: Testable Rate={testable_rate}, Avg Score={avg_score}")

            # 4. Click Run Verdict Gate
            print("[TEST 4] Clicking 'Run Verdict Gate'...")
            page.click("#runBtn")
            # Wait for results section to be visible
            page.wait_for_selector("#resultsSection:not(.hidden)", timeout=15000)
            
            # Read metrics
            total = page.text_content("#statTotal")
            approved = page.text_content("#statApproved")
            revised = page.text_content("#statRevised")
            rejected = page.text_content("#statRejected")
            human_review = page.text_content("#statHumanReview")
            mean_score = page.text_content("#statMeanScore")
            print(f"[PASS] Gate evaluation completed:")
            print(f"       Total: {total} | Approved: {approved} | Revised: {revised} | Rejected: {rejected} | Human Review: {human_review} | Mean Score: {mean_score}")

            assert int(total) > 0, "Total cases evaluated must be greater than 0"
            assert int(approved) > 0, "At least one case should be approved"

            # 5. Check detail rows in table
            rows = page.query_selector_all("#resultsTableBody tr")
            print(f"[PASS] Verified {len(rows)} verdict rows in results table.")
            assert len(rows) > 0

            # 6. Test Filtering buttons
            page.click("#filterBtn_approve")
            approved_count = page.text_content("#filterCount_approve")
            print(f"[PASS] Filtered to Approved cases: {approved_count}")

            # Take screenshot confirmation
            screenshot_path = "C:/Verdict-Ai/scripts/e2e_browser_verdict_pass.png"
            page.screenshot(path=screenshot_path, full_page=True)
            print(f"[PASS] Saved full-page screenshot to {screenshot_path}")

            browser.close()
            print("\n=== ALL PLAYWRIGHT E2E UI TESTS PASSED ===")

    finally:
        if server_process:
            print("[INFO] Terminating background server process...")
            server_process.terminate()
            server_process.wait()

if __name__ == "__main__":
    run_e2e_browser_test()
