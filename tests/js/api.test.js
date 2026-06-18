/**
 * Tests for static/js/api.js
 */
const fs = require('fs');
const path = require('path');

let apiCode = fs.readFileSync(
  path.resolve(__dirname, '../../static/js/api.js'),
  'utf8'
);

// Remove the auto-init at the end, strip const so eval assigns globally
apiCode = apiCode
  .replace(/\/\/ Initialize on load\nAPI\.init\(\);$/, '')
  .replace(/^const /m, '');
(0, eval)(apiCode);

global.fetch = jest.fn();

function mockFetch(data, ok = true, status = 200) {
  return jest.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: () => Promise.resolve(data),
  });
}

describe('API', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
    API.baseUrl = '';
    API.csrfToken = null;
  });

  describe('init', () => {
    test('reads baseUrl from body dataset', () => {
      document.body.dataset.baseUrl = '/prefix';
      API.init();
      expect(API.baseUrl).toBe('/prefix');
    });

    test('defaults to empty string', () => {
      document.body.dataset.baseUrl = '';
      API.init();
      expect(API.baseUrl).toBe('');
    });
  });

  describe('getConfig', () => {
    test('fetches config and stores csrf token', async () => {
      const configData = { csrf_token: 'token123', map: {} };
      global.fetch = mockFetch(configData);

      const result = await API.getConfig();
      expect(result).toEqual(configData);
      expect(API.csrfToken).toBe('token123');
      expect(fetch).toHaveBeenCalledWith('/api/config');
    });
  });

  describe('getDrones', () => {
    test('builds URL with start and end params', async () => {
      global.fetch = mockFetch({ drones: [] });
      const start = new Date('2024-01-01T00:00:00Z');
      const end = new Date('2024-01-02T00:00:00Z');

      await API.getDrones(start, end);
      const url = fetch.mock.calls[0][0];
      expect(url).toContain('/api/drones?');
      expect(url).toContain('start=');
      expect(url).toContain('end=');
    });

    test('builds URL without params', async () => {
      global.fetch = mockFetch({ drones: [] });
      await API.getDrones(null, null);
      const url = fetch.mock.calls[0][0];
      expect(url).toBe('/api/drones?');
    });
  });

  describe('getPositions', () => {
    test('includes uas_id when provided', async () => {
      global.fetch = mockFetch({ positions: [] });
      const start = new Date('2024-01-01T00:00:00Z');
      const end = new Date('2024-01-02T00:00:00Z');

      await API.getPositions(start, end, 'drone-001');
      const url = fetch.mock.calls[0][0];
      expect(url).toContain('uas_id=drone-001');
    });
  });

  describe('getTrack', () => {
    test('includes sessions param', async () => {
      global.fetch = mockFetch({ uas_id: 'd1', sessions: [] });

      await API.getTrack('d1', null, null, true);
      const url = fetch.mock.calls[0][0];
      expect(url).toContain('sessions=true');
    });

    test('encodes UAS ID in URL path', async () => {
      global.fetch = mockFetch({ uas_id: 'd-1', sessions: [] });

      await API.getTrack('d-1', null, null, false);
      const url = fetch.mock.calls[0][0];
      expect(url).toContain('/api/tracks/d-1');
    });

    test('includes session_id param when provided', async () => {
      global.fetch = mockFetch({ uas_id: 'd1', sessions: [] });

      await API.getTrack('d1', null, null, true, 'session-123');
      const url = fetch.mock.calls[0][0];
      expect(url).toContain('session_id=session-123');
    });
  });

  describe('_post', () => {
    test('includes CSRF token header', async () => {
      API.csrfToken = 'csrf-abc';
      global.fetch = mockFetch({ status: 'ok' });

      await API.triggerSync();
      const options = fetch.mock.calls[0][1];
      expect(options.method).toBe('POST');
      expect(options.headers['X-CSRFToken']).toBe('csrf-abc');
      expect(options.headers['Content-Type']).toBe('application/json');
    });

    test('posts JSON body', async () => {
      API.csrfToken = 't';
      global.fetch = mockFetch({ status: 'ok' });

      await API._post('/api/test', { key: 'value' });
      const options = fetch.mock.calls[0][1];
      expect(JSON.parse(options.body)).toEqual({ key: 'value' });
    });
  });

  describe('retry logic', () => {
    test('retries on failure', async () => {
      const fail = jest.fn().mockRejectedValue(new Error('Network error'));
      const success = mockFetch({ ok: true });
      global.fetch = fail
        .mockRejectedValueOnce(new Error('Network error'))
        .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({}) });

      await API._get('/api/test', 1, 10);
      expect(fetch).toHaveBeenCalledTimes(2);
    });

    test('throws after exhausting retries', async () => {
      global.fetch = jest
        .fn()
        .mockRejectedValue(new Error('Persistent error'));

      await expect(API._get('/api/test', 1, 10)).rejects.toThrow(
        'Persistent error'
      );
      expect(fetch).toHaveBeenCalledTimes(2);
    });
  });
});
