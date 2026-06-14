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

    /**
     * Initialize the map
     */
    async init() {
        // Get config from server
        try {
            const response = await API.getConfig();
            this.config = response.map;
        } catch (e) {
            console.error('Failed to load config:', e);
            this.config = {};
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
     * Create drone icon
     */
    createDroneIcon(color) {
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="drone-icon" style="border: 3px solid ${color}; color: ${color};">
                     <i class="fas fa-plane"></i>
                   </div>`,
            iconSize: [32, 32],
            iconAnchor: [16, 16],
            popupAnchor: [0, -16]
        });
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
     */
    updateSessionOperators(uasId, sessionId, positions, color) {
        // Remove any existing session operators for this uasId
        this._clearSessionOperators(uasId);

        // Find unique operator positions within this session
        const operatorPositions = [];
        const seenPositions = new Set();

        for (const pos of positions) {
            if (pos.operator_latitude != null && pos.operator_longitude != null) {
                const key = `${pos.operator_latitude.toFixed(6)},${pos.operator_longitude.toFixed(6)}`;
                if (!seenPositions.has(key)) {
                    seenPositions.add(key);
                    operatorPositions.push({
                        operator_id: pos.operator_id,
                        operator_latitude: pos.operator_latitude,
                        operator_longitude: pos.operator_longitude,
                        uas_latitude: pos.latitude,
                        uas_longitude: pos.longitude,
                        timestamp: pos.timestamp
                    });
                }
            }
        }

        // Add markers for each unique operator position
        if (!this.sessionOperatorMarkers) {
            this.sessionOperatorMarkers = {};
        }
        if (!this.sessionOperatorMarkers[uasId]) {
            this.sessionOperatorMarkers[uasId] = [];
        }

        for (const op of operatorPositions) {
            // Check if operator is at same location as UAS
            const distance = this._calculateDistance(
                op.operator_latitude, op.operator_longitude,
                op.uas_latitude, op.uas_longitude
            );
            const isSameLocation = distance < 50; // Within 50 meters

            const marker = L.marker([op.operator_latitude, op.operator_longitude], {
                icon: this.createSessionOperatorIcon(color, isSameLocation),
                opacity: 0.85
            }).addTo(this.layers.operators);

            marker.bindPopup(this._createSessionOperatorPopup(uasId, sessionId, op, color, distance));
            this.sessionOperatorMarkers[uasId].push(marker);
        }
    },

    /**
     * Clear session-specific operator markers for a UAS
     */
    _clearSessionOperators(uasId) {
        if (this.sessionOperatorMarkers && this.sessionOperatorMarkers[uasId]) {
            for (const marker of this.sessionOperatorMarkers[uasId]) {
                this.layers.operators.removeLayer(marker);
            }
            delete this.sessionOperatorMarkers[uasId];
        }
    },

    /**
     * Clear all session operator markers
     */
    _clearAllSessionOperators() {
        if (this.sessionOperatorMarkers) {
            for (const uasId of Object.keys(this.sessionOperatorMarkers)) {
                for (const marker of this.sessionOperatorMarkers[uasId]) {
                    this.layers.operators.removeLayer(marker);
                }
            }
            this.sessionOperatorMarkers = {};
        }
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
        const distanceText = distance < 1000
            ? `${distance.toFixed(0)}m`
            : `${(distance / 1000).toFixed(2)}km`;
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
     * Update drone markers (handles both UAS and session entries)
     */
    updateDrones(drones) {
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

        // Update or create markers (one per UAS, at the latest position)
        for (const uasId of uasIds) {
            // Find the latest position for this UAS across all sessions
            const uasEntries = drones.filter(d => d.uas_id === uasId);
            const latestEntry = uasEntries.reduce((latest, current) => {
                return new Date(current.timestamp) > new Date(latest.timestamp) ? current : latest;
            }, uasEntries[0]);

            const color = this.getDroneColor(uasId);
            const lat = latestEntry.latitude;
            const lon = latestEntry.longitude;

            if (this.markers[uasId]) {
                // Update existing marker
                this.markers[uasId].setLatLng([lat, lon]);
            } else {
                // Create new marker
                const marker = L.marker([lat, lon], {
                    icon: this.createDroneIcon(color)
                }).addTo(this.layers.drones);

                // Add popup
                marker.bindPopup(this._createDronePopup(latestEntry, color));

                this.markers[uasId] = marker;
            }
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
     * Remove a specific drone track from the map
     */
    removeTrack(uasId) {
        if (this.tracks[uasId]) {
            if (Array.isArray(this.tracks[uasId])) {
                // Remove all session segments
                for (const segment of this.tracks[uasId]) {
                    this.layers.tracks.removeLayer(segment);
                }
                // Remove session markers if they exist
                if (this.tracks[uasId].markers) {
                    for (const marker of this.tracks[uasId].markers) {
                        this.layers.tracks.removeLayer(marker);
                    }
                }
            } else {
                // Legacy single track
                this.layers.tracks.removeLayer(this.tracks[uasId]);
            }
            delete this.tracks[uasId];
        }

        // Also clear session operators for this UAS
        this._clearSessionOperators(uasId);
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
        const points = positions.map(t => [t.latitude, t.longitude]);

        const polyline = L.polyline(points, {
            color: color,
            weight: 3,
            opacity: 0.6,
            lineCap: 'round',
            lineJoin: 'round'
        }).addTo(this.layers.tracks);

        // Store by UAS ID (array of segments)
        if (!this.tracks[uasId]) {
            this.tracks[uasId] = [];
        }
        this.tracks[uasId].push(polyline);

        // Add start and end markers for this session
        this._addSessionMarkers(uasId, sessionId, positions, color);

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
    _addSessionMarkers(uasId, sessionId, positions, color) {
        if (!positions || positions.length === 0) return;

        const startPos = positions[0];
        const endPos = positions[positions.length - 1];

        // Add start marker
        const startMarker = L.marker([startPos.latitude, startPos.longitude], {
            icon: this.createSessionStartIcon(color),
            opacity: 0.9
        }).addTo(this.layers.tracks);

        startMarker.bindPopup(this._createSessionPointPopup(uasId, sessionId, startPos, 'Start', color));

        // Store the markers with the track
        if (!this.tracks[uasId].markers) {
            this.tracks[uasId].markers = [];
        }
        this.tracks[uasId].markers.push(startMarker);

        // Add end marker (only if different from start)
        if (positions.length > 1) {
            const endMarker = L.marker([endPos.latitude, endPos.longitude], {
                icon: this.createSessionEndIcon(color),
                opacity: 0.9
            }).addTo(this.layers.tracks);

            endMarker.bindPopup(this._createSessionPointPopup(uasId, sessionId, endPos, 'End', color));
            this.tracks[uasId].markers.push(endMarker);
        }
    },

    /**
     * Create popup content for session start/end point
     */
    _createSessionPointPopup(uasId, sessionId, pos, pointType, color) {
        const shortSession = sessionId ? sessionId.replace('session_', '') : 'Unknown';
        const altitude = pos.altitude !== null && pos.altitude !== undefined
            ? `${pos.altitude.toFixed(1)}m`
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
     * Create popup content for drone
     */
    _createDronePopup(drone, color) {
        const altitude = drone.altitude !== null && drone.altitude !== undefined
            ? `${drone.altitude.toFixed(1)}m`
            : 'N/A';
        const time = new Date(drone.timestamp);
        const dateStr = time.toLocaleDateString('en-CA');
        const timeStr = time.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
        const displayTime = `${dateStr} ${timeStr}`;

        // Show session info if available
        let sessionInfo = '';
        if (drone.computed_session_id) {
            const shortSession = drone.computed_session_id.replace('session_', '');
            sessionInfo = `
                <div class="popup-row">
                    <span class="popup-label">Session:</span>
                    <span class="popup-value">${shortSession}</span>
                </div>
            `;
        }

        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-plane"></i> ${drone.uas_id}
            </div>
            ${sessionInfo}
            <div class="popup-row">
                <span class="popup-label">Altitude:</span>
                <span class="popup-value">${altitude}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Last seen:</span>
                <span class="popup-value">${displayTime}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Position:</span>
                <span class="popup-value">${drone.latitude.toFixed(6)}, ${drone.longitude.toFixed(6)}</span>
            </div>
        `;
    },

    /**
     * Create popup content for operator
     */
    _createOperatorPopup(op, color) {
        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-user"></i> ${op.uas_id}
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
