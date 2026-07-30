import { resolveApiBaseUrl } from './api-base';

describe('resolveApiBaseUrl', () => {
  it('points ng-serve at the local FastAPI origin', () => {
    expect(resolveApiBaseUrl({ port: '4200', origin: 'http://localhost:4200' }))
      .toBe('http://localhost:8000');
  });

  it('uses the page origin outside ng-serve', () => {
    expect(resolveApiBaseUrl({ port: '8000', origin: 'http://127.0.0.1:8000' }))
      .toBe('http://127.0.0.1:8000');
    expect(resolveApiBaseUrl({ port: '', origin: 'https://example.test' }))
      .toBe('https://example.test');
  });
});
