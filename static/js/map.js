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
        operators: null,
        waypoints: null
    },
    config: null,
    bounds: null,
    droneAliases: {},
    waypoints: [],
    waypointMarkers: {},
    loadedTrackSessions: new Set(),
    tileLayer: null,
    ready: false,
    staleTimeout: 300,
    alertUasIds: new Set(),

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    /**
     * Initialize the map
     */
    async init() {
        // Get config from server
        try {
            const response = await API.getConfig();
            this.config = response.map;
            this.droneAliases = response.drone_aliases || {};
            this.waypoints = response.waypoints || [];
            this.staleTimeout = response.stale_timeout || 300;
        } catch (e) {
            console.error('Failed to load config:', e);
            this.config = {};
            this.droneAliases = {};
            this.waypoints = [];
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
        this.layers.waypoints = L.layerGroup().addTo(this.map);

        // Reset marker tracking objects
        this.markers = {};
        this.dronePositions = {};
        this.tracks = {};
        this.operatorMarkers = {};
        this.sessionOperatorMarkers = {};

        this.ready = true;

        // Add custom waypoints from config
        this._addWaypoints();

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

        this.tileLayer = L.tileLayer(tileUrl, {
            attribution: attribution,
            maxZoom: 19
        }).addTo(this.map);
    },

    /**
     * Switch tile provider (e.g. from light to dark tiles)
     */
    setTileProvider(provider) {
        if (this.tileLayer) {
            this.map.removeLayer(this.tileLayer);
        }
        const prevProvider = this.config.tile_provider;
        this.config.tile_provider = provider;
        this._addTileLayer();
        this.config.tile_provider = prevProvider;
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
     * Create waypoint icon
     */
    createWaypointIcon(wp) {
        const color = wp.color || '#007bff';
        const icon = wp.icon || 'fa-map-pin';
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="waypoint-icon" style="border: 2px solid ${color}; color: ${color};">
                     <i class="fas ${icon}"></i>
                   </div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
            popupAnchor: [0, -14]
        });
    },

    /**
     * Create popup content for a waypoint or geozone
     */
    _createWaypointPopup(wp) {
        const esc = (v) => this.escapeHtml(v);
        const color = wp.color || '#007bff';
        const category = wp.category
            ? `<div class="popup-row">
                 <span class="popup-label">Category:</span>
                 <span class="popup-value">${esc(wp.category)}</span>
               </div>`
            : '';
        const description = wp.description
            ? `<div class="popup-row" style="margin-top: 4px;">
                 <span class="popup-value" style="font-weight: 400;">${esc(wp.description)}</span>
               </div>`
            : '';

        let typeInfo = '';
        if (wp.type === 'circle') {
            typeInfo = `<div class="popup-row"><span class="popup-label">Radius:</span><span class="popup-value">${Units.formatDistance(wp.radius)}</span></div>`;
        } else if (wp.type === 'rectangle') {
            typeInfo = `<div class="popup-row"><span class="popup-label">Dimensions:</span><span class="popup-value">${Units.formatDistance(wp.width)} × ${Units.formatDistance(wp.height)}</span></div>`;
        }

        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas ${esc(wp.icon || 'fa-map-pin')}"></i> ${esc(wp.name)}
            </div>
            ${category}
            <div class="popup-row">
                <span class="popup-label">Latitude:</span>
                <span class="popup-value">${wp.lat.toFixed(6)}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Longitude:</span>
                <span class="popup-value">${wp.lon.toFixed(6)}</span>
            </div>
            ${typeInfo}
            ${description}
        `;
    },

    /**
     * Add custom waypoints & geozones from config to the map
     */
    _addWaypoints() {
        if (!this.waypoints || this.waypoints.length === 0) return;

        this.waypointMarkers = {};
        for (const wp of this.waypoints) {
            if (wp.enabled === false) continue;
            if (wp.lat == null || wp.lon == null) continue;

            const popupContent = this._createWaypointPopup(wp);
            const center = [wp.lat, wp.lon];
            const color = wp.color || '#007bff';
            let shape = null;

            if (wp.type === 'circle' && wp.radius > 0) {
                shape = L.circle(center, {
                    radius: wp.radius,
                    color: color,
                    fillColor: color,
                    fillOpacity: wp.fill_opacity != null ? wp.fill_opacity : 0.1,
                    weight: 2,
                    dashArray: '8, 8',
                    opacity: 0.8
                }).addTo(this.layers.waypoints);
                shape.bindPopup(popupContent);
            } else if (wp.type === 'rectangle' && wp.width > 0 && wp.height > 0) {
                const lat = wp.lat;
                const lon = wp.lon;
                const latRad = lat * Math.PI / 180;
                const mPerDegLat = 111320;
                const mPerDegLon = 111320 * Math.cos(latRad);
                const halfH = wp.height / 2 / mPerDegLat;
                const halfW = wp.width / 2 / mPerDegLon;
                const bounds = [[lat - halfH, lon - halfW], [lat + halfH, lon + halfW]];
                shape = L.rectangle(bounds, {
                    color: color,
                    fillColor: color,
                    fillOpacity: wp.fill_opacity != null ? wp.fill_opacity : 0.1,
                    weight: 2,
                    dashArray: '8, 8',
                    opacity: 0.8
                }).addTo(this.layers.waypoints);
                shape.bindPopup(popupContent);
            }

            // Always place a clickable marker at center
            const marker = L.marker(center, {
                icon: this.createWaypointIcon(wp),
                opacity: 0.85
            }).addTo(this.layers.waypoints);
            marker.bindPopup(popupContent);

            this.waypointMarkers[wp.name] = { marker, shape };
        }
    },

    /**
     * Pan to a waypoint/geozone and flash its marker
     */
    panToWaypoint(name) {
        const entry = this.waypointMarkers[name];
        if (!entry) return;

        const marker = entry.marker;
        const latlng = marker.getLatLng();
        const isVisible = this.map.getBounds().contains(latlng);

        if (isVisible) {
            this._flashMarker(name);
        } else {
            this.map.setView(latlng, 16, { animate: true });
            setTimeout(() => this._flashMarker(name), 500);
        }
    },

    _flashMarker(name) {
        const entry = this.waypointMarkers[name];
        if (!entry) return;

        // Flash the center marker icon
        const el = entry.marker.getElement();
        if (el) {
            const wpIcon = el.querySelector('.waypoint-icon');
            if (wpIcon) {
                wpIcon.classList.remove('flash');
                void wpIcon.offsetWidth;
                wpIcon.classList.add('flash');
                setTimeout(() => wpIcon.classList.remove('flash'), 600);
            }
        }

        // Flash the geozone shape if present
        if (entry.shape) {
            this._flashShape(entry.shape);
        }
    },

    _flashShape(shape) {
        const origOpts = Object.assign({}, shape.options);
        shape.setStyle({ opacity: 1, weight: 4, fillOpacity: 0.3 });
        setTimeout(() => {
            shape.setStyle({
                opacity: origOpts.opacity,
                weight: origOpts.weight,
                fillOpacity: origOpts.fillOpacity
            });
        }, 600);
    },

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

    _calculateDistance(lat1, lon1, lat2, lon2) {
        return Units.haversineDistance(lat1, lon1, lat2, lon2);
    },

    /**
     * Create popup content for session operator
     */
    _createSessionOperatorPopup(uasId, sessionId, op, color, distance) {
        const shortSession = sessionId ? sessionId.replace('session_', '') : 'Unknown';
        const esc = (v) => this.escapeHtml(v);
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
                <span class="popup-value">${esc(uasId)}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Session:</span>
                <span class="popup-value">${esc(shortSession)}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Operator ID:</span>
                <span class="popup-value">${esc(op.operator_id || 'N/A')}</span>
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
        this.layers.drones.clearLayers();
        this.markers = {};
        this.dronePositions = {};
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
     * Track drone positions for pan-to functionality
     */
    updateDrones(drones) {
        if (!this.ready) return;

        const uasIds = [...new Set(drones.map(d => d.uas_id))];
        const currentIds = new Set(uasIds);

        // Remove stale position entries
        for (const id of Object.keys(this.dronePositions)) {
            if (!currentIds.has(id)) {
                delete this.dronePositions[id];
            }
        }

        if (uasIds.length === 0) return;

        // Store latest position for each drone
        uasIds.forEach(uasId => {
            const entries = drones.filter(d => d.uas_id === uasId);
            if (entries.length === 0) return;
            const last = entries[entries.length - 1];
            if (last.latitude != null && last.longitude != null) {
                this.dronePositions[uasId] = [last.latitude, last.longitude];
            }
        });
    },

    /**
     * Filter operators to only show specific UAS IDs
     * Removes operators for UAS IDs not in the visible set
     */
    /**
     * Update the set of UAS IDs that have active geozone alerts
     */
    updateAlertState(alerts) {
        this.alertUasIds = new Set((alerts || []).map(a => a.uas_id));
    },

    /**
     * Check if a position timestamp is within the stale timeout (still active)
     */
    _isPositionActive(timestamp) {
        const age = (Date.now() - new Date(timestamp).getTime()) / 1000;
        return age < this.staleTimeout;
    },

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
            this.loadedTrackSessions.delete(sessionKey);
        } else {
            // Clear all session operators for this UAS
            this._clearSessionOperators(uasId);
            // Clear all cached sessions for this UAS
            for (const key of this.loadedTrackSessions) {
                if (key.startsWith(`${uasId}:`)) {
                    this.loadedTrackSessions.delete(key);
                }
            }
        }
    },

    /**
     * Load and draw a specific session track, with client-side cache
     */
    async loadTrackSession(uasId, sessionId, start, end) {
        if (!this.ready) return;

        const sessionKey = `${uasId}:${sessionId}`;
        if (this.loadedTrackSessions.has(sessionKey)) return;

        try {
            const response = await API.getTrack(uasId, start, end, true, sessionId);
            if (response.sessions && response.sessions.length > 0) {
                const session = response.sessions[0];
                if (session.positions && session.positions.length > 1) {
                    const color = this.getDroneColor(uasId);
                    this._drawTrackSegment(uasId, sessionId, session.positions, color);
                    this.loadedTrackSessions.add(sessionKey);
                }
            }
        } catch (e) {
            console.error(`Failed to get track for ${uasId}:${sessionId}:`, e);
        }
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
     * Create a drone icon showing live position, with optional geozone alert badge
     */
    createDroneIcon(color, hasAlert) {
        const badge = hasAlert
            ? '<i class="fas fa-exclamation-triangle geozone-badge" style="color: #e74c3c;"></i>'
            : '';
        return L.divIcon({
            className: 'custom-div-icon',
            html: `<div class="drone-position-icon" style="border-color: ${color}; color: ${color};">
                     <i class="fas fa-plane"></i>${badge}
                   </div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
            popupAnchor: [0, -14]
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
        // Use a drone icon if the position is recent (within stale timeout)
        if (positions.length > 1) {
            const isActive = this._isPositionActive(endPos.timestamp);
            const hasAlert = isActive && this.alertUasIds.has(uasId);
            const endIcon = isActive
                ? this.createDroneIcon(color, hasAlert)
                : this.createSessionEndIcon(color);
            const endMarker = L.marker([endPos.latitude, endPos.longitude], {
                icon: endIcon,
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

        const esc = (v) => this.escapeHtml(v);
        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-${pointType === 'Start' ? 'play' : 'stop'}"></i> ${pointType}
            </div>
            <div class="popup-row">
                <span class="popup-label">UAS ID:</span>
                <span class="popup-value">${esc(uasId)}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Session:</span>
                <span class="popup-value">${esc(shortSession)}</span>
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
        const esc = (v) => this.escapeHtml(v);
        const displayName = this.getDroneName(op.uas_id);
        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-user"></i> ${esc(displayName)}
            </div>
            <div class="popup-row">
                <span class="popup-label">UAS ID:</span>
                <span class="popup-value">${esc(op.uas_id)}</span>
            </div>
            <div class="popup-row">
                <span class="popup-label">Operator ID:</span>
                <span class="popup-value">${esc(op.operator_id || 'N/A')}</span>
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
        const pos = this.dronePositions[uasId];
        if (pos) {
            this.map.setView(pos, 16);
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
        this.panToDrone(uasId);
    }
};

// Initialize map when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    MapController.init();
});
