#!/bin/zsh
set -u
cd -- "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Python environment missing. Run Setup.command first."
  read -r "?Press Return to close..."
  exit 1
fi

"$PYTHON" code/source_onboarding.py --config-dir . --list
STATUS=$?
echo
read -r "?Press Return to close..."
exit "$STATUS"
