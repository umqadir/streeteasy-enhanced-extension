#!/bin/bash
# Quick batch collection script
# Run this to collect remaining listings

cd "$(dirname "$0")/.."

CURRENT=$(python3 -c "import json; print(len(json.load(open('data/streeteasy_examples_20.json'))['examples']))")
echo "Current: $CURRENT/200"
echo "Need: $((200-CURRENT)) more"
echo ""
echo "URLs to process:"
tail -n +$((CURRENT+1)) data/listing_urls.txt | head -20
echo ""
echo "Press ENTER to start manual collection, or Ctrl+C to cancel"
read

echo "Navigate to each URL above and run the extraction JS in DevTools console"
echo "Then paste the result into: data/current_extraction.json"
echo "This script will watch for changes and save them automatically"
