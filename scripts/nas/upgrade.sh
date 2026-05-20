#!/usr/bin/env bash
# Pull latest from the fork, rebuild the gui image, restart the service.
# Safe to run repeatedly. Persistent data is in the bind-mounted ./data
# directory and is never touched by this script.
#
# Usage (from local machine, after credentials.local exists):
#     scripts/nas/upgrade.sh

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRED_FILE="$DIR/credentials.local"
NAS_CMD="$DIR/nas-cmd.sh"

[[ -f "$CRED_FILE" ]] || { echo "Missing $CRED_FILE — see credentials.local.example"; exit 2; }
# shellcheck disable=SC1090
source "$CRED_FILE"

NAS_REPO_PATH="${NAS_REPO_PATH:-/volume1/docker/tradingagents}"
NAS_GIT_BRANCH="${NAS_GIT_BRANCH:-main}"
NAS_GIT_REMOTE="${NAS_GIT_REMOTE:-origin}"

echo "[upgrade] pulling $NAS_GIT_REMOTE/$NAS_GIT_BRANCH on the NAS"
"$NAS_CMD" "set -e
cd '$NAS_REPO_PATH'
PRE=\$(git rev-parse --short HEAD)
git fetch '$NAS_GIT_REMOTE' --prune
git checkout '$NAS_GIT_BRANCH'
git pull --ff-only '$NAS_GIT_REMOTE' '$NAS_GIT_BRANCH'
POST=\$(git rev-parse --short HEAD)
if [ \"\$PRE\" = \"\$POST\" ]; then
    echo '[upgrade] no new commits — nothing to do.'
    exit 0
fi
echo \"[upgrade] \$PRE -> \$POST\"
git --no-pager log --oneline \$PRE..\$POST
echo

# ── Selective rebuild — only rebuild services whose files changed ──
# Before this optimisation, every deploy rebuilt BOTH api + web even
# when only one had changes, costing ~40s of Next.js rebuild per deploy
# that didn't need it. Now we inspect the changed paths and rebuild
# only what's necessary. Backend-only changes deploy in ~60s instead
# of 2-3 min.
CHANGED=\$(git diff --name-only \$PRE..\$POST)
REBUILD_API=0
REBUILD_WEB=0
RESTART_API=0
RESTART_WEB=0

# Look for paths that change each image. The Dockerfile, requirements,
# pyproject — these REQUIRE a full rebuild. Anything else under the
# service tree just needs a restart (the COPY layer picks it up on
# image rebuild, but since the bind mount in compose makes /app match
# the host repo on the NAS, restart alone is enough for code-only
# changes IF the Dockerfile is unchanged).
if echo \"\$CHANGED\" | grep -qE '^(Dockerfile\\.api|pyproject\\.toml|requirements.*\\.txt|service/|tradingagents/|gui/)'; then
    REBUILD_API=1
    RESTART_API=1
fi
if echo \"\$CHANGED\" | grep -qE '^(Dockerfile\\.web|web/package.*\\.json|web/next\\.config\\.|web/tsconfig\\.|web/tailwind\\.|web/postcss\\.)'; then
    REBUILD_WEB=1
    RESTART_WEB=1
elif echo \"\$CHANGED\" | grep -qE '^web/'; then
    # Source-only change in web — Next.js needs a rebuild to pick up
    # any TS/TSX (production build, no dev hot-reload). So we still
    # rebuild but tell the user it's source-only.
    REBUILD_WEB=1
    RESTART_WEB=1
fi
# Compose changes touch both.
if echo \"\$CHANGED\" | grep -qE '^docker-compose\\.yml'; then
    REBUILD_API=1; REBUILD_WEB=1; RESTART_API=1; RESTART_WEB=1
fi

if [ \$REBUILD_API -eq 0 ] && [ \$REBUILD_WEB -eq 0 ]; then
    echo '[upgrade] no code/config changes that need a rebuild — done.'
    exit 0
fi

TO_BUILD=''
[ \$REBUILD_API -eq 1 ] && TO_BUILD=\"\$TO_BUILD api\"
[ \$REBUILD_WEB -eq 1 ] && TO_BUILD=\"\$TO_BUILD web\"
TO_RESTART=''
[ \$RESTART_API -eq 1 ] && TO_RESTART=\"\$TO_RESTART api\"
[ \$RESTART_WEB -eq 1 ] && TO_RESTART=\"\$TO_RESTART web\"

echo \"[upgrade] rebuilding:\$TO_BUILD\"
docker compose build\$TO_BUILD
echo \"[upgrade] restarting:\$TO_RESTART\"
docker compose up -d\$TO_RESTART
echo
[ \$RESTART_API -eq 1 ] && {
    echo '[upgrade] api logs (last 20):'
    docker compose logs --tail=20 api
    echo
}
[ \$RESTART_WEB -eq 1 ] && {
    echo '[upgrade] web logs (last 20):'
    docker compose logs --tail=20 web
}
"

echo
echo "[upgrade] done."
