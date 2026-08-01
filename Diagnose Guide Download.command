#!/bin/bash
# Double-click when a price-guide CSV download is refused and you want to
# know WHY. Makes three test requests to the refusing host, and the same
# three to PriceCharting as a control, so the two are compared under
# identical conditions instead of from memory.
#
# Safe to run any time: it downloads nothing and changes nothing. Your
# token is redacted in the output, so the whole thing is safe to paste.
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Run 'Run Scan.command' once first to set up dependencies."
    read -r -p "Press Enter to close..."
    exit 1
fi

.venv/bin/python -B code/fetch_guide_csv.py --diagnose 2>&1 | grep -v NotOpenSSLWarning

echo
read -r -p "Press Enter to close..."
