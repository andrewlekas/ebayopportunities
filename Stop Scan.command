#!/bin/bash
# Stop whatever scan or sweep is running right now.
#
# Double-click this any time a run is taking too long, is burning API calls,
# or is holding the lock and stopping you starting a fresh scan.
#
# It asks the run to finish cleanly first (so the database is left tidy) and
# only forces it if it refuses. A partly-finished run writes no workbook -
# that is normal and nothing is corrupted.
cd "$(dirname "$0")" || exit 1

LOCK="state/.scan.lock"

echo "=================================================================="
echo " Stop a running scan"
echo "=================================================================="
echo

if [ ! -f "$LOCK" ]; then
  echo "No lock file - nothing is running."
  echo
  read -r -p "Press return to close..." _
  exit 0
fi

read -r PID MODE STARTED <"$LOCK"
echo "Lock says:  pid $PID   mode=$MODE   started $STARTED"

if ! kill -0 "$PID" 2>/dev/null; then
  echo
  echo "That process is already gone - the lock was stale."
  rm -f "$LOCK"
  echo "Stale lock removed. You can start a new scan."
  echo
  read -r -p "Press return to close..." _
  exit 0
fi

# Guard against pid reuse: only ever signal something that really is this
# scanner. Killing an unrelated process that inherited the number would be
# a very bad way to find out the lock was stale.
CMD=$(ps -p "$PID" -o command= 2>/dev/null)
case "$CMD" in
  *main.py*) : ;;
  *)
    echo
    echo "pid $PID is NOT the card scanner. It is:"
    echo "  $CMD"
    echo
    echo "Refusing to touch it. The lock is stale - removing just the lock."
    rm -f "$LOCK"
    echo
    read -r -p "Press return to close..." _
    exit 0
    ;;
esac

RUNTIME=""
if [ -n "$STARTED" ]; then RUNTIME=" (started $STARTED)"; fi
echo "Running:    $CMD"
echo
printf 'Stop this %s run%s? Type yes to confirm: ' "$MODE" "$RUNTIME"
read -r ANSWER
if [ "$ANSWER" != "yes" ]; then
  echo "Cancelled - the run is still going."
  echo
  read -r -p "Press return to close..." _
  exit 0
fi

echo
echo "Asking it to stop cleanly..."
kill -TERM "$PID" 2>/dev/null

for i in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "Stopped cleanly after ${i}s."
    rm -f "$LOCK"
    echo "Lock released. You can start a new scan."
    echo
    read -r -p "Press return to close..." _
    exit 0
  fi
  sleep 1
done

echo "It did not stop after 20s - forcing it."
kill -KILL "$PID" 2>/dev/null
sleep 1
if kill -0 "$PID" 2>/dev/null; then
  echo
  echo "Could not stop pid $PID. Try Activity Monitor, search for 'python',"
  echo "select it and press the X button."
else
  echo "Forced stop complete."
  rm -f "$LOCK"
  echo "Lock released. You can start a new scan."
fi

echo
read -r -p "Press return to close..." _
