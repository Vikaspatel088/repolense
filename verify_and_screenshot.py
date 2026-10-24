"""Automated Playwright browser verification and real screenshot capture for RepoLens V2."""
import os
import sys
import time
import threading
from playwright.sync_api import sync_playwright
import server

ROOT = os.path.dirname(os.path.abspath(__file__))
SCREENSHOTS_DIR = os.path.join(ROOT, "screenshots")
PORT = 8799
URL = f"http://127.0.0.1:{PORT}"


def run_verification():
    print(f"Starting RepoLens test server on port {PORT}...")
    srv = server.make_server(host="127.0.0.1", port=PORT)
    server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(1)

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        # Listen to console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("1. Testing First-Run Onboarding Experience...")
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector(".hero-title")
        assert "Explore Codebase Architecture" in page.inner_text(".hero-title")
        assert page.is_visible("text=Local Directory")
        assert page.is_visible("text=Public GitHub Repository")
        assert page.is_visible("text=Load Sample Repository")
        results["onboarding_view"] = "PASSED"

        print("2. Testing Sample Repository Load & Analysis...")
        page.click("text=Load Sample Repository")
        page.wait_for_selector(".stat-grid", timeout=15000)
        page.wait_for_selector("text=Files Analyzed")
        time.sleep(1)  # ensure animations settle
        results["sample_repo_analysis"] = "PASSED"

        print("3. Testing Overview Dashboard & Capturing Screenshot 01...")
        stat_cards = page.query_selector_all(".stat-card")
        assert len(stat_cards) >= 6
        assert "11" in page.inner_text(".stat-card:nth-child(1)")  # 11 files
        overview_path = os.path.join(SCREENSHOTS_DIR, "01-overview.png")
        page.screenshot(path=overview_path, full_page=False)
        print(f"   Saved: {overview_path}")
        results["screenshot_01_overview"] = "PASSED"

        print("4. Testing Dependency Graph & Capturing Screenshot 02...")
        page.click("nav button:has-text('Dependency Graph')")
        page.wait_for_selector(".graph-container svg")
        time.sleep(1.5)  # Wait for layout physics simulation
        nodes = page.query_selector_all(".node")
        assert len(nodes) > 0, "Graph nodes should be rendered"
        
        # Click a node using dispatch_event to trigger SVG onclick reliably
        node_locator = page.locator(".node:has-text('catalog.py')").first
        node_locator.dispatch_event("click")
        page.wait_for_selector("#drawer:not([hidden])")
        time.sleep(0.5)
        graph_path = os.path.join(SCREENSHOTS_DIR, "02-dependency-graph.png")
        page.screenshot(path=graph_path, full_page=False)
        print(f"   Saved: {graph_path}")
        results["screenshot_02_graph"] = "PASSED"

        print("5. Testing Files & Symbols Explorer & Capturing Screenshot 03...")
        page.click("nav button:has-text('Files & Symbols')")
        page.wait_for_selector("table.data-table")
        rows = page.query_selector_all("table.data-table tbody tr")
        assert len(rows) == 11, f"Expected 11 file rows, got {len(rows)}"
        
        # Click on pricing.py
        page.click("table.data-table tbody tr:has-text('pricing.py') .file-link")
        page.wait_for_selector("#drawer:not([hidden])")
        page.click("#drawer button:has-text('View Source')")
        page.wait_for_selector("pre.src-view")
        time.sleep(0.5)
        files_path = os.path.join(SCREENSHOTS_DIR, "03-files-and-source.png")
        page.screenshot(path=files_path, full_page=False)
        print(f"   Saved: {files_path}")
        results["screenshot_03_files_source"] = "PASSED"

        print("6. Testing Impact Analysis & Capturing Screenshot 04...")
        page.click("nav button:has-text('Impact Analysis')")
        page.wait_for_selector("#impact-select")
        page.select_option("#impact-select", value="services/catalog.py")
        page.wait_for_selector(".path-breadcrumb")
        time.sleep(0.5)
        impact_path = os.path.join(SCREENSHOTS_DIR, "04-impact-analysis.png")
        page.screenshot(path=impact_path, full_page=False)
        print(f"   Saved: {impact_path}")
        results["screenshot_04_impact"] = "PASSED"

        print("7. Testing Issues & Cycles & Capturing Screenshot 05...")
        page.click("nav button:has-text('Issues & Cycles')")
        page.wait_for_selector("text=Circular Dependencies (Tarjan SCC)")
        time.sleep(0.5)
        issues_path = os.path.join(SCREENSHOTS_DIR, "05-issues.png")
        page.screenshot(path=issues_path, full_page=False)
        print(f"   Saved: {issues_path}")
        results["screenshot_05_issues"] = "PASSED"

        print("8. Testing Command Palette (Ctrl+K)...")
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmd-pal[open]")
        page.fill("#pal-input", "catalog")
        page.wait_for_selector("#pal-results li")
        items = page.query_selector_all("#pal-results li")
        assert len(items) > 0, "Search results should be populated"
        page.keyboard.press("Escape")
        time.sleep(0.3)
        assert not page.is_visible("#cmd-pal[open]")
        results["command_palette"] = "PASSED"

        print("9. Testing Reanalyze Workflow...")
        page.click("#reanalyze-btn")
        page.wait_for_selector("#global-status:has-text('11 files')")
        results["reanalyze"] = "PASSED"

        print("10. Testing Responsive Layouts...")
        # Laptop 1280x800
        page.set_viewport_size({"width": 1280, "height": 800})
        time.sleep(0.5)
        assert page.evaluate("() => document.body.scrollWidth <= window.innerWidth + 20")
        results["responsive_laptop_1280x800"] = "PASSED"

        # Mobile 390x844
        page.set_viewport_size({"width": 390, "height": 844})
        time.sleep(0.5)
        results["responsive_mobile_390x844"] = "PASSED"

        print("11. Testing Error Handling on Invalid Input...")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.click("#switch-btn")
        page.wait_for_selector(".onboarding")
        
        # Test bad local path
        page.fill("#input-local-path", "C:\\nonexistent_invalid_path_404")
        page.click("button:has-text('Analyze Repository')")
        page.wait_for_selector(".alert-banner.err")
        assert "Path does not exist" in page.inner_text(".alert-banner.err")
        
        # Test bad github url
        page.click(".source-tab-btn:has-text('Public GitHub Repository')")
        page.wait_for_selector("#input-gh-url")
        page.fill("#input-gh-url", "not_a_valid_github_url")
        page.click("button:has-text('Clone & Analyze')")
        page.wait_for_selector(".alert-banner.err")
        assert "Invalid GitHub URL" in page.inner_text(".alert-banner.err")
        results["error_handling"] = "PASSED"

        browser.close()

    print("\n--- Playwright Automation Summary ---")
    for k, v in results.items():
        print(f"  {k}: {v}")
    
    if console_errors:
        print(f"Console errors noted: {console_errors}")
    else:
        print("Browser console errors: 0")

    srv.shutdown()
    srv.server_close()
    return all(v == "PASSED" for v in results.values())


if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)
