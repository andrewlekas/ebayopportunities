#!/bin/bash
# Change how often the BIN sweep runs.
#
#   Set BIN Schedule.command            -> every 2 hours (the default)
#   Set BIN Schedule.command 1          -> every hour
#   Set BIN Schedule.command 30m        -> every 30 minutes (the old setting)
#
# It backs up your crontab first, shows you the exact before/after, and does
# nothing until you type "yes". Nothing else in your crontab is touched.
cd "$(dirname "$0")" || exit 1

HOURS="${1:-2}"

# The daily full scan runs on the hour. A sweep that starts at the same
# instant gets SIGTERMed mid-work by the full scan taking the lock, which
# wastes whatever it had already fetched. Offsetting the sweep off the hour
# means it either runs cleanly or finds the lock held and skips politely.
OFFSET=30
if crontab -l 2>/dev/null | grep -Eqv 'sweep|mode[^A-Za-z]*bin' \
   && crontab -l 2>/dev/null | grep -E 'Run Scan|main\.py' \
      | grep -Eq '^[[:space:]]*30[[:space:]]'; then
  OFFSET=15    # the full scan already uses :30, so step aside
fi

case "$HOURS" in
  30m|30) SCHED="*/30 * * * *"; HUMAN="every 30 minutes" ;;
  1)      SCHED="$OFFSET * * * *";       HUMAN="every hour at :$OFFSET" ;;
  *)      SCHED="$OFFSET */$HOURS * * *"
          HUMAN="every $HOURS hours at :$OFFSET" ;;
esac

STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p state
BACKUP="state/crontab-backup-$STAMP.txt"

echo "=================================================================="
echo " BIN sweep schedule -> $HUMAN"
echo "=================================================================="
echo

if ! crontab -l >"$BACKUP" 2>/dev/null; then
  echo "You have no crontab entries at all."
  echo
  echo "That means the BIN sweep is probably scheduled another way. Check:"
  echo "  ls ~/Library/LaunchAgents/ | grep -i -E 'scan|bin|ebay|card'"
  echo
  echo "If you find a .plist there, tell Claude the filename and it can"
  echo "give you the exact edit. Nothing has been changed."
  rm -f "$BACKUP"
  exit 1
fi

echo "Backed up your current crontab to:"
echo "  $BACKUP"
echo
echo "--- CURRENT ---"
cat "$BACKUP"
echo "---------------"
echo

# Match the BIN sweep however it is invoked: by .command file or by --mode bin
# Crontab escapes spaces as "Run\ BIN\ Sweep", so match on tokens rather
# than on a literal phrase.
MATCH='[Ss]weep|mode[^A-Za-z]*bin'
if ! grep -Eq "$MATCH" "$BACKUP"; then
  echo "No BIN sweep line found in your crontab."
  echo "Lines are matched on: 'Run BIN Sweep', '--mode bin' or 'mode bin'."
  echo "Nothing has been changed - tell Claude what your crontab looks like."
  exit 1
fi

echo "These lines will be rescheduled to \"$SCHED\" ($HUMAN):"
grep -E "$MATCH" "$BACKUP" | sed 's/^/  /'
echo

# Replace only the 5 schedule fields, keeping the command exactly as-is.
awk -v sched="$SCHED" -v match_re="$MATCH" '
  $0 ~ match_re && $0 !~ /^[[:space:]]*#/ {
    cmd = $0
    # strip the five leading schedule fields (or @hourly-style shorthand)
    if (sub(/^[[:space:]]*@[a-z]+[[:space:]]+/, "", cmd) == 0) {
      sub(/^[[:space:]]*([^[:space:]]+[[:space:]]+){5}/, "", cmd)
    }
    print sched " " cmd
    next
  }
  { print }
' "$BACKUP" >"state/crontab-new-$STAMP.txt"

echo "--- PROPOSED ---"
cat "state/crontab-new-$STAMP.txt"
echo "----------------"
echo
printf 'Install this crontab? Type yes to confirm: '
read -r ANSWER
if [ "$ANSWER" != "yes" ]; then
  echo "Cancelled. Nothing changed. Your crontab is untouched."
  rm -f "state/crontab-new-$STAMP.txt"
  exit 0
fi

if crontab "state/crontab-new-$STAMP.txt"; then
  echo
  echo "Done. BIN sweep now runs $HUMAN."
  echo
  echo "To undo:  crontab \"$PWD/$BACKUP\""
else
  echo
  echo "Install failed. Your old crontab is still active."
  echo "Backup kept at: $BACKUP"
  exit 1
fi

echo
read -r -p "Press return to close..." _
