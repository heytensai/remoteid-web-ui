/**
 * API client for Remote ID Web Interface
 */

const API = {
    baseUrl: '',

    /**
     * Initialize API client
     */
    init() {
        this.baseUrl = '';
    },

    /**
     * Get configuration
     */
    async getConfig() {
        return this._get('/api/config');
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
     * Get track for specific drone
     */
    async getTrack(uasId, start, end) {
        const params = new URLSearchParams();
        if (start) params.append('start', start.toISOString());
        if (end) params.append('end', end.toISOString());
        return this._get(`/api/tracks/${encodeURIComponent(uasId)}?${params}`);
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
     * Trigger manual sync
     */
    async triggerSync() {
        return this._post('/api/sync');
    },

    /**
     * Get sync status
     */
    async getSyncStatus() {
        return this._get('/api/sync/status');
    },

    /**
     * Set sync status
     */
    async setSyncStatus(enabled) {
        return this._post('/api/sync/status', { enabled });
    },

    /**
     * Get collectors status
     */
    async getCollectorsStatus() {
        return this._get('/api/sync/collectors');
    },

    /**
     * Generic GET request with retry logic
     */
    async _get(url, retries = 2, delay = 500) {
        let lastError;
        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                const response = await fetch(url);
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
                const response = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
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
