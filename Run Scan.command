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

# Are we in front of a human? Double-clicking in Finder, or running from a
# terminal, should pop the report open. A cron run should not. Checking only
# stdin missed cases where Terminal hands the script a non-tty stdin, which
# is why the report quietly stopped appearing.
INTERACTIVE=0
if [ -t 0 ] || [ -t 1 ] || [ -t 2 ] || [ -n "$TERM_PROGRAM" ]; then
    INTERACTIVE=1
fi

# Show the workbook whenever one exists. main.py writes the report BEFORE it
# decides its exit code, so an "exit 1" run - meaning nothing was actionable
# this time - still produces a complete, readable workbook. Gating the open
# on exit 0 hid those reports entirely.
if [ -f "$OUT" ]; then
    echo "Done! Report saved in reports/Opp Runs."
    if [ $RC -ne 0 ]; then
        echo "(Nothing met the action thresholds this run - the workbook"
        echo " still has every research row and the Source Health tab.)"
    fi
    if [ "$INTERACTIVE" -eq 1 ]; then
        open "$OUT"
    fi
elif [ $RC -eq 2 ]; then
    echo
    echo "TEST RUN FAILED - the scan was stopped before it started."
    echo "Nothing was scanned and no report was written."
    echo "Double-click 'Run Tests.command' to see which check failed."
else
    echo
    echo "No workbook was written - see the messages above, or open"
    echo "logs/scan.log. A missing report usually means a source failed"
    echo "or a breaker was still cooling, not that there were no bargains."
fi
if [ "$INTERACTIVE" -eq 1 ]; then read -r -p "Press Enter to close..."; fi
