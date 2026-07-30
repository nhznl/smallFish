# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report privately through GitHub's private vulnerability reporting:

1. Go to the [Security tab](https://github.com/nhznl/smallFish/security) of
   this repository.
2. Choose **Report a vulnerability**.
3. Describe the issue, how to reproduce it, and what an attacker could achieve.

This keeps the report visible only to the maintainer until a fix is available.

### What to expect

Single-maintainer project, best effort:

| | Target |
|---|---|
| Acknowledgement | within 7 days |
| Initial assessment | within 14 days |
| Fix or documented mitigation | depends on severity; you will be kept informed |

You will be credited when a report leads to a fix, unless you prefer otherwise.

## Never include in a report

- A real credential, API key, or refresh token. Describe it; do not paste it.
- A real account number, position, cost basis, or transaction.
- An unredacted `app.env`, or any file from `data/ledger_trading/` or
  `data/ledger_retirement/`.

`./commands.sh doctor` output is safe to attach: it masks secrets and reports
counts and paths rather than contents.

**If you believe your own credentials were exposed, revoke them at the provider
first,** then report. Revocation is the only action that actually stops the
exposure.

## Supported versions

smallFish has no release tags yet. Only the current `main` is supported. There
are no backports.

## Threat model

smallFish is a **local, single-user research tool**.

Assumed:

- It runs on a machine the user controls, bound to `127.0.0.1`.
- The user is the only operator.
- `app.env` is protected by filesystem permissions (mode 0600).

Explicitly **not** provided:

- **No authentication, authorization, or rate limiting.** Anyone who can reach
  the port has full access to every endpoint, including the ones that trigger
  batch jobs.
- **No multi-tenancy.** There is no notion of separate users.
- **No transport security.** Plain HTTP on localhost.

Exposing smallFish to the internet or an untrusted network is outside the threat
model and is not supported. If you need remote access, put it behind your own
authenticating reverse proxy and accept responsibility for that design.

### In scope

- A path that leaks a credential into a response, log, error message, or the UI.
- Path traversal or arbitrary file read through an API parameter.
- Command injection through a parameter reaching a batch job.
- A brokerage integration performing a write when it should be read-only.
- A committed secret, or one written to a git-tracked file.
- Dependency vulnerabilities that are reachable in normal use.

### Out of scope

- Anything requiring the attacker to already control the machine or `app.env`.
- The absence of authentication (documented above).
- Vulnerabilities in a provider's own service — report those to the provider.
- Advisories affecting only dev dependencies with no runtime path. `npm audit`
  currently reports advisories in the Karma test toolchain; none ship in the
  application bundle.

## How smallFish handles your credentials

- `app.env` is git-ignored and created at mode 0600.
- Secrets are entered with `getpass` and never accepted as command-line
  arguments, so they reach neither shell history nor a process listing.
- `doctor` and `setup-brokerages.sh status` mask every value and make no network
  call. Both have tests asserting no secret can reach their output.
- `GET /capabilities` reports presence only — booleans, never values.
- Provider exceptions can embed tokens, so only the exception *type* is shown to
  the user; detail stays in the server logs.
- Brokerage access is read-only. smallFish never places, modifies, or cancels an
  order, and never receives a brokerage password.

Verify the repository yourself:

```bash
python3 tools/scan_secrets.py            # tracked files; runs in CI
python3 tools/scan_secrets.py --history  # all reachable Git objects
```

Both mask every excerpt, so their output is safe to share.

## Repository history

This repository's history begins at a single initial commit. It was published
fresh rather than carried over from an earlier private repository, so no
credential, personal address, or generated artifact from that earlier work is
reachable here.

You can verify that yourself:

```bash
python3 tools/scan_secrets.py --history
```
