#!/bin/bash
# Fast Buy-It-Now sweep (priority queries). Output lands in "reports/BIN runs".
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Run 'Run Scan.command' once first to set up."
    read -r -p "Press Enter to close..."
    exit 1
fi

mkdir -p "reports/BIN runs"
OUT="$(pwd)/reports/BIN runs/bin_sweep_$(date +%Y-%m-%d_%H.%M).xlsx"
caffeinate -im .venv/bin/python code/main.py --mode bin -o "$OUT"
RC=$?
if [ $RC -eq 0 ]; then
    if [ -t 0 ]; then open "$OUT"; fi
elif [ $RC -eq 2 ]; then
    echo "TEST RUN FAILED - sweep stopped before it started. Run 'Run Tests.command'."
else
    echo "No BIN opportunities this sweep (or an error - see above)."
fi
# keep only the 30 newest sweep files so the folder doesn't fill up
cd "reports/BIN runs" && ls -t bin_sweep_*.xlsx 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null
if [ -t 0 ]; then read -r -p "Press Enter to close..."; fi
