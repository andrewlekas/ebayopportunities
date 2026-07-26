#!/bin/bash
# Double-click to check the scanner's own safety rules still hold.
#
# These tests are pinned to real rows and real numbers from your data - a
# failure means a bug we already fixed has come back, not that something
# cosmetic changed. Nothing here touches the network, the database or your
# reports; it is always safe to run.
#
# Results are also written to "test results/test_results.log".
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

{
    echo "=============================================================="
    echo "Card scanner self-check - $(date '+%Y-%m-%d %H:%M:%S')"
    echo "=============================================================="
    echo

    echo "--- config sanity (keys present, settings readable) ---"
    "$PY" - <<'EOF'
import sys
try:
    import yaml
    c = yaml.safe_load(open("config.yaml"))
    assert c["api_keys"]["ebay"]["client_id"], "eBay client_id missing"
    assert c["api_keys"]["ebay"]["client_secret"], "eBay client_secret missing"
    assert c["api_keys"]["pricecharting"]["token"], "PriceCharting token missing"
    assert c["alerts"]["telegram"]["bot_token"], "Telegram bot_token missing"
    assert c["alerts"]["telegram"]["chat_id"], "Telegram chat_id missing"
    print("  API keys and Telegram settings: present")
    print("  watchlist entries: %d   grails: %d   sites: %s"
          % (len(c["watchlist"]), len(c["grails"]), c["sites"]))
    print("  pokemon grade floor: PSA %s" % c["filters"]["pokemon_grade_floor"])
    print("  auction settle ratio (config): %s"
          % c["algorithm"]["auction_settle_ratio"])
except Exception as e:
    print("  PROBLEM: %s" % e)
    sys.exit(1)
EOF
    CONFIG_RC=$?
    echo

    echo "--- what the close model is currently using ---"
    "$PY" - <<'EOF'
import json
try:
    p = json.load(open("model/learned_params.json"))
except Exception:
    print("  no learned_params.json yet - using the config settle ratio")
else:
    n = p.get("n", 0)
    if n >= 20:
        print("  LEARNED from %d closed auctions: settle ratio %.3f "
              "(avg error %.3f)" % (n, p.get("settle_ratio", 0),
                                    p.get("parametric_mae", 0)))
        if p.get("settle_bands"):
            print("  price bands: %s" % p["settle_bands"])
    else:
        print("  cold start (%d/20 trustworthy closes) - using the "
              "hand-tuned settle ratio from config.yaml" % n)
    ml = p.get("ml") or {}
    print("  machine-learned close model deployed: %s%s"
          % (ml.get("deployed", False),
             "  (%s)" % ml["benched_why"] if ml.get("benched_why") else ""))
    tf = p.get("training_filter")
    if tf:
        print("  training filter: %s"
              % ", ".join("%s=%s" % kv for kv in sorted(tf.items())))
EOF
    echo

    echo "--- unit tests ---"
    "$PY" -m unittest discover -s code -p "test_*.py" -v 2>&1
    TEST_RC=$?
    echo

    echo "--- end-to-end pipeline check (synthetic data, no network) ---"
    "$PY" code/main.py --demo -o /tmp/scanner_selfcheck.xlsx 2>&1 | tail -4
    echo

    if [ "$TEST_RC" -eq 0 ] && [ "$CONFIG_RC" -eq 0 ]; then
        echo "RESULT: everything passed."
    else
        echo "RESULT: SOMETHING FAILED - see the output above."
    fi
} 2>&1 | tee "test results/test_results.log"

echo
read -r -p "Press Enter to close..."
