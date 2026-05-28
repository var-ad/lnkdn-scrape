"""
debug_scraper.py
Opens LinkedIn content search, saves a screenshot + full HTML so we can
identify the real CSS selectors LinkedIn is currently using.

Run: python debug_scraper.py
Output: debug_screenshot.png, debug_page.html
"""

import json, time
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

import config

SESSION_FILE = ".linkedin_session.json"
QUERY = "is hiring fresher"
DATE_FILTER = "past-week"


def main():
    session = None
    try:
        with open(SESSION_FILE) as f:
            session = json.load(f)
    except FileNotFoundError:
        pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        ctx_opts = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 900},
        }
        if session:
            ctx_opts["storage_state"] = session

        context = browser.new_context(**ctx_opts)
        page = context.new_page()

        # ── Login if needed ────────────────────────────────────────────────
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        time.sleep(2)
        if "login" in page.url or "authwall" in page.url:
            print("Logging in...")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            page.fill("#username", config.LINKEDIN_EMAIL)
            page.fill("#password", config.LINKEDIN_PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_url("**/feed/**", timeout=60_000)
            storage = context.storage_state()
            with open(SESSION_FILE, "w") as f:
                json.dump(storage, f)
            print("Logged in and session saved.")

        # ── Go to content search ───────────────────────────────────────────
        url = (
            f"https://www.linkedin.com/search/results/content/"
            f"?keywords={quote_plus(QUERY)}&datePosted={DATE_FILTER}"
        )
        print(f"Navigating to: {url}")
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(4)  # let JS render

        # Scroll once to trigger lazy load
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(2)

        # ── Save screenshot ────────────────────────────────────────────────
        page.screenshot(path="debug_screenshot.png", full_page=False)
        print("Saved: debug_screenshot.png")

        # ── Save HTML ──────────────────────────────────────────────────────
        html = page.content()
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved: debug_page.html ({len(html):,} bytes)")

        # ── Quick selector probe ───────────────────────────────────────────
        CANDIDATES = {
            # Old selector (was in scraper.py)
            "OLD: data-view-name template":
                "div[data-view-name='search-entity-result-universal-template']",
            # Common feed post containers
            "feed-shared-update-v2":
                "div.feed-shared-update-v2",
            "reusable-search result container":
                "li.reusable-search__result-container",
            "search result item":
                "div.search-result__occluded-item",
            # Generic data attributes LinkedIn uses
            "data-urn":
                "[data-urn]",
            "artdeco-card":
                "div.artdeco-card",
            # New unified feed item
            "scaffold-finite-scroll item":
                "li.scaffold-finite-scroll__content > div",
        }

        print("\n── Selector probe ──────────────────────────────────────")
        for label, sel in CANDIDATES.items():
            count = len(page.query_selector_all(sel))
            print(f"  {count:3d}  {label}")
            if count > 0:
                # Print first element's outer HTML (first 300 chars) for inspection
                el = page.query_selector(sel)
                outer = (el.evaluate("e => e.outerHTML") or "")[:300]
                print(f"         → {outer!r}")

        print("\n── All unique class names on potential post cards ──────")
        # Look for elements that contain long text (likely post bodies)
        candidates = page.evaluate("""
            () => {
                const els = document.querySelectorAll('span, p, div');
                const seen = new Set();
                const results = [];
                for (const el of els) {
                    if (el.innerText && el.innerText.trim().length > 100) {
                        const cls = el.className;
                        if (cls && !seen.has(cls)) {
                            seen.add(cls);
                            results.push({
                                tag: el.tagName,
                                cls: cls,
                                text_preview: el.innerText.trim().slice(0, 80)
                            });
                        }
                        if (results.length >= 20) break;
                    }
                }
                return results;
            }
        """)
        for c in candidates:
            print(f"  <{c['tag']} class='{c['cls']}'> → {c['text_preview']!r}")

        input("\nPress Enter to close the browser...")
        browser.close()


if __name__ == "__main__":
    main()