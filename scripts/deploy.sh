#!/usr/bin/env bash
# Copy the project's publishable files to HOST:~/piyofana and (re)start the stack.
# Only files git would track are sent: .env, data/ and docs/local/ never leave.
set -euo pipefail
host=${1:?usage: scripts/deploy.sh user@host}
cd "$(git rev-parse --show-toplevel)"
scripts/scan.sh
git ls-files -co --exclude-standard | rsync -a --files-from=- ./ "$host:piyofana/"
ssh "$host" 'cd ~/piyofana && test -f .env && docker compose up -d --build && docker compose ps'
