/**
 * API client for Remote ID Web Interface
 */

const API = {
    baseUrl: '',
    csrfToken: null,

    /**
     * Initialize API client
     */
    init() {
        this.baseUrl = document.body.dataset.baseUrl || '';
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
     * Generic GET request with retry logic
     */
    async _get(url, retries = 2, delay = 500) {
        let lastError;
        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                const response = await fetch(this.baseUrl + url);
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return response.json();
            } catch (error) {
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
    async _post(url, data = {}, retries = 2, delay = 500) {
        let lastError;
        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                const headers = {
                    'Content-Type': 'application/json'
                };
                if (this.csrfToken) {
                    headers['X-CSRFToken'] = this.csrfToken;
                }
                const response = await fetch(this.baseUrl + url, {
                    method: 'POST',
                    headers,
                    body: JSON.stringify(data)
                });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                return response.json();
            } catch (error) {
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
