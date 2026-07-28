# Supported environments

smallFish declares a narrow, tested support contract rather than "Python 3" and
"Node.js". Anything outside this matrix may work but is not verified by CI.

Decided: 2026-07-26.

## Platforms

| Platform | Status |
|---|---|
| macOS (Apple silicon and Intel) | Supported, developed on |
| Linux (x86-64, glibc) | Supported, CI baseline |
| Windows | Supported **through WSL 2 only**. Native Windows is untested; `setup.sh`, `commands.sh`, and `setup-brokerages.sh` are POSIX shell scripts. |

## Runtimes

| Runtime | Minimum | Tested in CI | Notes |
|---|---|---|---|
| Python | 3.12 | 3.12 | Both virtual environments use the same interpreter version. Newer versions (3.13, 3.14) work; the pinned `pandas==3.0.0` / `numpy==2.4.2` wheels set the practical floor. |
| Node.js | 22.22.3 | 22 and 24 | Required by Angular 22. `.nvmrc` selects Node 24 LTS; `package.json#engines` records the supported release lines. |
| npm | 10 | 10 and 11 | `npm ci` against the committed lockfile. A global Angular CLI is **not** required. |
| Git | 2.30 | runner default | Needed for `setup.sh` prerequisite checks. |

The preferred development runtime is Node 24 LTS. CI also verifies the minimum supported Node 22 release line. Versions outside `package.json#engines` may work but are not supported.

## Network exposure

FastAPI binds to `127.0.0.1` by default (`APP_HOST`). The service has **no
authentication, authorization, or rate-limiting layer** and must not be exposed
directly to the internet or to an untrusted network. It is a local research
tool. If you need remote access, put it behind your own authenticating reverse
proxy and accept responsibility for that design.

## Not supported at launch

These are reasonable follow-ups, not launch requirements:

- Docker images and devcontainer definitions
- Native Windows (outside WSL)
- Hosted or multi-user deployment
- Python versions below 3.12
