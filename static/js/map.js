/**
 * Map controller for Remote ID Web Interface
 * Uses Leaflet.js with OpenStreetMap tiles
 */

const MapController = {
    map: null,
    markers: {},
    tracks: {},
    operatorMarkers: {},
    layers: {
        drones: null,
        tracks: null,
        operators: null
    },
    config: null,
    bounds: null,
    droneAliases: {},
    ready: false,

    /**
     * Initialize the map
     */
    async init() {
        // Get config from server
        try {
            const response = await API.getConfig();
            this.config = response.map;
            this.droneAliases = response.drone_aliases || {};
        } catch (e) {
            console.error('Failed to load config:', e);
            this.config = {};
            this.droneAliases = {};
        }

        // Create map
        const defaultCenter = [20, 0]; // World view centered on equator/prime meridian
        const center = (this.config.center_lat && this.config.center_lon)
            ? [this.config.center_lat, this.config.center_lon]
            : defaultCenter;
        const zoom = this.config.default_zoom || 3;

        this.map = L.map('map', {
            zoomControl: true,
            attributionControl: true
        }).setView(center, zoom);

        // Add tile layer based on config
        this._addTileLayer();

        // Create layer groups
        this.layers.tracks = L.layerGroup().addTo(this.map);
        this.layers.drones = L.layerGroup().addTo(this.map);
        this.layers.operators = L.layerGroup().addTo(this.map);

        // Reset marker tracking objects
        this.markers = {};
        this.tracks = {};
        this.operatorMarkers = {};
        this.sessionOperatorMarkers = {};

        this.ready = true;

        // Handle window resize
        window.addEventListener('resize', () => {
            this.map.invalidateSize();
        });
    },

    /**
     * Add appropriate tile layer
     */
    _addTileLayer() {
        const provider = this.config.tile_provider || 'osm';

        let tileUrl, attribution;

        switch (provider) {
            case 'carto-dark':
                tileUrl = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
                attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
                break;
            case 'carto-light':
                tileUrl = 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';
                attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
                break;
            case 'osm':
            default:
                tileUrl = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
                attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
                break;
        }

        L.tileLayer(tileUrl, {
            attribution: attribution,
            maxZoom: 19
        }).addTo(this.map);
    },

    /**
     * Generate consistent color from drone ID
     */
    getDroneColor(uasId) {
        let hash = 0;
        for (let i = 0; i < uasId.length; i++) {
            hash = uasId.charCodeAt(i) + ((hash << 5) - hash);
        }
        const hue = Math.abs(hash % 360);
        return `hsl(${hue}, 70%, 50%)`;
    },

    /**
     * Get display name for drone (alias or uas_id)
     */
    getDroneName(uasId) {
        return this.droneAliases[uasId] || uasId;
    },

    /**
     * Create operator icon
     */
    createOperatorIcon(color) {
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="operator-icon" style="border: 2px solid ${color}; color: ${color};">
                     <i class="fas fa-user"></i>
                   </div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
            popupAnchor: [0, -14]
        });
    },

    /**
     * Create session-specific operator icon (smaller, with session indicator)
     */
    createSessionOperatorIcon(color, isSameLocationAsUas) {
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="session-operator-icon ${isSameLocationAsUas ? 'same-location' : ''}" style="border-color: ${color}; color: ${color};">
                     <i class="fas fa-user"></i>
                   </div>`,
            iconSize: [20, 20],
            iconAnchor: [10, 10],
            popupAnchor: [0, -10]
        });
    },

    /**
     * Update operator markers
     */
    updateOperators(operators) {
        // Clear existing operator markers
        this.layers.operators.clearLayers();
        this.operatorMarkers = {};

        for (const op of operators) {
            if (!op.operator_latitude || !op.operator_longitude) continue;

            const color = this.getDroneColor(op.uas_id);
            const lat = op.operator_latitude;
            const lon = op.operator_longitude;

            const marker = L.marker([lat, lon], {
                icon: this.createOperatorIcon(color),
                opacity: 0.8
            }).addTo(this.layers.operators);

            // Add popup
            marker.bindPopup(this._createOperatorPopup(op, color));

            this.operatorMarkers[op.uas_id] = marker;
        }
    },

    /**
     * Update session-specific operator markers (called when showing track)
     * Shows only ONE operator - the first valid operator position from the session
     */
    updateSessionOperators(uasId, sessionId, positions, color) {
        // Remove any existing session operators for this session
        const sessionKey = `${uasId}:${sessionId}`;
        this._clearSessionOperatorsByKey(sessionKey);

        // Find the FIRST valid operator position from this session
        let firstOperator = null;
        for (const pos of positions) {
            if (pos.operator_latitude != null && pos.operator_longitude != null) {
                firstOperator = {
                    operator_id: pos.operator_id,
                    operator_latitude: pos.operator_latitude,
                    operator_longitude: pos.operator_longitude,
                    uas_latitude: pos.latitude,
                    uas_longitude: pos.longitude,
                    timestamp: pos.timestamp
                };
                break; // Only take the first one
            }
        }

        // If no operator position found, don't add any marker
        if (!firstOperator) {
            return;
        }

        // Add marker for the first operator position
        if (!this.sessionOperatorMarkers) {
            this.sessionOperatorMarkers = {};
        }
        if (!this.sessionOperatorMarkers[sessionKey]) {
            this.sessionOperatorMarkers[sessionKey] = [];
        }

        // Check if operator is at same location as UAS
        const distance = this._calculateDistance(
            firstOperator.operator_latitude, firstOperator.operator_longitude,
            firstOperator.uas_latitude, firstOperator.uas_longitude
        );
        const isSameLocation = distance < 50; // Within 50 meters

        const marker = L.marker([firstOperator.operator_latitude, firstOperator.operator_longitude], {
            icon: this.createSessionOperatorIcon(color, isSameLocation),
            opacity: 0.85
        }).addTo(this.layers.operators);

        marker.bindPopup(this._createSessionOperatorPopup(uasId, sessionId, firstOperator, color, distance));
        this.sessionOperatorMarkers[sessionKey].push(marker);
    },

    /**
     * Clear session-specific operator markers for a session key
     */
    _clearSessionOperatorsByKey(sessionKey) {
        if (this.sessionOperatorMarkers && this.sessionOperatorMarkers[sessionKey]) {
            for (const marker of this.sessionOperatorMarkers[sessionKey]) {
                this.layers.operators.removeLayer(marker);
            }
            delete this.sessionOperatorMarkers[sessionKey];
        }
    },

    /**
     * Clear session-specific operator markers for a UAS
     */
    _clearSessionOperators(uasId) {
        if (this.sessionOperatorMarkers) {
            for (const sessionKey of Object.keys(this.sessionOperatorMarkers)) {
                if (sessionKey.startsWith(`${uasId}:`)) {
                    for (const marker of this.sessionOperatorMarkers[sessionKey]) {
                        this.layers.operators.removeLayer(marker);
                    }
                    delete this.sessionOperatorMarkers[sessionKey];
                }
            }
        }
    },

    /**
     * Clear all session operator markers
     */
    _clearAllSessionOperators() {
        if (this.sessionOperatorMarkers) {
            for (const sessionKey of Object.keys(this.sessionOperatorMarkers)) {
                for (const marker of this.sessionOperatorMarkers[sessionKey]) {
                    this.layers.operators.removeLayer(marker);
                }
            }
            this.sessionOperatorMarkers = {};
        }
    },

    /**
     * Clear all operator markers (both global and session-specific)
     */
    clearAllOperators() {
        if (!this.ready || !this.layers.operators) return;
        // Clear global operators
        this.layers.operators.clearLayers();
        this.operatorMarkers = {};

        // Clear session operators
        this._clearAllSessionOperators();
    },

    /**
     * Calculate distance between two coordinates in meters
     */
    _calculateDistance(lat1, lon1, lat2, lon2) {
        const R = 6371000; // Earth's radius in meters
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
        const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        return R * c;
    },

    /**
     * Create popup content for session operator
     */
    _createSessionOperatorPopup(uasId, sessionId, op, color, distance) {
        const shortSession = sessionId ? sessionId.replace('session_', '') : 'Unknown';
        const distanceText = Units.formatDistance(distance);
        const locationNote = distance < 50
            ? '<span style="color: #28a745;">Same location as UAS</span>'
            : `<span style="color: #6c757d;">${distanceText} from UAS</span>`;

        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-user"></i> Session Operator
            </div>
            <div class="popup-row">
                <span class="popup-label">UAS ID:</span>
                <span class="popup-value">${uasId}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Session:</span>
                <span class="popup-value">${shortSession}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Operator ID:</span>
                <span class="popup-value">${op.operator_id || 'N/A'}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Position:</span>
                <span class="popup-value">${op.operator_latitude.toFixed(6)}, ${op.operator_longitude.toFixed(6)}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Location:</span>
                <span class="popup-value">${locationNote}</span>
            </div>
        `;
    },

    /**
     * Clear all drone markers
     */
    clearAllDroneMarkers() {
        if (!this.ready || !this.layers.drones) return;
        // Clear the entire drones layer group
        this.layers.drones.clearLayers();
        this.markers = {};
    },

    /**
     * Clear all tracks
     */
    clearAllTracks() {
        if (!this.ready || !this.layers.tracks) return;
        this.layers.tracks.clearLayers();
        this.tracks = {};
    },

    /**
     * Update drone markers (handles both UAS and session entries)
     */
    updateDrones(drones) {
        if (!this.ready || !this.layers.drones) return;

        // Get unique UAS IDs (since we may have multiple sessions per UAS)
        const uasIds = [...new Set(drones.map(d => d.uas_id))];
        const currentIds = new Set(uasIds);

        // Remove markers for drones no longer present
        for (const [id, marker] of Object.entries(this.markers)) {
            if (!currentIds.has(id)) {
                this.layers.drones.removeLayer(marker);
                delete this.markers[id];
            }
        }

        // If no drones to show, clear everything
        if (uasIds.length === 0) {
            this.clearAllDroneMarkers();
            return;
        }


    },

    /**
     * Update operator markers
     */
    updateOperators(operators) {
        // Clear existing operator markers
        this.layers.operators.clearLayers();
        this.operatorMarkers = {};

        for (const op of operators) {
            if (!op.operator_latitude || !op.operator_longitude) continue;

            const color = this.getDroneColor(op.uas_id);
            const lat = op.operator_latitude;
            const lon = op.operator_longitude;

            const marker = L.marker([lat, lon], {
                icon: this.createOperatorIcon(color),
                opacity: 0.8
            }).addTo(this.layers.operators);

            // Add popup
            marker.bindPopup(this._createOperatorPopup(op, color));

            this.operatorMarkers[op.uas_id] = marker;
        }
    },

    /**
     * Filter operators to only show specific UAS IDs
     * Removes operators for UAS IDs not in the visible set
     */
    filterOperatorsByUasIds(visibleUasIds) {
        if (!this.ready) return;

        // Remove global operators for hidden UAS IDs
        for (const [uasId, marker] of Object.entries(this.operatorMarkers)) {
            if (!visibleUasIds.has(uasId)) {
                this.layers.operators.removeLayer(marker);
                delete this.operatorMarkers[uasId];
            }
        }

        // Remove session operators for hidden sessions
        if (this.sessionOperatorMarkers) {
            for (const sessionKey of Object.keys(this.sessionOperatorMarkers)) {
                const [uasId] = sessionKey.split(':');
                if (!visibleUasIds.has(uasId)) {
                    this._clearSessionOperatorsByKey(sessionKey);
                }
            }
        }
    },

    /**
     * Remove a specific drone track from the map (session-specific)
     */
    removeTrack(uasId, sessionKey) {
        if (!this.ready || !this.layers.tracks) return;

        // If sessionKey is provided, remove only that session's track
        // Otherwise remove all tracks for this uasId
        const trackKey = sessionKey || uasId;

        if (this.tracks[trackKey]) {
            if (Array.isArray(this.tracks[trackKey])) {
                // Remove all session segments
                for (const segment of this.tracks[trackKey]) {
                    this.layers.tracks.removeLayer(segment);
                }
                // Remove session markers if they exist
                if (this.tracks[trackKey].markers) {
                    for (const marker of this.tracks[trackKey].markers) {
                        this.layers.tracks.removeLayer(marker);
                    }
                }
            } else {
                // Legacy single track
                this.layers.tracks.removeLayer(this.tracks[trackKey]);
            }
            delete this.tracks[trackKey];
        }

        // Also clear session operators for this specific session if sessionKey provided
        if (sessionKey) {
            this._clearSessionOperatorsByKey(sessionKey);
        } else {
            // Clear all session operators for this UAS
            this._clearSessionOperators(uasId);
        }
    },

    /**
     * Load and draw a single drone track (with session support)
     */
    async loadTrack(uasId, start, end) {
        try {
            const response = await API.getTrack(uasId, start, end, true);
            if (response.sessions && response.sessions.length > 0) {
                const color = this.getDroneColor(uasId);
                // Draw each session separately
                for (const session of response.sessions) {
                    if (session.positions && session.positions.length > 1) {
                        this._drawTrackSegment(uasId, session.session_id, session.positions, color);
                    }
                }
            } else if (response.track && response.track.length > 1) {
                // Fallback for old format
                const color = this.getDroneColor(uasId);
                this._drawTrack(uasId, response.track, color);
            }
        } catch (e) {
            console.error(`Failed to get track for ${uasId}:`, e);
        }
    },

    /**
     * Load and draw a specific session track
     */
    async loadTrackSession(uasId, sessionId, start, end) {
        if (!this.ready) return;

        try {
            const response = await API.getTrack(uasId, start, end, true);
            if (response.sessions && response.sessions.length > 0) {
                const color = this.getDroneColor(uasId);
                // Find the specific session
                const session = response.sessions.find(s => s.session_id === sessionId);
                if (session && session.positions && session.positions.length > 1) {
                    // Draw just this session
                    this._drawTrackSegment(uasId, sessionId, session.positions, color);
                }
            }
        } catch (e) {
            console.error(`Failed to get track for ${uasId}:${sessionId}:`, e);
        }
    },

    /**
     * Clear all tracks
     */
    clearAllTracks() {
        this.layers.tracks.clearLayers();
        this.tracks = {};
    },

    /**
     * Update tracks (handles multiple drones)
     */
    async updateTracks(uasIds, start, end) {
        // Clear existing tracks
        this.layers.tracks.clearLayers();
        this.tracks = {};

        // Clear all session operators
        this._clearAllSessionOperators();

        // Fetch and draw tracks for each drone in parallel
        await Promise.all(uasIds.map(async uasId => {
            try {
                const response = await API.getTrack(uasId, start, end, true);
                if (response.sessions && response.sessions.length > 0) {
                    const color = this.getDroneColor(uasId);
                    // Draw each session separately
                    for (const session of response.sessions) {
                        if (session.positions && session.positions.length > 1) {
                            this._drawTrackSegment(uasId, session.session_id, session.positions, color);
                        }
                    }
                } else if (response.track && response.track.length > 1) {
                    // Fallback for old format
                    const color = this.getDroneColor(uasId);
                    this._drawTrack(uasId, response.track, color);
                }
            } catch (e) {
                console.error(`Failed to get track for ${uasId}:`, e);
            }
        }));
    },

    /**
     * Draw a track segment (single session) on the map
     */
    _drawTrackSegment(uasId, sessionId, positions, color) {
        if (!this.ready || !this.layers.tracks) return;

        const points = positions.map(t => [t.latitude, t.longitude]);

        const polyline = L.polyline(points, {
            color: color,
            weight: 3,
            opacity: 0.6,
            lineCap: 'round',
            lineJoin: 'round'
        }).addTo(this.layers.tracks);

        // Store by session key instead of just UAS ID
        const sessionKey = `${uasId}:${sessionId}`;
        if (!this.tracks[sessionKey]) {
            this.tracks[sessionKey] = [];
        }
        this.tracks[sessionKey].push(polyline);

        // Add start and end markers for this session
        this._addSessionMarkers(uasId, sessionId, positions, color, sessionKey);

        // Add session-specific operator markers
        this.updateSessionOperators(uasId, sessionId, positions, color);
    },

    /**
     * Create session start icon
     */
    createSessionStartIcon(color) {
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="session-start-icon" style="border-color: ${color}; color: ${color};">
                     <i class="fas fa-play"></i>
                   </div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12]
        });
    },

    /**
     * Create session end icon
     */
    createSessionEndIcon(color) {
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="session-end-icon" style="border-color: ${color}; color: ${color};">
                     <i class="fas fa-stop"></i>
                   </div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12]
        });
    },

    /**
     * Add start and end markers for a session
     */
    _addSessionMarkers(uasId, sessionId, positions, color, sessionKey) {
        if (!positions || positions.length === 0) return;

        const startPos = positions[0];
        const endPos = positions[positions.length - 1];

        // Add start marker
        const startMarker = L.marker([startPos.latitude, startPos.longitude], {
            icon: this.createSessionStartIcon(color),
            opacity: 0.9
        }).addTo(this.layers.tracks);

        startMarker.bindPopup(this._createSessionPointPopup(uasId, sessionId, startPos, 'Start', color));

        // Store the markers with the track using session key
        const trackKey = sessionKey || `${uasId}:${sessionId}`;
        if (!this.tracks[trackKey].markers) {
            this.tracks[trackKey].markers = [];
        }
        this.tracks[trackKey].markers.push(startMarker);

        // Add end marker (only if different from start)
        if (positions.length > 1) {
            const endMarker = L.marker([endPos.latitude, endPos.longitude], {
                icon: this.createSessionEndIcon(color),
                opacity: 0.9
            }).addTo(this.layers.tracks);

            endMarker.bindPopup(this._createSessionPointPopup(uasId, sessionId, endPos, 'End', color));
            this.tracks[trackKey].markers.push(endMarker);
        }
    },

    /**
     * Create popup content for session start/end point
     */
    _createSessionPointPopup(uasId, sessionId, pos, pointType, color) {
        const shortSession = sessionId ? sessionId.replace('session_', '') : 'Unknown';
        const altitude = pos.altitude !== null && pos.altitude !== undefined
            ? Units.formatAltitude(pos.altitude, true, 1)
            : 'N/A';
        const time = new Date(pos.timestamp);
        const dateStr = time.toLocaleDateString('en-CA');
        const timeStr = time.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-${pointType === 'Start' ? 'play' : 'stop'}"></i> ${pointType}
            </div>
            <div class="popup-row">
                <span class="popup-label">UAS ID:</span>
                <span class="popup-value">${uasId}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Session:</span>
                <span class="popup-value">${shortSession}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Time:</span>
                <span class="popup-value">${dateStr} ${timeStr}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Altitude:</span>
                <span class="popup-value">${altitude}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Position:</span>
                <span class="popup-value">${pos.latitude.toFixed(6)}, ${pos.longitude.toFixed(6)}</span>
            </div>
        `;
    },

    /**
     * Draw a track on the map (legacy - single track)
     */
    _drawTrack(uasId, track, color) {
        const points = track.map(t => [t.latitude, t.longitude]);

        const polyline = L.polyline(points, {
            color: color,
            weight: 3,
            opacity: 0.6,
            lineCap: 'round',
            lineJoin: 'round'
        }).addTo(this.layers.tracks);

        this.tracks[uasId] = polyline;
    },

    /**
     * Create popup content for operator
     */
    _createOperatorPopup(op, color) {
        const displayName = this.getDroneName(op.uas_id);
        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-user"></i> ${displayName}
            </div>
            <div class="popup-row">
                <span class="popup-label">UAS ID:</span>
                <span class="popup-value">${op.uas_id}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Operator ID:</span>
                <span class="popup-value">${op.operator_id || 'N/A'}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Position:</span>
                <span class="popup-value">${op.operator_latitude?.toFixed(6)}, ${op.operator_longitude?.toFixed(6)}</span>
            </div>
        `;
    },

    /**
     * Fit map to show all content
     */
    fitBounds(bounds) {
        if (bounds && bounds.min_lat !== null) {
            const latLngBounds = [
                [bounds.min_lat, bounds.min_lon],
                [bounds.max_lat, bounds.max_lon]
            ];
            this.map.fitBounds(latLngBounds, { padding: [50, 50] });
        }
    },

    /**
     * Pan to a specific drone
     */
    panToDrone(uasId) {
        const marker = this.markers[uasId];
        if (marker) {
            const latLng = marker.getLatLng();
            this.map.setView(latLng, 16);
            marker.openPopup();
        }
    },

    /**
     * Show/hide operators
     */
    toggleOperators(show) {
        if (show) {
            this.map.addLayer(this.layers.operators);
        } else {
            this.map.removeLayer(this.layers.operators);
        }
    },

    /**
     * Show/hide tracks
     */
    toggleTracks(show) {
        if (show) {
            this.map.addLayer(this.layers.tracks);
        } else {
            this.map.removeLayer(this.layers.tracks);
        }
    },

    /**
     * Update track opacity
     */
    setTrackOpacity(opacity) {
        for (const track of Object.values(this.tracks)) {
            if (Array.isArray(track)) {
                for (const segment of track) {
                    segment.setStyle({ opacity: opacity / 100 });
                }
            } else {
                track.setStyle({ opacity: opacity / 100 });
            }
        }
    },

    /**
     * Highlight a specific drone
     */
    highlightDrone(uasId) {
        // Reset all markers
        for (const [id, marker] of Object.entries(this.markers)) {
            if (id === uasId) {
                marker.setZIndexOffset(1000);
            } else {
                marker.setZIndexOffset(0);
            }
        }

        // Pan to the drone
        this.panToDrone(uasId);
    }
};

// Initialize map when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    MapController.init();
});
