#!/bin/zsh
set -u
cd -- "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Python environment missing. Run Setup.command first."
  read -r "?Press Return to close..."
  exit 1
fi

echo "Drag a source manifest YAML file into this window, then press Return:"
read -r MANIFEST
MANIFEST="${(Q)MANIFEST}"
"$PYTHON" code/source_onboarding.py --config-dir . --install "$MANIFEST"
STATUS=$?
echo
read -r "?Press Return to close..."
exit "$STATUS"
