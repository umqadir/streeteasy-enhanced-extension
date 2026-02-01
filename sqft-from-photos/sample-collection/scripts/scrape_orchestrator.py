#!/usr/bin/env python3
"""
Orchestrator for StreetEasy scraping via Claude-in-Chrome MCP.

This script manages the scraping workflow:
1. Load/save URL queues
2. Track progress
3. Manage delays and session breaks

Usage:
    # Add URLs to queue
    python scrape_orchestrator.py add-urls urls.txt

    # Show queue status
    python scrape_orchestrator.py status

    # Get next URL to scrape (with delay enforcement)
    python scrape_orchestrator.py next

    # Mark URL as completed with data
    python scrape_orchestrator.py complete <url> --data '{"sqft": 500, ...}'

    # Mark URL as failed
    python scrape_orchestrator.py fail <url> --reason "blocked"
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
QUEUE_FILE = DATA_DIR / "scrape_queue.json"
DATASET_FILE = DATA_DIR / "streeteasy_examples_20.json"

# Delay settings
MIN_DELAY_SECONDS = 15
MAX_DELAY_SECONDS = 35
SESSION_BREAK_EVERY = 20  # Suggest new session every N listings


def load_queue() -> dict:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return {
        "pending": [],
        "completed": [],
        "failed": [],
        "last_scrape_time": None,
        "session_count": 0,
    }


def save_queue(queue: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def load_dataset() -> dict:
    if DATASET_FILE.exists():
        return json.loads(DATASET_FILE.read_text())
    return {
        "source": "streeteasy",
        "collectedAt": datetime.now(timezone.utc).isoformat(),
        "photoUrlTemplate": "https://photos.zillowstatic.com/fp/{id}-full.jpg",
        "maxPhotoIdsPerListing": 30,
        "examples": [],
    }


def save_dataset(dataset: dict):
    dataset["collectedAt"] = datetime.now(timezone.utc).isoformat()
    DATASET_FILE.write_text(json.dumps(dataset, indent=2))


def add_urls(urls: list[str]):
    queue = load_queue()
    existing = set(queue["pending"] + queue["completed"] + queue["failed"])

    added = 0
    for url in urls:
        url = url.strip()
        if url and url.startswith("https://streeteasy.com/") and url not in existing:
            queue["pending"].append(url)
            added += 1

    save_queue(queue)
    print(f"Added {added} new URLs to queue ({len(urls) - added} duplicates skipped)")


def show_status():
    queue = load_queue()
    dataset = load_dataset()

    print("=== Scrape Queue Status ===")
    print(f"Pending:   {len(queue['pending'])}")
    print(f"Completed: {len(queue['completed'])}")
    print(f"Failed:    {len(queue['failed'])}")
    print(f"Session:   {queue['session_count']} listings since break")
    print()
    print(f"=== Dataset Status ===")
    print(f"Total examples: {len(dataset['examples'])}")

    with_sqft = sum(1 for ex in dataset["examples"] if ex.get("sqft") is not None)
    without_sqft = len(dataset["examples"]) - with_sqft
    print(f"With sqft:    {with_sqft}")
    print(f"Without sqft: {without_sqft}")

    if queue["last_scrape_time"]:
        elapsed = time.time() - queue["last_scrape_time"]
        print(f"\nLast scrape: {elapsed:.0f}s ago")


def get_next():
    queue = load_queue()

    if not queue["pending"]:
        print("No URLs in queue. Add more with: scrape_orchestrator.py add-urls")
        return

    # Check session break
    if queue["session_count"] >= SESSION_BREAK_EVERY:
        print("=== SESSION BREAK RECOMMENDED ===")
        print(f"You've scraped {queue['session_count']} listings.")
        print("Open a new incognito window to avoid detection.")
        print("Then run 'next' again to continue.")
        queue["session_count"] = 0
        save_queue(queue)
        return

    # Check delay
    if queue["last_scrape_time"]:
        elapsed = time.time() - queue["last_scrape_time"]
        delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        if elapsed < delay:
            wait = delay - elapsed
            print(f"Waiting {wait:.0f}s before next scrape (anti-detection)...")
            time.sleep(wait)

    url = queue["pending"][0]
    print(f"Next URL: {url}")
    print()
    print("After visiting and extracting, run:")
    print(f"  scrape_orchestrator.py complete '{url}' --data '<json>'")
    print("Or if blocked:")
    print(f"  scrape_orchestrator.py fail '{url}' --reason 'blocked'")


def complete_url(url: str, data_json: str):
    queue = load_queue()
    dataset = load_dataset()

    if url not in queue["pending"]:
        print(f"URL not in pending queue: {url}")
        return

    # Parse and validate data
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}")
        return

    # Check for duplicate in dataset
    existing_urls = {ex["listingUrl"] for ex in dataset["examples"]}
    if url in existing_urls:
        print(f"URL already in dataset, skipping: {url}")
    else:
        # Add to dataset
        example = {
            "listingUrl": data.get("listingUrl", url),
            "title": data.get("title", ""),
            "sqft": data.get("sqft"),
            "sqftText": data.get("sqftText"),
            "photoIdCountDetected": data.get("photoIdCountDetected", 0),
            "photoIdCountUsed": data.get("photoIdCountUsed", 0),
            "photoIds": data.get("photoIds", []),
        }
        dataset["examples"].append(example)
        save_dataset(dataset)
        print(f"Added to dataset: {url}")
        print(f"  sqft: {example['sqft']}, photos: {example['photoIdCountUsed']}")

    # Update queue
    queue["pending"].remove(url)
    queue["completed"].append(url)
    queue["last_scrape_time"] = time.time()
    queue["session_count"] += 1
    save_queue(queue)

    print(f"\nProgress: {len(queue['completed'])} completed, {len(queue['pending'])} remaining")


def fail_url(url: str, reason: str):
    queue = load_queue()

    if url not in queue["pending"]:
        print(f"URL not in pending queue: {url}")
        return

    queue["pending"].remove(url)
    queue["failed"].append({"url": url, "reason": reason, "time": time.time()})
    save_queue(queue)

    print(f"Marked as failed: {url} ({reason})")
    print(f"Remaining: {len(queue['pending'])} pending")


def reset_failed():
    """Move failed URLs back to pending for retry."""
    queue = load_queue()
    for item in queue["failed"]:
        url = item["url"] if isinstance(item, dict) else item
        if url not in queue["pending"]:
            queue["pending"].append(url)
    count = len(queue["failed"])
    queue["failed"] = []
    save_queue(queue)
    print(f"Moved {count} failed URLs back to pending")


def main():
    parser = argparse.ArgumentParser(description="StreetEasy scraping orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add-urls
    add_parser = subparsers.add_parser("add-urls", help="Add URLs to queue")
    add_parser.add_argument("file", help="File with URLs (one per line) or - for stdin")

    # status
    subparsers.add_parser("status", help="Show queue and dataset status")

    # next
    subparsers.add_parser("next", help="Get next URL to scrape")

    # complete
    complete_parser = subparsers.add_parser("complete", help="Mark URL as completed")
    complete_parser.add_argument("url", help="The URL that was scraped")
    complete_parser.add_argument("--data", required=True, help="JSON data extracted")

    # fail
    fail_parser = subparsers.add_parser("fail", help="Mark URL as failed")
    fail_parser.add_argument("url", help="The URL that failed")
    fail_parser.add_argument("--reason", default="unknown", help="Reason for failure")

    # reset-failed
    subparsers.add_parser("reset-failed", help="Move failed URLs back to pending")

    args = parser.parse_args()

    if args.command == "add-urls":
        if args.file == "-":
            urls = sys.stdin.read().strip().split("\n")
        else:
            urls = Path(args.file).read_text().strip().split("\n")
        add_urls(urls)
    elif args.command == "status":
        show_status()
    elif args.command == "next":
        get_next()
    elif args.command == "complete":
        complete_url(args.url, args.data)
    elif args.command == "fail":
        fail_url(args.url, args.reason)
    elif args.command == "reset-failed":
        reset_failed()


if __name__ == "__main__":
    main()
