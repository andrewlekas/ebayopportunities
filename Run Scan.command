#!/bin/bash
# Double-click to run the full scan. Report lands in "reports/Opp Runs".
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"
    read -r -p "Press Enter to close..."
    exit 1
fi

if [ ! -d .venv ]; then
    echo "First-time setup: installing dependencies..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
fi
# keep dependencies in sync with setup/requirements.txt (fast when already installed)
.venv/bin/pip install --quiet -r setup/requirements.txt

mkdir -p "reports/Opp Runs"
OUT="$(pwd)/reports/Opp Runs/opportunities_$(date +%Y-%m-%d_%H.%M).xlsx"
echo "Scanning (typically a few minutes)..."
caffeinate -im .venv/bin/python code/main.py -o "$OUT"
RC=$?
if [ $RC -eq 0 ]; then
    echo "Done! Report saved in reports/Opp Runs."
    if [ -t 0 ]; then open "$OUT"; fi
elif [ $RC -eq 2 ]; then
    echo
    echo "TEST RUN FAILED - the scan was stopped before it started."
    echo "Nothing was scanned and no report was written."
    echo "Double-click 'Run Tests.command' to see which check failed."
else
    echo "Scan finished with an error - see messages above."
fi
if [ -t 0 ]; then read -r -p "Press Enter to close..."; fi
