# Angular lazy-load (Phase 8) design

**Status:** 8a complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 8

## Goal

Cut the initial JS payload by converting remaining eager feature routes to
`loadComponent`, matching the pattern already used for portfolios, sector
rotation, and wheel explainer. Preserve every path, title, and component
behavior. No API or wire-shape changes.

Phase 5 route-state tests unblocked this work; the production build was already
within budget, so this is a measured performance improvement.

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **8a** | Lazy-load momentum, studies, wheel, options, retirement, stock detail | Done |

## Before / after

| Route | Before | After |
|---|---|---|
| `momentum` | eager | `loadComponent` |
| `studies`, `studies/:studyId` | eager | `loadComponent` (shared chunk) |
| `wheel` | eager | `loadComponent` |
| `options` | eager | `loadComponent` |
| `retirement` | eager | `loadComponent` |
| `stockDetail/:symbol` | eager | `loadComponent` (dynamic title unchanged) |
| `sectors`, `wheelExplainer`, `portfolios` | already lazy | unchanged |
| `**` (404) | eager | remains eager (tiny shell) |
| `` (redirect to `/momentum`) | redirect | unchanged |

## Conventions

- Same factory shape as existing lazy routes:
  `loadComponent: () => import('…').then(m => m.XComponent)`
- Keep `stockDetailTitle` as a route `title` resolver; it does not require a
  static component import
- Do not introduce `loadChildren` / feature NgModules; standalone
  `loadComponent` is enough
- Specs continue to import components directly; they do not depend on the
  router table

## Out of scope

- ~~Studies nested `paramMap` race~~ **Done in Phase 9** — see
  [`STUDIES_ROUTE_RACE_PHASE9_DESIGN.md`](STUDIES_ROUTE_RACE_PHASE9_DESIGN.md)
- View-model decomposition or shared formatting helpers
- Changing route paths, titles, or default redirect
- Bundle-budget policy changes beyond recording the new initial size

## Verification

- `npm run build` — confirm additional lazy chunks and a smaller initial bundle
  (Phase 8: initial total **457 kB** raw vs audit baseline ~1.12 MB)
- `npm run test:ci`
- `python3 tools/check_docs.py`
- `git diff --check`
- `python3 tools/scan_secrets.py`
