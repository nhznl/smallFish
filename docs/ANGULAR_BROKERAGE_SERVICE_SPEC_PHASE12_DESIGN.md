# Angular BrokerageService spec expectations (Phase 12) design

**Status:** 12a complete (uncommitted pending review)  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 12

## Goal

Make the three BrokerageService cases that already pass via
`HttpTestingController` also register Jasmine expectations, so `npm run test:ci`
is warning-clean for that suite and the intended GET / query-param contracts are
obvious in the spec.

## Why this phase

Preferred Phase 12 options were: (1) the three “no expectations” service-spec
warnings, (2) company-info live-fetch exception documentation, (3) view-model
decomposition design note only.

| Option | Disposition |
|---|---|
| BrokerageService “no expectations” | **Chosen.** Bounded, high hygiene value; three cases already assert URLs via `expectOne` but Karma does not count those as Jasmine expectations. |
| Company-info exception docs | Still open (Medium). Dedicated phase; not mixed here. |
| View-model decomposition design | Still open (Medium). Design-only follow-up; formatting and transport do not unblock it. |
| Broader format-display migration | Still open after Phase 11; measure each surface separately. |
| `models/price.py` | Stop condition — adopt-or-delete owner decision. |

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **12a** | Explicit request assertions on the three warning-triggering BrokerageService cases | Done (pending review) |

## Landed

- `builds identical resource paths for tastytrade` / `fidelity`: each flushed
  request now asserts `method === 'GET'` (same style as the encode / patch /
  archive cases already in the file)
- `omits unset query parameters instead of sending empty values`: each
  `expectOne` capture asserts `GET` before flush
- Phase 10 design status line cleaned to “10a complete” (leftover from after
  that commit landed)

## Out of scope

- New BrokerageService methods or URL shape changes
- CapabilityService or component specs
- Company-info fetcher injection / README exception note
- View-model / presentational decomposition
- Further format-display call-site migration
- Deleting or adopting `models/price.py`
- Beta/Greek trim

## Verification

- `npm run test:ci` (no “no expectations” warnings for BrokerageService)
- `npm run build`
- `python3 tools/check_docs.py`
- `git diff --check`
- `python3 tools/scan_secrets.py`
