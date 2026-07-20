#!/bin/bash
# Auto-updater, run by allseeingeye-update.timer.
#
# Checks the origin branch recorded at install time; when new commits
# exist, fast-forwards the local repo, re-runs the installer (idempotent,
# never touches /etc/allseeingeye/config.yml or recordings), and restarts
# the kiosk browser only when UI/kiosk files actually changed.
#
# Skips (loudly, see journalctl -u allseeingeye-update) if the repo has
# local uncommitted changes, rather than overwriting someone's edits.

set -euo pipefail

STATE_DIR=/var/lib/allseeingeye
# Consume the UI's request flag first so the path unit doesn't re-trigger.
rm -f "$STATE_DIR/update.request" 2>/dev/null || true

# state + commit + timestamp, read back by the UI via /api/update/status
report() {
    echo "$1 ${2:--} $(date +%s)" > "$STATE_DIR/update.status" 2>/dev/null || true
}

CONF=/etc/allseeingeye/update.conf
if [[ ! -f $CONF ]]; then
    echo "no $CONF — run system/install.sh once to enable updates"
    report blocked
    exit 0
fi
# shellcheck source=/dev/null
source "$CONF" # provides REPO_DIR and BRANCH

if [[ ! -d $REPO_DIR/.git ]]; then
    echo "repo $REPO_DIR is missing — cannot update"
    report blocked
    exit 0
fi
cd "$REPO_DIR"

if [[ -n $(git status --porcelain) ]]; then
    echo "SKIPPING update: local uncommitted changes in $REPO_DIR"
    echo "commit/discard them (or re-clone) to resume updates"
    report blocked
    exit 0
fi

git fetch --quiet origin "$BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
if [[ $LOCAL == "$REMOTE" ]]; then
    report current "$(git rev-parse --short HEAD)"
    exit 0
fi

echo "updating ${LOCAL:0:9} -> ${REMOTE:0:9}"
git reset --hard --quiet "origin/$BRANCH"

if [[ ${ASE_UPDATE_NO_INSTALL:-0} != 1 ]]; then
    bash "$REPO_DIR/system/install.sh"
    # Blank the console only when something it displays/runs has changed.
    if git diff --name-only "$LOCAL" "$REMOTE" | grep -qE '^web/|^system/(allseeingeye-kiosk|kiosk-launch)'; then
        echo "UI changed — restarting kiosk"
        systemctl restart allseeingeye-kiosk.service 2>/dev/null || true
    fi
fi

report updated "$(git rev-parse --short HEAD)"
echo "updated to $(git rev-parse --short HEAD)"
