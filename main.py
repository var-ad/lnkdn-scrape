"""
main.py
Orchestrates the LinkedIn job scraping pipeline.

Usage:
    python main.py                   # uses DATE_FILTER from .env
    python main.py --date past-24h   # override date filter
    python main.py --dry-run         # scrape + extract but don't write to Sheets
"""

import argparse
import time

import config
from scraper import scrape_query
from extractor import extract_jobs_data
from sheets import append_jobs


def run(date_filter: str, dry_run: bool = False):
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for i, query in enumerate(config.SEARCH_QUERIES):
        print(f"\n{'='*60}")
        print(f"Query {i+1}/{len(config.SEARCH_QUERIES)}: \"{query}\"")
        print('='*60)

        raw_posts = scrape_query(query, date_filter)
        query_jobs = extract_jobs_data(raw_posts)

        for job in query_jobs:
            # Deduplicate across queries by URL
            url = job.get("LinkedIn Post URL", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            all_jobs.append(job)
            print(f"  ✓ {job['Company'] or '(unknown company)'} — {job['Job Title']} — {job['Location']}")

        # Polite inter-query delay to reduce LinkedIn rate-limit risk
        if i < len(config.SEARCH_QUERIES) - 1:
            print("[main] Sleeping 8s before next query...")
            time.sleep(8)

    print(f"\n{'='*60}")
    print(f"Total jobs found: {len(all_jobs)}")
    print('='*60)

    if dry_run:
        print("[main] --dry-run: skipping Sheets write.")
        for j in all_jobs:
            print(f"  {j['Post Date']} | {j['Company']} | {j['Job Title']} | {j['Location']} | {j['Salary / Stipend']}")
    else:
        written = append_jobs(all_jobs)
        print(f"[main] Done. {written} new rows written to Google Sheets.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        choices=["past-24h", "past-week"],
        default=config.DATE_FILTER,
        help="LinkedIn date filter"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without writing to Google Sheets"
    )
    args = parser.parse_args()

    run(date_filter=args.date, dry_run=args.dry_run)
