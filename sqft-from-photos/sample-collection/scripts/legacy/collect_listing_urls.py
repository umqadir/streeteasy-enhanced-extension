#!/usr/bin/env python3
"""
Collect StreetEasy listing URLs from search results pages.
Uses direct HTTP requests with browser-like headers to avoid blocking.
"""

import re
import time
import random
import json
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent.parent / "data"
URLS_FILE = DATA_DIR / "listing_urls.txt"

# Browser-like headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def extract_listing_urls(html: str) -> list[str]:
    """Extract listing URLs from search results HTML."""
    # Pattern for listing URLs like /building/name/unit
    pattern = r'href="(https://streeteasy\.com/building/[^"?]+/[^"?]+)"'
    matches = re.findall(pattern, html)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for url in matches:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def collect_urls_from_pages(base_url: str, start_page: int = 1, end_page: int = 20) -> list[str]:
    """Collect listing URLs from multiple search result pages."""
    all_urls = []

    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        for page in range(start_page, end_page + 1):
            url = f"{base_url}?page={page}"
            print(f"Fetching page {page}... ", end="", flush=True)

            try:
                response = client.get(url)

                if response.status_code == 403:
                    print("BLOCKED (403)")
                    print("Try using browser automation instead.")
                    break
                elif response.status_code != 200:
                    print(f"ERROR ({response.status_code})")
                    continue

                urls = extract_listing_urls(response.text)
                all_urls.extend(urls)
                print(f"found {len(urls)} listings (total: {len(all_urls)})")

                # Random delay between requests
                delay = random.uniform(2, 5)
                time.sleep(delay)

            except Exception as e:
                print(f"ERROR: {e}")
                continue

    # Deduplicate final list
    return list(dict.fromkeys(all_urls))


def main():
    # NYC rentals search
    base_url = "https://streeteasy.com/for-rent/nyc/area:100,200,300,400,500"

    print("Collecting StreetEasy listing URLs...")
    print(f"Base URL: {base_url}")
    print()

    urls = collect_urls_from_pages(base_url, start_page=1, end_page=20)

    print()
    print(f"Total unique URLs collected: {len(urls)}")

    # Save to file
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with URLS_FILE.open("w") as f:
        for url in urls:
            f.write(url + "\n")

    print(f"Saved to: {URLS_FILE}")

    # Also output as JSON for inspection
    print(json.dumps({"count": len(urls), "sample": urls[:10]}, indent=2))


if __name__ == "__main__":
    main()
