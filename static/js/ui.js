/**
 * UI controller for Remote ID Web Interface
 * Handles sidebar, time picker, and user interactions
 */

const UIController = {
    // State
    currentStartTime: null,
    currentEndTime: null,
    selectedDrones: new Set(),
    selectedSession: null,
    isLoading: false,
    defaultHours: 24,
    droneMap: {},
    loadedTracks: new Set(),
    selectedDrone: null,
    selectedDroneTrack: null,

    // DOM Elements
    elements: {},

    /**
     * Initialize UI
     */
    async init() {
        this._cacheElements();
        this._initEventListeners();
        await this._initTimePicker();
        await this._loadConfig();
        await this.refreshData();
    },

    /**
     * Cache DOM element references
     */
    _cacheElements() {
        this.elements = {
            sidebar: document.getElementById('sidebar'),
            openSidebarBtn: document.getElementById('openSidebar'),
            closeSidebarBtn: document.getElementById('closeSidebar'),
            droneList: document.getElementById('droneList'),
            droneDetail: document.getElementById('droneDetail'),
            closeDetailBtn: document.getElementById('closeDetail'),
            detailUasId: document.getElementById('detailUasId'),
            detailPositions: document.getElementById('detailPositions'),
            detailMaxAlt: document.getElementById('detailMaxAlt'),
            detailDistance: document.getElementById('detailDistance'),
            detailMaxSpeed: document.getElementById('detailMaxSpeed'),
            detailTimeSpan: document.getElementById('detailTimeSpan'),
            detailChart: document.getElementById('detailChart'),
            detailOperator: document.getElementById('detailOperator'),
            detailOperatorId: document.getElementById('detailOperatorId'),
            detailOperatorPos: document.getElementById('detailOperatorPos'),
            refreshBtn: document.getElementById('refreshBtn'),
            syncToggle: document.getElementById('syncToggle'),
            startTimeInput: document.getElementById('startTime'),
            endTimeInput: document.getElementById('endTime'),
            lastUpdateSpan: document.getElementById('lastUpdate'),
            showOperatorsCheckbox: document.getElementById('showOperators'),
            showTracksCheckbox: document.getElementById('showTracks'),
            trackOpacitySlider: document.getElementById('trackOpacity'),
            timePresets: document.querySelectorAll('.header-time-presets button')
        };

    },

    /**
     * Initialize event listeners
     */
    _initEventListeners() {
        // Sidebar toggle
        this.elements.openSidebarBtn.addEventListener('click', () => {
            this.elements.sidebar.classList.add('open');
        });

        this.elements.closeSidebarBtn.addEventListener('click', () => {
            this.elements.sidebar.classList.remove('open');
        });

        // Refresh button
        this.elements.refreshBtn.addEventListener('click', () => {
            this.refreshData();
        });

        // Sync toggle
        this.elements.syncToggle.addEventListener('change', (e) => {
            this._toggleSync(e.target.checked);
        });

        // Close detail panel
        this.elements.closeDetailBtn.addEventListener('click', () => {
            this._closeDetailPanel();
        });

        // Time preset buttons
        this.elements.timePresets.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const hours = parseInt(e.target.dataset.hours);
                this._setTimeRange(hours);
                this._updateActivePreset(e.target);
                this.refreshData();
            });
        });

        // Show/hide operators
        this.elements.showOperatorsCheckbox.addEventListener('change', (e) => {
            MapController.toggleOperators(e.target.checked);
        });

        // Show/hide tracks
        this.elements.showTracksCheckbox.addEventListener('change', (e) => {
            MapController.toggleTracks(e.target.checked);
        });

        // Track opacity
        let opacityTimeout = null;
        this.elements.trackOpacitySlider.addEventListener('input', (e) => {
            if (opacityTimeout) {
                clearTimeout(opacityTimeout);
            }
            opacityTimeout = setTimeout(() => {
                MapController.setTrackOpacity(e.target.value);
            }, 50);
        });

        // Close sidebar when clicking on map (mobile)
        document.addEventListener('click', (e) => {
            if (window.innerWidth < 768 &&
                !this.elements.sidebar.contains(e.target) &&
                !this.elements.openSidebarBtn.contains(e.target)) {
                this.elements.sidebar.classList.remove('open');
            }
        });
    },

    /**
     * Initialize Flatpickr time pickers
     */
    async _initTimePicker() {
        const endTime = new Date();
        const startTime = new Date(endTime.getTime() - this.defaultHours * 60 * 60 * 1000);

        this.currentStartTime = startTime;
        this.currentEndTime = endTime;

        const config = {
            enableTime: true,
            dateFormat: 'Y-m-d H:i',
            time_24hr: true,
            onChange: (selectedDates, dateStr, instance) => {
                // Clear active preset when manual time is selected
                this._clearActivePreset();
                if (instance.element.id === 'startTime') {
                    this.currentStartTime = selectedDates[0];
                } else {
                    this.currentEndTime = selectedDates[0];
                }
            }
        };

        flatpickr(this.elements.startTimeInput, {
            ...config,
            defaultDate: startTime
        });

        flatpickr(this.elements.endTimeInput, {
            ...config,
            defaultDate: endTime
        });
    },

    /**
     * Load configuration from server
     */
    async _loadConfig() {
        try {
            const config = await API.getConfig();
            this.defaultHours = config.default_hours || 24;

            // Re-initialize time picker with correct default
            this._setTimeRange(this.defaultHours);

            // Load sync status
            if (config.sync_enabled) {
                await this._loadSyncStatus();
            }

            // Update tooltip content

        } catch (e) {
            console.error('Failed to load config:', e);
        }
    },

    /**
     * Set time range based on hours back from now
     */
    _setTimeRange(hours) {
        const endTime = new Date();
        const startTime = new Date(endTime.getTime() - hours * 60 * 60 * 1000);

        this.currentStartTime = startTime;
        this.currentEndTime = endTime;

        // Update Flatpickr instances
        if (this.elements.startTimeInput._flatpickr) {
            this.elements.startTimeInput._flatpickr.setDate(startTime);
        }
        if (this.elements.endTimeInput._flatpickr) {
            this.elements.endTimeInput._flatpickr.setDate(endTime);
        }
    },

    /**
     * Toggle sync thread on/off
     */
    async _toggleSync(enabled) {
        try {
            await API.setSyncStatus(enabled);
        } catch (e) {
            console.error('Failed to toggle sync:', e);
            this.elements.syncToggle.checked = !enabled;
        }
    },

    /**
     * Load sync status from server
     */
    async _loadSyncStatus() {
        try {
            const status = await API.getSyncStatus();
            this.elements.syncToggle.checked = status.enabled;
        } catch (e) {
            console.error('Failed to load sync status:', e);
        }
    },

    /**
     * Update active preset button
     */
    _updateActivePreset(activeBtn) {
        this.elements.timePresets.forEach(btn => btn.classList.remove('active'));
        activeBtn.classList.add('active');
    },

    /**
     * Clear active preset
     */
    _clearActivePreset() {
        this.elements.timePresets.forEach(btn => btn.classList.remove('active'));
    },

    /**
     * Refresh all data
     */
    async refreshData() {
        if (this.isLoading) return;

        this.isLoading = true;
        this.elements.refreshBtn.classList.add('spinning');

        try {
            // Close detail panel on refresh
            this._closeDetailPanel();

            // Fetch drones
            const dronesResponse = await API.getDrones(this.currentStartTime, this.currentEndTime);
            const drones = dronesResponse.drones || [];

            // Update drone list
            this._updateDroneList(drones);

            // Update map markers
            MapController.updateDrones(drones);

            // Store drone data for lazy track loading
            this.droneMap = {};
            drones.forEach(d => { this.droneMap[d.uas_id] = d; });

            // Fetch operators
            const operatorsResponse = await API.getOperators(this.currentStartTime, this.currentEndTime);
            MapController.updateOperators(operatorsResponse.operators || []);

            // Try to fit bounds if no default center is set
            if (!MapController.config.center_lat) {
                const boundsResponse = await API.getBounds(this.currentStartTime, this.currentEndTime);
                if (boundsResponse.bounds) {
                    MapController.fitBounds(boundsResponse.bounds);
                }
            }

            // Update last update time
            this._updateLastUpdateTime();

        } catch (e) {
            console.error('Failed to refresh data:', e);
            this._showError('Failed to load data. Please try again.');
        } finally {
            this.isLoading = false;
            this.elements.refreshBtn.classList.remove('spinning');
        }
    },

    /**
     * Update the drone list in sidebar - now shows sessions as independent entries
     */
    _updateDroneList(drones) {
        const list = this.elements.droneList;

        if (drones.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-satellite-dish"></i>
                    <p>No flights detected in time window</p>
                </div>
            `;
            return;
        }

        // Group flights by date (using session start date)
        const groups = {};
        drones.forEach(drone => {
            const time = new Date(drone.timestamp);
            const dateKey = `${time.getFullYear()}-${String(time.getMonth() + 1).padStart(2, '0')}-${String(time.getDate()).padStart(2, '0')}`;
            if (!groups[dateKey]) {
                groups[dateKey] = [];
            }
            groups[dateKey].push(drone);
        });

        const sortedDates = Object.keys(groups).sort().reverse();

        let html = '';
        sortedDates.forEach(date => {
            const flightCount = groups[date].length;
            // Sort sessions within this date by timestamp (reverse chronological - newest first)
            const sortedSessions = groups[date].sort((a, b) => 
                new Date(b.timestamp) - new Date(a.timestamp)
            );
            html += `
                <div class="date-group">
                    <div class="date-header">
                        <span class="date-label">${date}</span>
                        <span class="date-count">${flightCount} flight${flightCount !== 1 ? 's' : ''}</span>
                        <i class="fas fa-chevron-right date-chevron"></i>
                    </div>
                    <div class="date-items collapsed">
                        ${sortedSessions.map(drone => {
                            const color = MapController.getDroneColor(drone.uas_id);
                            const altitude = drone.altitude !== null && drone.altitude !== undefined
                                ? `${drone.altitude.toFixed(0)}m`
                                : 'N/A';
                            const time = new Date(drone.timestamp);
                            const timeStr = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
                            
                            // Create unique key for this session
                            const sessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
                            const sessionId = drone.computed_session_id ? drone.computed_session_id.replace('session_', '') : '';
                            const isSelected = this.selectedDrones.has(sessionKey);

                            return `
                                <div class="drone-item ${isSelected ? 'active' : ''}" data-uas-id="${drone.uas_id}" data-session-key="${sessionKey}" data-session-id="${drone.computed_session_id || ''}">
                                    <div class="drone-color" style="background-color: ${color};"></div>
                                    <div class="drone-info">
                                        <div class="drone-id">${drone.uas_id}</div>
                                        <div class="session-id">${sessionId}</div>
                                        <div class="drone-meta">Alt: ${altitude} | ${timeStr}</div>
                                    </div>
                                    <div class="drone-actions">
                                        <button class="focus-btn" title="Focus on map">
                                            <i class="fas fa-crosshairs"></i>
                                        </button>
                                    </div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `;
        });

        list.innerHTML = html;

        // Add date header click handlers (toggle expand/collapse)
        list.querySelectorAll('.date-header').forEach(header => {
            header.addEventListener('click', async () => {
                const group = header.closest('.date-group');
                const items = group.querySelector('.date-items');
                const chevron = header.querySelector('.date-chevron');

                if (items.classList.contains('collapsed')) {
                    items.classList.remove('collapsed');
                    chevron.classList.remove('fa-chevron-right');
                    chevron.classList.add('fa-chevron-down');

                    // Load tracks for all flights in this group
                    const droneItems = items.querySelectorAll('.drone-item');
                    for (const item of droneItems) {
                        const uasId = item.dataset.uasId;
                        const sessionKey = item.dataset.sessionKey;
                        const sessionId = item.dataset.sessionId;
                        if (sessionId && !this.loadedTracks.has(sessionKey)) {
                            await MapController.loadTrackSession(uasId, sessionId, this.currentStartTime, this.currentEndTime);
                            this.loadedTracks.add(sessionKey);
                        }
                    }
                } else {
                    items.classList.add('collapsed');
                    chevron.classList.remove('fa-chevron-down');
                    chevron.classList.add('fa-chevron-right');

                    // Remove tracks for all flights in this group
                    const droneItems = items.querySelectorAll('.drone-item');
                    for (const item of droneItems) {
                        const uasId = item.dataset.uasId;
                        const sessionKey = item.dataset.sessionKey;
                        MapController.removeTrack(uasId);
                        this.loadedTracks.delete(sessionKey);
                    }
                }
            });
        });

        // Add click handlers for drone items (now session-based)
        list.querySelectorAll('.drone-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const uasId = item.dataset.uasId;
                const sessionKey = item.dataset.sessionKey;
                const sessionId = item.dataset.sessionId;

                // Toggle selection
                if (this.selectedDrones.has(sessionKey)) {
                    this.selectedDrones.delete(sessionKey);
                    item.classList.remove('active');
                } else {
                    this.selectedDrones.clear();
                    this.selectedDrones.add(sessionKey);
                    list.querySelectorAll('.drone-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                }

                // Focus on map
                MapController.highlightDrone(uasId);

                // Load track for this specific session
                if (sessionId) {
                    MapController.loadTrackSession(uasId, sessionId, this.currentStartTime, this.currentEndTime);
                    this.loadedTracks.add(sessionKey);
                }

                // Open detail panel for this session
                this._openDetailPanel(uasId, sessionId);

                // Close sidebar on mobile
                if (window.innerWidth < 768) {
                    this.elements.sidebar.classList.remove('open');
                }
            });
        });

        // Add focus button handlers
        list.querySelectorAll('.focus-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const item = btn.closest('.drone-item');
                const uasId = item.dataset.uasId;
                const sessionKey = item.dataset.sessionKey;
                const sessionId = item.dataset.sessionId;
                
                MapController.panToDrone(uasId);

                // Also load the track for this session
                if (sessionId && !this.loadedTracks.has(sessionKey)) {
                    MapController.loadTrackSession(uasId, sessionId, this.currentStartTime, this.currentEndTime);
                    this.loadedTracks.add(sessionKey);
                }

                // Close sidebar on mobile
                if (window.innerWidth < 768) {
                    this.elements.sidebar.classList.remove('open');
                }
            });
        });
    },

    /**
     * Update last update time display
     */
    _updateLastUpdateTime() {
        const now = new Date();
        this.elements.lastUpdateSpan.textContent = `Last updated: ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;
    },

    /**
     * Open the drone detail panel - now session-specific
     */
    async _openDetailPanel(uasId, sessionId = null) {
        this.selectedDrone = uasId;
        this.selectedSession = sessionId;
        
        if (sessionId) {
            // Show shortened session ID in title
            const shortSessionId = sessionId.replace('session_', '');
            this.elements.detailUasId.innerHTML = `${uasId}<br><small>${shortSessionId}</small>`;
        } else {
            this.elements.detailUasId.textContent = uasId;
        }
        
        this.elements.droneDetail.classList.add('open');

        // Fetch track data
        try {
            const response = await API.getTrack(uasId, this.currentStartTime, this.currentEndTime, true);

            if (sessionId && response.sessions && response.sessions.length > 0) {
                // Find specific session
                const session = response.sessions.find(s => s.session_id === sessionId);
                if (session && session.positions && session.positions.length > 0) {
                    this.selectedDroneTrack = session.positions;
                    this._updateDetailStats(session.positions);
                    this._drawAltitudeChart(session.positions);
                } else {
                    this._clearDetailStats();
                }
            } else if (response.track && response.track.length > 0) {
                // Fallback for old format
                this.selectedDroneTrack = response.track;
                this._updateDetailStats(response.track);
                this._drawAltitudeChart(response.track);
            } else {
                this._clearDetailStats();
            }
        } catch (e) {
            console.error('Failed to load drone detail:', e);
            this._clearDetailStats();
        }
    },

    /**
     * Close the drone detail panel
     */
    _closeDetailPanel() {
        this.elements.droneDetail.classList.remove('open');
        this.selectedDrone = null;
        this.selectedSession = null;
        this.selectedDroneTrack = null;
    },

    /**
     * Update detail stats from track data (session-specific)
     */
    _updateDetailStats(track) {
        const numPositions = track.length;
        this.elements.detailPositions.textContent = numPositions;

        // Max altitude
        const maxAlt = Math.max(...track.map(p => p.altitude || 0));
        this.elements.detailMaxAlt.textContent = maxAlt > 0 ? `${maxAlt.toFixed(0)}m` : 'N/A';

        // Total distance and max speed
        let totalDistance = 0;
        let maxSpeed = 0;

        for (let i = 1; i < track.length; i++) {
            const dist = this._haversineDistance(
                track[i - 1].latitude, track[i - 1].longitude,
                track[i].latitude, track[i].longitude
            );
            totalDistance += dist;

            const timeDiff = (new Date(track[i].timestamp) - new Date(track[i - 1].timestamp)) / 1000;
            if (timeDiff > 0) {
                const speed = dist / timeDiff;
                if (speed > maxSpeed) {
                    maxSpeed = speed;
                }
            }
        }

        this.elements.detailDistance.textContent = totalDistance > 1000
            ? `${(totalDistance / 1000).toFixed(2)} km`
            : `${totalDistance.toFixed(0)} m`;

        this.elements.detailMaxSpeed.textContent = maxSpeed > 0
            ? `${(maxSpeed * 3.6).toFixed(1)} km/h`
            : 'N/A';

        // Time span
        if (track.length >= 2) {
            const firstTime = new Date(track[0].timestamp);
            const lastTime = new Date(track[track.length - 1].timestamp);
            const spanMs = lastTime - firstTime;
            const spanMin = Math.floor(spanMs / 60000);
            const spanSec = Math.floor((spanMs % 60000) / 1000);
            this.elements.detailTimeSpan.textContent = spanMin > 0
                ? `${spanMin}m ${spanSec}s`
                : `${spanSec}s`;
        } else {
            this.elements.detailTimeSpan.textContent = 'N/A';
        }

        // Operator info
        const lastPoint = track[track.length - 1];
        if (lastPoint && lastPoint.operator_id) {
            this.elements.detailOperator.style.display = 'block';
            this.elements.detailOperatorId.textContent = lastPoint.operator_id;
            if (lastPoint.operator_latitude != null && lastPoint.operator_longitude != null) {
                this.elements.detailOperatorPos.textContent =
                    `${lastPoint.operator_latitude.toFixed(4)}, ${lastPoint.operator_longitude.toFixed(4)}`;
            } else {
                this.elements.detailOperatorPos.textContent = 'N/A';
            }
        } else {
            this.elements.detailOperator.style.display = 'none';
        }
    },

    /**
     * Clear detail stats
     */
    _clearDetailStats() {
        this.elements.detailPositions.textContent = '-';
        this.elements.detailMaxAlt.textContent = '-';
        this.elements.detailDistance.textContent = '-';
        this.elements.detailMaxSpeed.textContent = '-';
        this.elements.detailTimeSpan.textContent = '-';
        this.elements.detailOperator.style.display = 'none';
    },

    /**
     * Draw altitude over time chart
     */
    _drawAltitudeChart(track) {
        const canvas = this.elements.detailChart;
        const ctx = canvas.getContext('2d');

        const dpr = window.devicePixelRatio || 1;
        const rect = canvas.parentElement.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = 200 * dpr;
        canvas.style.height = '200px';
        ctx.scale(dpr, dpr);

        const width = rect.width;
        const height = 200;
        const padding = { top: 20, right: 16, bottom: 30, left: 48 };

        // Clear
        ctx.clearRect(0, 0, width, height);

        // Filter valid altitude points
        const validPoints = track.filter(p => p.altitude != null && p.altitude > 0);
        if (validPoints.length < 2) {
            ctx.fillStyle = '#6c757d';
            ctx.font = '13px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('No altitude data available', width / 2, height / 2);
            return;
        }

        const times = validPoints.map(p => new Date(p.timestamp).getTime());
        const altitudes = validPoints.map(p => p.altitude);

        const minTime = Math.min(...times);
        const maxTime = Math.max(...times);
        const minAlt = Math.min(...altitudes);
        const maxAlt = Math.max(...altitudes);

        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        function xForTime(t) {
            return padding.left + ((t - minTime) / (maxTime - minTime || 1)) * chartWidth;
        }
        function yForAlt(a) {
            return padding.top + chartHeight - ((a - minAlt) / (maxAlt - minAlt || 1)) * chartHeight;
        }

        // Grid lines
        ctx.strokeStyle = '#e9ecef';
        ctx.lineWidth = 1;
        const altRange = maxAlt - minAlt || 1;
        const altStep = this._niceStep(altRange, 4);
        const altStart = Math.ceil(minAlt / altStep) * altStep;

        ctx.font = '10px sans-serif';
        ctx.fillStyle = '#6c757d';
        ctx.textAlign = 'right';

        for (let a = altStart; a <= maxAlt; a += altStep) {
            const y = yForAlt(a);
            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(width - padding.right, y);
            ctx.stroke();
            ctx.fillText(`${a.toFixed(0)}`, padding.left - 4, y + 3);
        }

        // Altitude label
        ctx.save();
        ctx.translate(10, height / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.fillStyle = '#495057';
        ctx.font = '11px sans-serif';
        ctx.fillText('Altitude (m)', 0, 0);
        ctx.restore();

        // Draw line
        ctx.strokeStyle = '#007bff';
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.beginPath();

        for (let i = 0; i < validPoints.length; i++) {
            const x = xForTime(times[i]);
            const y = yForAlt(altitudes[i]);
            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        }
        ctx.stroke();

        // Draw area fill
        ctx.lineTo(xForTime(times[times.length - 1]), padding.top + chartHeight);
        ctx.lineTo(xForTime(times[0]), padding.top + chartHeight);
        ctx.closePath();
        ctx.fillStyle = 'rgba(0, 123, 255, 0.1)';
        ctx.fill();

        // Time labels
        ctx.fillStyle = '#6c757d';
        ctx.textAlign = 'center';
        ctx.font = '10px sans-serif';

        const timeRange = maxTime - minTime || 1;
        const timeStep = this._niceStep(timeRange, 4);
        const timeStart = Math.ceil(minTime / timeStep) * timeStep;

        for (let t = timeStart; t <= maxTime; t += timeStep) {
            const x = xForTime(t);
            const date = new Date(t);
            const label = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            ctx.fillText(label, x, height - 6);
        }
    },

    /**
     * Calculate nice step value for chart axes
     */
    _niceStep(range, targetTicks) {
        const rough = range / targetTicks;
        const pow = Math.pow(10, Math.floor(Math.log10(rough)));
        const frac = rough / pow;
        let nice;
        if (frac <= 1.5) nice = 1;
        else if (frac <= 3) nice = 2;
        else if (frac <= 7) nice = 5;
        else nice = 10;
        return nice * pow;
    },

    /**
     * Haversine distance in meters
     */
    _haversineDistance(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
        const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        return R * c;
    },

    /**
     * Show error message
     */
    _showError(message) {
        // Simple alert for now - could be replaced with a toast
        console.error(message);
        // Could implement a toast notification here
    }
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    UIController.init();
});
