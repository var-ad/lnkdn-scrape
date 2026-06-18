"""
scraper.py
Login to LinkedIn with Playwright, search for a query, scroll through posts,
and return raw post data (text + metadata) for further extraction.
"""

import time
import json
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config


# ── Selectors — verified May 2026 via debug_scraper.py ─────────────────────
# Each search result post card
POST_CONTAINER = "div._7b2ee40b"  # update selector

SESSION_FILE = ".linkedin_session.json"


def _normalize_post_date(raw_date: str, date_filter: str = "") -> str:
    """Return YYYY-MM-DD when LinkedIn gives datetime, relative labels, or nothing."""
    raw_date = (raw_date or "").strip()
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()

    if not raw_date:
        return today.isoformat() if date_filter == "past-24h" else ""

    if re.match(r"^\d{4}-\d{2}-\d{2}", raw_date):
        return raw_date[:10]

    text = raw_date.lower()
    match = re.search(r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)\b", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith(("s", "m", "h")):
            return today.isoformat()
        if unit.startswith("d"):
            return (today - timedelta(days=amount)).isoformat()
        if unit.startswith("w"):
            return (today - timedelta(weeks=amount)).isoformat()

    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
    if "today" in text or "now" in text:
        return today.isoformat()

    return raw_date


def _save_session(context):
    """Persist cookies/storage so we don't re-login every run."""
    storage = context.storage_state()
    with open(SESSION_FILE, "w") as f:
        json.dump(storage, f)


def _load_session_if_exists():
    """Return storage_state dict or None."""
    try:
        with open(SESSION_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _login(page):
    """Perform LinkedIn email/password login."""
    print("[scraper] Logging in to LinkedIn...")
    page.goto(
        "https://www.linkedin.com/login",
        wait_until="domcontentloaded",
        timeout=config.NAVIGATION_TIMEOUT,
    )
    page.fill("#username", config.LINKEDIN_EMAIL)
    page.fill("#password", config.LINKEDIN_PASSWORD)
    page.click("button[type='submit']")

    # Wait for feed or checkpoint
    try:
        page.wait_for_url("**/feed/**", timeout=config.LOGIN_WAIT_TIMEOUT // 4)
        print("[scraper] Login successful.")
    except PlaywrightTimeout:
        # Could be 2FA / captcha — pause for manual intervention
        print("[scraper] ⚠️  Login didn't reach feed. Manual action needed (2FA / CAPTCHA).")
        print("[scraper]    Waiting 2 mins for you to complete it in the browser window...")
        page.wait_for_url("**/feed/**", timeout=config.LOGIN_WAIT_TIMEOUT)
        print("[scraper] Continuing after manual step.")


def _build_search_url(query: str, date_filter: str) -> str:
    """
    LinkedIn content search URL.
    datePosted: past-24h | past-week
    """
    encoded = quote_plus(query)
    encoded_date_filter = quote_plus(json.dumps([date_filter]))
    return (
        f"https://www.linkedin.com/search/results/content/"
        f"?keywords={encoded}&datePosted={encoded_date_filter}&origin=FACETED_SEARCH"
    )


def _extract_posts_from_page(page, date_filter: str = "") -> list[dict]:
    posts = []

    # Use JS to find all post cards by "Feed post" text, but keep only the
    # smallest matching wrapper so parent containers do not duplicate posts.
    raw_cards = page.evaluate("""
        () => {
            const results = [];
            const seen = new Set();
            const candidates = Array.from(document.querySelectorAll('div._7b2ee40b'))
                .filter(el => el.innerText?.trim().startsWith('Feed post'))
                .filter(el => el.innerText?.trim().length > 100);

            // Sort by text length ascending — smallest = innermost card
            candidates.sort((a, b) => a.innerText.length - b.innerText.length);

            for (const el of candidates) {
                const text = el.innerText.trim();
                const key = text.slice(0, 80);
                if (seen.has(key)) continue;
                seen.add(key);

                let url = '';
                for (const a of el.querySelectorAll('a')) {
                    const href = a.getAttribute('href') || '';
                    if (href.includes('/posts/') || href.includes('activity')
                        || href.includes('ugcPost') || href.includes('/feed/update/')) {
                        url = href.split('?')[0];
                        break;
                    }
                }
                const timeEl = el.querySelector('time');
                const date = timeEl?.getAttribute('datetime') || timeEl?.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                results.push({
                    poster: lines[1] || '',
                    text: lines.slice(3).join(' '),
                    date: date,
                    url: url
                });
            }
            return results;
        }
    """)

    for card in (raw_cards or []):
        text = card.get("text", "")
        if len(text) <= 40:
            continue

        card_date = _normalize_post_date(card.get("date", ""), date_filter)
        if not card_date and date_filter == "past-24h":
            card_date = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()

        posts.append({
            "text": text,
            "poster": card.get("poster", ""),
            "date": card_date,
            "url": card.get("url", ""),
        })

    return posts


def scrape_query(query: str, date_filter: str = None) -> list[dict]:
    """
    Main entry point. Returns list of raw post dicts for one query.
    Each dict: { text, poster, date, url }
    """
    date_filter = date_filter or config.DATE_FILTER
    url = _build_search_url(query, date_filter)
    session = _load_session_if_exists()

    all_posts: list[dict] = []
    seen_texts: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=config.HEADLESS_BROWSER,
            slow_mo=0 if config.HEADLESS_BROWSER else 80,
        )

        context_opts = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 900},
        }
        if session:
            context_opts["storage_state"] = session

        context = browser.new_context(**context_opts)
        page = context.new_page()

        # ── Auth ────────────────────────────────────────────────────────────
        if not session:
            _login(page)
            _save_session(context)
        else:
            # Quick check: hit the feed and confirm we're logged in
            page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=config.NAVIGATION_TIMEOUT,
            )
            if "login" in page.url:
                print("[scraper] Session expired, re-logging in...")
                _login(page)
                _save_session(context)

        # ── Search ──────────────────────────────────────────────────────────
        print(f"[scraper] Searching: '{query}'")
        page.goto(url, wait_until="domcontentloaded", timeout=config.NAVIGATION_TIMEOUT)

        # Wait for at least one post card to render (up to 10s)
        try:
            page.wait_for_selector(POST_CONTAINER, timeout=config.SELECTOR_WAIT_TIMEOUT)
        except PlaywrightTimeout:
            print("[scraper]   No post cards found after waiting — check login / selectors.")

        time.sleep(2)

        for round_num in range(config.MAX_SCROLL_ROUNDS):
            new_posts = _extract_posts_from_page(page, date_filter)
            added = 0
            for p_data in new_posts:
                # Deduplicate by first 120 chars of text
                key = p_data["text"][:120]
                if key not in seen_texts:
                    seen_texts.add(key)
                    all_posts.append(p_data)
                    added += 1

            print(f"[scraper]   scroll {round_num+1}/{config.MAX_SCROLL_ROUNDS} — "
                  f"+{added} new posts (total: {len(all_posts)})")

            # Scroll the finite-scroll container when LinkedIn uses one.
            page.evaluate("""
                () => {
                    const scrollable = document.querySelector('div.scaffold-finite-scroll__content')
                                    || document.querySelector('main')
                                    || document.body;
                    scrollable.scrollTop = scrollable.scrollHeight;
                    window.scrollBy(0, 800);
                }
            """)
            time.sleep(config.SCROLL_DELAY + 1)

            # Check for "No more results"
            no_more = page.query_selector("div.search-no-results__container")
            if no_more:
                print("[scraper]   No more results.")
                break

        browser.close()

    print(f"[scraper] Done — {len(all_posts)} posts scraped for query '{query}'")
    return all_posts
