#!/usr/bin/env bash
# Set up and inspect smallFish's optional brokerage connections.
#
#   ./setup-brokerages.sh status            local only, masked, no network
#   ./setup-brokerages.sh setup tastytrade  guided credential entry
#   ./setup-brokerages.sh setup snaptrade   (Fidelity connects through SnapTrade)
#   ./setup-brokerages.sh setup all
#   ./setup-brokerages.sh verify            documented read-only provider calls
#
# Both integrations are optional. smallFish's core features — stocks, ETFs,
# portfolios, sectors, momentum, wheel screening, and Research Studies — never
# require one. You can stop at any point and still have a working application.
#
# Secrets are entered without echo and are never passed as arguments, so they
# do not reach your shell history or a process listing. Nothing here prints a
# secret or an account identifier. See docs/BROKERAGES.md.
#
# A thin, stable wrapper: the tested logic lives in tools/brokerages.py.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "${PYTHON:-python3}" "$ROOT/tools/brokerages.py" --root "$ROOT" "$@"
