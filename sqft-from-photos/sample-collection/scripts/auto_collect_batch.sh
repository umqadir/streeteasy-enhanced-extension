#!/bin/bash
# Automated batch collection - to be run with browser automation
# Extracts data from current page and saves to JSON file

cd "$(dirname "$0")"

# Output file for extracted data (to be processed by Python)
OUTPUT_FILE="../data/browser_extraction_temp.json"

# Get URL, title, hasDashFt, and photo chunk data from stdin (JSON)
cat > "$OUTPUT_FILE"

echo "Data saved to $OUTPUT_FILE"
echo "Run process_extraction.py to save to dataset"
