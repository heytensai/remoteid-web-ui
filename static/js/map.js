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
     * Update drone markers
     */
    updateDrones(drones) {
        // Remove markers for drones no longer present
        const currentIds = new Set(drones.map(d => d.uas_id));
        for (const [id, marker] of Object.entries(this.markers)) {
            if (!currentIds.has(id)) {
                this.layers.drones.removeLayer(marker);
                delete this.markers[id];
            }
        }

        // Update or create markers
        for (const drone of drones) {
            const color = this.getDroneColor(drone.uas_id);
            const lat = drone.latitude;
            const lon = drone.longitude;

            if (this.markers[drone.uas_id]) {
                // Update existing marker
                this.markers[drone.uas_id].setLatLng([lat, lon]);
            } else {
                // Create new marker
                const marker = L.marker([lat, lon], {
                    icon: this.createDroneIcon(color)
                }).addTo(this.layers.drones);

                // Add popup
                marker.bindPopup(this._createDronePopup(drone, color));

                this.markers[drone.uas_id] = marker;
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
            this.layers.tracks.removeLayer(this.tracks[uasId]);
            delete this.tracks[uasId];
        }
    },

    /**
     * Load and draw a single drone track
     */
    async loadTrack(uasId, start, end) {
        try {
            const response = await API.getTrack(uasId, start, end);
            if (response.track && response.track.length > 1) {
                const color = this.getDroneColor(uasId);
                this._drawTrack(uasId, response.track, color);
            }
        } catch (e) {
            console.error(`Failed to get track for ${uasId}:`, e);
        }
    },

    /**
     * Update tracks
     */
    async updateTracks(uasIds, start, end) {
        // Clear existing tracks
        this.layers.tracks.clearLayers();
        this.tracks = {};

        // Fetch and draw tracks for each drone in parallel
        await Promise.all(uasIds.map(async uasId => {
            try {
                const response = await API.getTrack(uasId, start, end);
                if (response.track && response.track.length > 1) {
                    const color = this.getDroneColor(uasId);
                    this._drawTrack(uasId, response.track, color);
                }
            } catch (e) {
                console.error(`Failed to get track for ${uasId}:`, e);
            }
        }));
    },

    /**
     * Draw a track on the map
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

        return `
            <div class="popup-title" style="color: ${color};">
                <i class="fas fa-plane"></i> ${drone.uas_id}
            </div>
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
            track.setStyle({ opacity: opacity / 100 });
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
