#!/bin/bash
# Clear the scanner's own cooldown timers so it will try a source again.
#
# WHAT THIS IS
# When a source serves bot-challenge pages or errors three times in a row,
# the scanner stops contacting it for a while - 30 minutes at first, doubling
# each time it happens again, up to 24 hours. That timer is OURS. It is not a
# block imposed by eBay.
#
# WHAT CLEARING IT DOES AND DOESN'T DO
# It does NOT unblock anything. It only means the next scan will try again.
# If the site is still challenging us, we take three more strikes and the
# next cooldown is LONGER than the one you just cleared. Clearing repeatedly
# while a site is genuinely refusing us is how a few hours becomes a day.
#
# Worth knowing: since 2026-07-26 the price guide leads valuation, so the
# eBay sold-comp lane is corroboration rather than the main source. There is
# less to gain from forcing it than there used to be.
cd "$(dirname "$0")" || exit 1

STATE="state/.breaker_state.json"
if [ ! -f "$STATE" ]; then
  echo "No breaker state file - nothing is cooling down."
  read -r -p "Press return to close..." _
  exit 0
fi

echo "=================================================================="
echo " Source cooldowns"
echo "=================================================================="
echo
.venv/bin/python - "$STATE" <<'PY'
import json, sys
from datetime import datetime, timezone
state = json.load(open(sys.argv[1]))
now = datetime.now(timezone.utc)
if not state:
    print("  nothing recorded")
for lane, v in sorted(state.items()):
    try:
        until = datetime.fromisoformat(str(v.get("until", "")).replace("Z", "+00:00"))
        mins = (until - now).total_seconds() / 60
    except Exception:
        mins = 0
    strikes = v.get("strikes", 0)
    if mins > 0:
        hrs = mins / 60
        print(f"  {lane:18s} COOLING for another {hrs:5.1f}h   ({strikes} strikes)")
    else:
        print(f"  {lane:18s} clear                        ({strikes} strikes)")
PY
echo
echo "Choose what to clear:"
echo "  1) eBay sold-comp lane only  (ebay/html - the usual one)"
echo "  2) Everything"
echo "  3) Cancel"
printf 'Enter 1, 2 or 3: '
read -r CHOICE

case "$CHOICE" in
  1) LANES="ebay/html" ;;
  2) LANES="__all__" ;;
  *) echo "Cancelled. Nothing changed."; read -r -p "Press return to close..." _; exit 0 ;;
esac

cp "$STATE" "state/.breaker_state.backup-$(date +%Y%m%d-%H%M%S).json"

.venv/bin/python - "$STATE" "$LANES" <<'PY'
import json, sys
path, lanes = sys.argv[1], sys.argv[2]
state = json.load(open(path))
if lanes == "__all__":
    removed = list(state)
    state = {}
else:
    removed = [l for l in lanes.split(",") if l in state]
    for l in removed:
        state.pop(l, None)
json.dump(state, open(path, "w"), indent=1)
print("  cleared: " + (", ".join(removed) if removed else "nothing was cooling"))
PY

echo
echo "Done. The next scan will try those sources again."
echo "A backup of the old state is in state/ if you want it back."
echo
echo "If the source is still refusing us, the scanner will re-trip after"
echo "three failures and the next cooldown will be longer. Watch for"
echo "'cooling off after earlier failures' in logs/scan.log."
echo
read -r -p "Press return to close..." _
