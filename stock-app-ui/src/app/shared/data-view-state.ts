/**
 * Canonical async list-load states (Phase 14).
 *
 * Use `unavailable` when a capability or artifact prerequisite is missing —
 * not when HTTP failed. Job endpoints use their own status objects.
 */
export type DataViewState = 'loading' | 'empty' | 'ready' | 'failed';

/** Screens that gate on capabilities add `unavailable` alongside list load states. */
export type ScreenDataState = DataViewState | 'unavailable';
