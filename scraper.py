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
POST_CONTAINER   = "div.feed-shared-update-v2"

# Post body text — LinkedIn wraps text in span[dir=ltr] inside the update
POST_TEXT_SEL    = "div.feed-shared-update-v2__description span[dir='ltr'], \
                    div.feed-shared-inline-show-more-text span[dir='ltr'], \
                    span.break-words span[dir='ltr']"

# Poster name — actor component
ACTOR_NAME_SEL   = "span.update-components-actor__name span[aria-hidden='true'], \
                    span.feed-shared-actor__name, \
                    a.update-components-actor__meta-link span[aria-hidden='true']"

# Post timestamp
POST_DATE_SEL    = "time, span.update-components-actor__sub-description time, \
                    a.update-components-actor__sub-description-link"

# Post permalink anchors
POST_LINK_PATTERNS = ["/posts/", "activity", "ugcPost", "feed-hashtag"]

SESSION_FILE = ".linkedin_session.json"


def _normalize_post_url(card, href: str = "") -> str:
    """Return a stable LinkedIn post URL for dedupe and sheet storage."""
    urn = card.get_attribute("data-urn") or ""
    match = re.search(r"urn:li:activity:\d+", href) or re.search(r"urn:li:activity:\d+", urn)
    if match:
        return f"/feed/update/{match.group(0)}/"

    if href:
        return href.split("?")[0]

    return ""


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
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    page.fill("#username", config.LINKEDIN_EMAIL)
    page.fill("#password", config.LINKEDIN_PASSWORD)
    page.click("button[type='submit']")

    # Wait for feed or checkpoint
    try:
        page.wait_for_url("**/feed/**", timeout=15_000)
        print("[scraper] Login successful.")
    except PlaywrightTimeout:
        # Could be 2FA / captcha — pause for manual intervention
        print("[scraper] ⚠️  Login didn't reach feed. Manual action needed (2FA / CAPTCHA).")
        print("[scraper]    Waiting 60s for you to complete it in the browser window...")
        page.wait_for_url("**/feed/**", timeout=60_000)
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
    """Extract all visible post cards from the current page state."""
    posts = []
    cards = page.query_selector_all(POST_CONTAINER)

    for card in cards:
        try:
            # ── Text ────────────────────────────────────────────────────────
            # Try specific selectors first; fall back to full card innerText
            text = ""
            for sel in POST_TEXT_SEL.split(","):
                sel = sel.strip()
                els = card.query_selector_all(sel)
                text = " ".join(el.inner_text() for el in els if el.inner_text().strip())
                if len(text) > 40:
                    break

            if len(text) < 40:
                # Fallback: grab all text inside the card and strip nav noise
                raw = card.inner_text()
                # Drop the first line ("Feed post") and nav-like short lines
                lines = [l.strip() for l in raw.splitlines()
                         if len(l.strip()) > 30]
                text = " ".join(lines)

            if len(text) < 40:
                continue

            # ── Poster name ─────────────────────────────────────────────────
            poster = ""
            for sel in ACTOR_NAME_SEL.split(","):
                el = card.query_selector(sel.strip())
                if el:
                    poster = el.inner_text().strip()
                    break
            # fallback: first line of innerText that isn't "Feed post"
            if not poster:
                for line in card.inner_text().splitlines():
                    line = line.strip()
                    if line and line != "Feed post" and len(line) > 2:
                        poster = line
                        break

            # ── Date ────────────────────────────────────────────────────────
            post_date = ""
            for sel in POST_DATE_SEL.split(","):
                el = card.query_selector(sel.strip())
                if el:
                    post_date = (el.get_attribute("datetime") or
                                 el.get_attribute("aria-label") or
                                 el.get_attribute("title") or
                                 el.inner_text().strip())
                    break
            post_date = _normalize_post_date(post_date, date_filter)

            # ── Post URL ────────────────────────────────────────────────────
            link = ""
            anchors = card.query_selector_all("a")
            for a in anchors:
                href = a.get_attribute("href") or ""
                if any(p in href for p in POST_LINK_PATTERNS):
                    link = _normalize_post_url(card, href)
                    break

            if not link:
                link = _normalize_post_url(card)

            posts.append({
                "text": text,
                "poster": poster,
                "date": post_date,
                "url": link,
            })

        except Exception as e:
            print(f"[scraper]   card parse error: {e}")
            continue

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
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            if "login" in page.url:
                print("[scraper] Session expired, re-logging in...")
                _login(page)
                _save_session(context)

        # ── Search ──────────────────────────────────────────────────────────
        print(f"[scraper] Searching: '{query}'")
        page.goto(url, wait_until="domcontentloaded")

        # Wait for at least one post card to render (up to 10s)
        try:
            page.wait_for_selector(POST_CONTAINER, timeout=10_000)
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

            # Scroll to bottom
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(config.SCROLL_DELAY)

            # Check for "No more results"
            no_more = page.query_selector("div.search-no-results__container")
            if no_more:
                print("[scraper]   No more results.")
                break

        browser.close()

    print(f"[scraper] Done — {len(all_posts)} posts scraped for query '{query}'")
    return all_posts
