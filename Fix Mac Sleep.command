#!/bin/bash
# One-time fix: stop this Mac from sleeping while plugged in, so the
# scheduled scans (daily 6pm full scan + every-30-min BIN sweeps) actually
# run. On 2026-07-17 the Mac slept from 1pm-8pm and missed 7 hours of
# sweeps - sleeping Macs don't run scheduled jobs at all.
#
# You'll be asked for your Mac login password. That's macOS's own "sudo"
# asking, in your own Terminal - the password goes to macOS, nowhere else.

echo "Setting 'never sleep while plugged in' (display can still turn off)..."
if sudo pmset -c sleep 0; then
    echo
    echo "Done. Current plugged-in power settings:"
    pmset -g custom | sed -n '/AC Power/,/^$/p'
    echo
    echo "Your Mac now stays awake for scans whenever it's on the charger."
    echo "On battery, nothing changes (sweeps may still be missed unplugged)."
else
    echo "Couldn't change the setting (wrong password?). Nothing was changed."
fi
read -r -p "Press Enter to close..."
