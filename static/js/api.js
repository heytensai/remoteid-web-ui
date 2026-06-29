/**
 * API client for Remote ID Web Interface
 */

const AUTH_TOKEN_KEY = 'auth_token';

const API = {
    baseUrl: '',
    csrfToken: null,
    authToken: null,

    /**
     * Initialize API client
     */
    init() {
        this.baseUrl = document.body.dataset.baseUrl || '';
        this.authToken = localStorage.getItem(AUTH_TOKEN_KEY);
    },

    /**
     * Get the current auth token, if any.
     */
    getAuthToken() {
        return this.authToken;
    },

    /**
     * Store an auth token to localStorage and the in-memory cache.
     */
    setAuthToken(token) {
        this.authToken = token;
        if (token) {
            localStorage.setItem(AUTH_TOKEN_KEY, token);
        } else {
            localStorage.removeItem(AUTH_TOKEN_KEY);
        }
    },

    /**
     * Create an ephemeral (anonymous) visitor account.
     * Returns {token, user}.
     */
    async anonLogin() {
        return this._post('/api/auth/anon', {}, 1, 500, 10000);
    },

    /**
     * Exchange a one-time login token for a session token.
     * Returns {token, user}.
     */
    async loginWithToken(loginToken) {
        return this._post('/api/auth/login', { login_token: loginToken }, 1, 500, 10000);
    },

    /**
     * Get the current authenticated user info and permissions.
     * Returns {authenticated: bool, user?: ..., permissions?: [...]}
     */
    async getMe() {
        return this._get('/api/auth/me', 1, 500, 10000);
    },

    /**
     * Revoke the current auth token.
     */
    async logout() {
        const result = await this._post('/api/auth/logout', {}, 1, 500, 10000);
        this.setAuthToken(null);
        return result;
    },

    /**
     * Get configuration
     */
    async getConfig() {
        const config = await this._get('/api/config');
        this.csrfToken = config.csrf_token || null;
        return config;
    },

    /**
     * Get list of drones in time window
     */
    async getDrones(start, end) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._get(`/api/drones?${params}`);
    },

    /**
     * Get drones with newer data than known timestamps (incremental update)
     * @param {Date} start - Start time
     * @param {Date} end - End time
     * @param {Object} knownTimestamps - Map of "uas_id:session_id" -> last known timestamp ISO string
     */
    async getDronesIncremental(start, end, knownTimestamps) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._post(`/api/drones/incremental?${params}`, { known_timestamps: knownTimestamps });
    },

    /**
     * Consolidated refresh: returns drones, alerts, stats, and sources in one call
     * @param {Date} start - Start time
     * @param {Date} end - End time
     * @param {Object} knownTimestamps - Map of "uas_id:session_id" -> last known timestamp ISO string
     */
    async getRefresh(start, end, knownTimestamps) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._post(`/api/refresh?${params}`, { known_timestamps: knownTimestamps || {} });
    },

    /**
     * Get positions in time window
     */
    async getPositions(start, end, uasId = null) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        if (uasId) params.append('uas_id', uasId);
        return this._get(`/api/positions?${params}`);
    },

    /**
     * Get track data for a drone
     * @param {string} uasId - UAS ID
     * @param {Date} start - Start time
     * @param {Date} end - End time
     * @param {boolean} groupBySessions - If true, returns tracks grouped by session
     * @param {string} [sessionId] - Optional session ID to filter to a single session
     */
    async getTrack(uasId, start, end, groupBySessions = true, sessionId) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        if (groupBySessions) params.append('sessions', 'true');
        if (sessionId) params.append('session_id', sessionId);
        return this._get(`/api/tracks/${encodeURIComponent(uasId)}?${params}`);
    },

    /**
     * Batch fetch tracks for multiple sessions
     * @param {Array<{uas_id: string, session_id: string}>} sessions
     * @returns {Promise<Object>} Map of "uas_id:session_id" -> {uas_id, session_id, positions}
     */
    async getTracksBatch(sessions) {
        if (!sessions || sessions.length === 0) return { tracks: {} };
        return this._post('/api/tracks/batch', { sessions });
    },

    /**
     * Get operator positions
     */
    async getOperators(start, end) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._get(`/api/operators?${params}`);
    },

    /**
     * Get bounds of all positions in time window
     */
    async getBounds(start, end) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._get(`/api/bounds?${params}`);
    },

    /**
     * Get remote sources status (API submitters)
     */
    async getSources() {
        return this._get('/api/sources');
    },

    /**
     * Get aggregate statistics for a time window
     */
    async getStats(start, end) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._get(`/api/stats?${params}`);
    },

    /**
     * Get mobile collector positions
     */
    async getCollectors() {
        return this._get('/api/collectors');
    },

    /**
     * Get active geozone alerts
     */
    async getAlerts() {
        return this._get('/api/alerts');
    },

    /**
     * Get geozone alert event history with optional filtering
     * @param {Object} [filters] - Optional filters {uas_id, geozone_name, from, to, limit, offset}
     */
    async getAlertHistory(filters = {}) {
        const params = new URLSearchParams();
        if (filters.uas_id) params.append('uas_id', filters.uas_id);
        if (filters.geozone_name) params.append('geozone_name', filters.geozone_name);
        if (filters.from) params.append('from', filters.from);
        if (filters.to) params.append('to', filters.to);
        if (filters.limit !== undefined) params.append('limit', String(filters.limit));
        if (filters.offset !== undefined) params.append('offset', String(filters.offset));
        return this._get(`/api/alerts/history?${params}`);
    },

    /**
     * Build request headers with auth token and CSRF token.
     */
    _headers(extra = {}) {
        const headers = { ...extra };
        if (this.authToken) {
            headers['X-Auth-Token'] = this.authToken;
        }
        return headers;
    },

    /**
     * Generic GET request with retry logic
     */
    async _get(url, retries = 2, delay = 500, timeoutMs = 30000) {
        let lastError;
        for (let attempt = 0; attempt <= retries; attempt++) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
            try {
                const response = await fetch(this.baseUrl + url, {
                    headers: this._headers(),
                    signal: controller.signal
                });
                clearTimeout(timeoutId);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return response.json();
            } catch (error) {
                clearTimeout(timeoutId);
                if (error.name === 'AbortError') {
                    console.debug(`[API] GET ${url} timed out after ${timeoutMs}ms`);
                }
                lastError = error;
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, delay));
                }
            }
        }
        throw lastError;
    },

    /**
     * Generic POST request with retry logic
     */
    async _post(url, data = {}, retries = 2, delay = 500, timeoutMs = 30000) {
        let lastError;
        for (let attempt = 0; attempt <= retries; attempt++) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
            try {
                const headers = this._headers({
                    'Content-Type': 'application/json'
                });
                if (this.csrfToken) {
                    headers['X-CSRFToken'] = this.csrfToken;
                }
                const response = await fetch(this.baseUrl + url, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(data),
                    signal: controller.signal
                });
                clearTimeout(timeoutId);
                if (!response.ok) {
                    const body = await response.json().catch(() => ({}));
                    // CSRF token expired/missing — refresh and retry
                    if (response.status === 400 && /csrf/i.test(body.error || '')) {
                        console.warn('[API] CSRF error — refreshing token and retrying');
                        await this.getConfig();
                        headers['X-CSRFToken'] = this.csrfToken;
                        const retryResp = await fetch(this.baseUrl + url, {
                            method: 'POST',
                            headers,
                            body: JSON.stringify(data),
                            signal: (new AbortController()).signal
                        });
                        if (!retryResp.ok) {
                            throw new Error(`HTTP ${retryResp.status}: ${retryResp.statusText}`);
                        }
                        return retryResp.json();
                    }
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return response.json();
            } catch (error) {
                clearTimeout(timeoutId);
                if (error.name === 'AbortError') {
                    console.debug(`[API] POST ${url} timed out after ${timeoutMs}ms`);
                }
                lastError = error;
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, delay));
                }
            }
        }
        throw lastError;
    }
};

// Initialize on load
API.init();
