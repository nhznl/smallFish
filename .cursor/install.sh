#!/usr/bin/env bash
# Cloud Agent install: prepare smallFish from a fresh checkout.
#
# Idempotent by design so it is safe to rerun and safe to bake into an
# environment build snapshot. It only adds what the Cursor base image is
# missing, then defers to the repository's own canonical setup entry points
# (./setup.sh and ./commands.sh) rather than duplicating their logic.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Python venv support. The base image ships Python 3.12 but not the
#    python3.12-venv/ensurepip package that `python3 -m venv` needs.
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3.12-venv
fi

# 2. Node 24 LTS via nvm. smallFish requires Node >= 22.22.3 (.nvmrc selects
#    24); the image's default `node` is older, so install and prepend nvm's
#    Node 24 to PATH for the rest of this script.
export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
nvm install 24 >/dev/null
nvm alias default 24 >/dev/null
PATH="$(dirname "$(nvm which 24)"):$PATH"
export PATH

# 3. Repository bootstrap: both Python virtual environments and UI deps.
#    setup.sh is non-interactive and idempotent.
./setup.sh

# 4. Starter price history and its derived snapshots. This reaches Yahoo
#    Finance, so treat a provider/network hiccup as non-fatal: the environment
#    is still usable and `./commands.sh bootstrap-data` can be rerun later.
#    Anything already cached is skipped, so reruns are cheap.
if ./commands.sh bootstrap-data; then
  ./commands.sh sector-rotation || echo "install: sector-rotation skipped (see logs)"
else
  echo "install: bootstrap-data did not complete; run it manually once the data provider is reachable" >&2
fi

# 5. Build the Angular dashboard into stock-app/static for single-server mode.
./commands.sh build-ui
