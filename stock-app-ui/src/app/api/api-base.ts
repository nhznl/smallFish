import { InjectionToken } from '@angular/core';

/**
 * Origin for FastAPI calls.
 *
 * ng serve (port 4200) proxies nothing by default, so the SPA talks to the
 * local API on :8000. Every other host — including the production static build
 * served by FastAPI — uses the page origin.
 */
export function resolveApiBaseUrl(
  location: Pick<Location, 'port' | 'origin'> = window.location,
): string {
  return location.port === '4200'
    ? 'http://localhost:8000'
    : location.origin;
}

export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => resolveApiBaseUrl(),
});
