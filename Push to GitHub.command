#!/bin/bash
# Double-click to push committed work to GitHub.
#
# Uses the credentials already on this Mac. Nothing is stored here, and no
# token is written into the repo. Safe to run any time: if there is nothing
# to push it says so and exits.
cd "$(dirname "$0")"

echo "=========================================================================="
echo "PUSH TO GITHUB"
echo "=========================================================================="

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "$BRANCH" ]; then
    echo "This folder is not a git repository."
    read -r -p "Press Enter to close..."
    exit 1
fi

# Uncommitted work is not an error, but you should know it is NOT going up.
DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"

git fetch --quiet origin "$BRANCH" 2>/dev/null
AHEAD="$(git rev-list --count origin/"$BRANCH"..HEAD 2>/dev/null || echo 0)"
BEHIND="$(git rev-list --count HEAD..origin/"$BRANCH" 2>/dev/null || echo 0)"

if [ "$AHEAD" = "0" ]; then
    echo "  Nothing to push - $BRANCH already matches GitHub."
    [ "$DIRTY" != "0" ] && echo "  ($DIRTY uncommitted file(s) - those are not committed yet.)"
    echo
    read -r -p "Press Enter to close..."
    exit 0
fi

echo "  Branch: $BRANCH"
echo "  $AHEAD commit(s) to push:"
git log --oneline origin/"$BRANCH"..HEAD | sed 's/^/    /'
[ "$DIRTY" != "0" ] && {
    echo
    echo "  NOTE: $DIRTY uncommitted file(s) will NOT be pushed:"
    git status --porcelain | sed 's/^/    /' | head -10
}

if [ "$BEHIND" != "0" ]; then
    echo
    echo "  GitHub has $BEHIND commit(s) you do not have locally."
    echo "  Pulling those first so the push is not rejected..."
    if ! git pull --rebase origin "$BRANCH"; then
        echo
        echo "  The pull hit a conflict. Nothing was pushed."
        echo "  Ask Claude to sort it out before trying again."
        read -r -p "Press Enter to close..."
        exit 1
    fi
fi

echo
echo "  Pushing..."
if git push origin "$BRANCH"; then
    echo
    echo "  Done. GitHub is up to date with $BRANCH."
else
    RC=$?
    echo
    echo "  PUSH FAILED (exit $RC)."
    echo
    echo "  If it asked for a username and password: GitHub stopped"
    echo "  accepting passwords. Install the GitHub CLI and sign in once -"
    echo "  it stores the credential in your Keychain and every later push"
    echo "  just works:"
    echo
    echo "      brew install gh"
    echo "      gh auth login"
    echo
    echo "  Choose: GitHub.com -> HTTPS -> yes, authenticate git -> browser."
fi

echo
read -r -p "Press Enter to close..."
