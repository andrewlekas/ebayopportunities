#!/bin/bash
# Proves the Telegram alert pipeline works: sends a TEST message through the
# exact same code the scanner uses, and shows recent alert activity from the
# log. Safe to run anytime - the test is clearly labeled and nothing is
# recorded in the alert-dedup table.
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Run 'Run Scan.command' once first to set up."
    read -r -p "Press Enter to close..."
    exit 1
fi

.venv/bin/python - <<'EOF'
import sys
sys.path.insert(0, "code")
import yaml
from models import Listing, Valuation, Opportunity
from alerts import _send_telegram

config = yaml.safe_load(open("config.yaml"))
tg = (config.get("alerts", {}).get("telegram") or {})
if not (tg.get("bot_token") and tg.get("chat_id")):
    print("Telegram is NOT configured (missing bot_token/chat_id in config.yaml)")
    sys.exit(1)

test = Opportunity(
    listing=Listing(site="ebay",
                    title="TEST ALERT (ignore) - alert pipeline check",
                    url="https://ebay.com", current_price=1000.0,
                    listing_type="fixed"),
    valuation=Valuation(fair_value=1500.0, edge_now=395.0,
                        opportunity_score=0.42))

print("Sending test message through the real alert code path...")
ok = _send_telegram([test], tg)
print()
if ok:
    print("SUCCESS - check your Telegram. Delivery works end-to-end.")
    print("If real alerts still don't arrive, the gates are filtering them")
    print("(see logs/scan.log after the next run for the per-run gate summary).")
else:
    print("FAILED - Telegram did not accept the message.")
    print("Check the warning above: wrong chat_id, revoked bot token, or no")
    print("internet. Fix config.yaml alerts.telegram and run this again.")

# recent alert activity from the log, if it exists yet
try:
    lines = [l.rstrip() for l in open("logs/scan.log", encoding="utf-8")
             if "alert" in l.lower()]
    if lines:
        print("\n--- recent alert activity (logs/scan.log) ---")
        for l in lines[-12:]:
            print(l)
    else:
        print("\n(logs/scan.log has no alert lines yet - it starts filling on the "
              "next scheduled run)")
except FileNotFoundError:
    print("\n(logs/scan.log will be created by the next scheduled run)")
EOF

read -r -p "Press Enter to close..."
