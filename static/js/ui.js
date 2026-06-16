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
    visibleSessions: new Set(), // Track which sessions are checked/visible
    showKnownDrones: true,
    showUnknownDrones: true,
    expandedDates: new Set(),

    // DOM Elements
    elements: {},

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    /**
     * Initialize UI
     */
    async init() {
        this._cacheElements();
        this._initEventListeners();
        await this._initTimePicker();
        await this._loadConfig();

        // Wait for MapController to be ready
        await this._waitForMapController();

        // Clear any existing markers before loading data
        MapController.clearAllDroneMarkers();
        MapController.clearAllTracks();
        MapController.clearAllOperators();
        await this.refreshData();
    },

    /**
     * Wait for MapController to be ready
     */
    async _waitForMapController() {
        let attempts = 0;
        const maxAttempts = 50; // 5 seconds max

        while (!MapController.ready && attempts < maxAttempts) {
            await new Promise(resolve => setTimeout(resolve, 100));
            attempts++;
        }

        if (!MapController.ready) {
            console.error('MapController failed to initialize');
        }
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
            timePresets: document.querySelectorAll('.header-time-presets button'),
            settingsPanel: document.getElementById('settingsPanel'),

            openSettingsBtn: document.getElementById('openSettings'),
            closeSettingsBtn: document.getElementById('closeSettings'),
            opacityValue: document.getElementById('opacityValue'),
            showKnownDrones: document.getElementById('showKnownDrones'),
            showUnknownDrones: document.getElementById('showUnknownDrones')
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

        // Settings panel toggle
        this.elements.openSettingsBtn.addEventListener('click', () => {
            this.elements.settingsPanel.classList.toggle('open');
        });

        this.elements.closeSettingsBtn.addEventListener('click', () => {
            this._closeSettingsPanel();
        });

        // Update opacity value display
        this.elements.trackOpacitySlider.addEventListener('input', (e) => {
            if (this.elements.opacityValue) {
                this.elements.opacityValue.textContent = e.target.value + '%';
            }
        });

        // Show known/unknown drones
        this.elements.showKnownDrones.addEventListener('change', (e) => {
            this.showKnownDrones = e.target.checked;
            this.refreshData();
        });

        this.elements.showUnknownDrones.addEventListener('change', (e) => {
            this.showUnknownDrones = e.target.checked;
            this.refreshData();
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
            this.droneAliases = config.drone_aliases || {};

            // Re-initialize time picker with correct default
            this._setTimeRange(this.defaultHours);

            // Load sync status
            if (config.sync_enabled) {
                await this._loadSyncStatus();
            }

            // Initialize units from config
            Units.init(config);

        } catch (e) {
            console.error('Failed to load config:', e);
        }
    },

    /**
     * Get display name for drone (alias or uas_id)
     */
    getDroneName(uasId) {
        return this.droneAliases[uasId] || uasId;
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
            // Close detail panel on refresh and clear drone markers/operators
            this._closeDetailPanel();
            MapController.clearAllDroneMarkers();
            MapController.clearAllOperators();

            // Fetch drones
            const dronesResponse = await API.getDrones(this.currentStartTime, this.currentEndTime);
            let drones = dronesResponse.drones || [];

            // Filter by known/unknown drone visibility
            drones = drones.filter(d => {
                const isKnown = !!this.droneAliases[d.uas_id];
                if (isKnown && !this.showKnownDrones) return false;
                if (!isKnown && !this.showUnknownDrones) return false;
                return true;
            });

            // Get current session keys from filtered data
            const currentSessionKeys = new Set(drones.map(d => `${d.uas_id}:${d.computed_session_id || 'unknown'}`));

            // Remove tracks for sessions no longer in the data
            for (const sessionKey of this.loadedTracks) {
                if (!currentSessionKeys.has(sessionKey)) {
                    const [uasId] = sessionKey.split(':');
                    MapController.removeTrack(uasId, sessionKey);
                }
            }
            this.loadedTracks = new Set([...this.loadedTracks].filter(k => currentSessionKeys.has(k)));
            this.visibleSessions = new Set([...this.visibleSessions].filter(k => currentSessionKeys.has(k)));

            // Update drone list (controls checkbox state, tracks loadedTracks)
            this._updateDroneList(drones);

            // Store drone data for lazy track loading
            this.droneMap = {};
            drones.forEach(d => { this.droneMap[d.uas_id] = d; });

            // Map shows ALL filtered drones directly (not filtered by visibleSessions)
            if (drones.length > 0) {
                MapController.updateDrones(drones);
                const allUasIds = new Set(drones.map(d => d.uas_id));
                MapController.filterOperatorsByUasIds(allUasIds);
            } else {
                MapController.clearAllDroneMarkers();
                MapController.clearAllTracks();
                MapController.clearAllOperators();
            }

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
     * Update the drone list in sidebar - shows sessions as independent entries with checkboxes
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

        // Determine most recent date for default selection (only on initial load)
        const mostRecentDate = sortedDates.length > 0 ? sortedDates[0] : null;
        const isInitialLoad = this.visibleSessions.size === 0;

        // Pre-populate visibleSessions for most recent date on initial load
        if (isInitialLoad && mostRecentDate) {
            groups[mostRecentDate].forEach(drone => {
                const sessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
                this.visibleSessions.add(sessionKey);
            });
        }

        const esc = (v) => this.escapeHtml(v);
        let html = '';
        sortedDates.forEach(date => {
            const flightCount = groups[date].length;
            const sortedSessions = groups[date].sort((a, b) =>
                new Date(b.timestamp) - new Date(a.timestamp)
            );

            const dateSessionKeys = sortedSessions.map(d => `${d.uas_id}:${d.computed_session_id || 'unknown'}`);
            const allVisible = dateSessionKeys.every(key => this.visibleSessions.has(key));
            const someVisible = dateSessionKeys.some(key => this.visibleSessions.has(key));
            const isIndeterminate = someVisible && !allVisible;

            if (isInitialLoad && date === mostRecentDate) {
                this.expandedDates.add(date);
            }
            const isExpanded = this.expandedDates.has(date);

            html += `
                <div class="date-group" data-date="${esc(date)}">
                    <div class="date-header">
                        <input type="checkbox" class="date-checkbox" data-date="${esc(date)}" ${allVisible ? 'checked' : ''} ${isIndeterminate ? 'data-indeterminate="true"' : ''}>
                        <span class="date-label">${esc(date)}</span>
                        <span class="date-count">${flightCount} flight${flightCount !== 1 ? 's' : ''}</span>
                        <i class="fas fa-chevron-${isExpanded ? 'down' : 'right'} date-chevron"></i>
                    </div>
                    <div class="date-items ${isExpanded ? '' : 'collapsed'}">
                        ${sortedSessions.map(drone => {
                            const color = MapController.getDroneColor(drone.uas_id);
                            const altitude = drone.altitude !== null && drone.altitude !== undefined
                                ? Units.formatAltitude(drone.altitude, true, 0)
                                : 'N/A';
                            const time = new Date(drone.timestamp);
                            const timeStr = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

                            const rawSessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
                            const sessionId = drone.computed_session_id ? drone.computed_session_id.replace('session_', '') : '';
                            const isSelected = this.selectedDrones.has(rawSessionKey);
                            const isVisible = this.visibleSessions.has(rawSessionKey);

                            return `
                                <div class="drone-item ${isSelected ? 'active' : ''} ${isVisible ? '' : 'dimmed'}" data-uas-id="${esc(drone.uas_id)}" data-session-key="${esc(rawSessionKey)}" data-session-id="${esc(drone.computed_session_id || '')}">
                                    <input type="checkbox" class="drone-checkbox" data-session-key="${esc(rawSessionKey)}" ${isVisible ? 'checked' : ''}>
                                    <div class="drone-color" style="background-color: ${color};"></div>
                                    <div class="drone-info">
                                        <div class="drone-id">${esc(this.getDroneName(drone.uas_id))}</div>
                                        <div class="session-id">${esc(sessionId)}</div>
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

        // Set indeterminate state for date checkboxes
        list.querySelectorAll('.date-checkbox[data-indeterminate="true"]').forEach(cb => {
            cb.indeterminate = true;
        });

        // Load tracks for initially visible sessions (most recent date)
        if (isInitialLoad && mostRecentDate) {
            const mostRecentSessions = groups[mostRecentDate];
            const dateStart = new Date(mostRecentDate + 'T00:00:00');
            const dateEnd = new Date(mostRecentDate + 'T23:59:59.999');

            for (const drone of mostRecentSessions) {
                const sessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
                const sessionId = drone.computed_session_id;
                if (sessionId && !this.loadedTracks.has(sessionKey)) {
                    MapController.loadTrackSession(drone.uas_id, sessionId, dateStart, dateEnd);
                    this.loadedTracks.add(sessionKey);
                }
            }
        }

        // Add date checkbox handlers
        list.querySelectorAll('.date-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                const date = e.target.dataset.date;
                const group = e.target.closest('.date-group');
                const droneCheckboxes = group.querySelectorAll('.drone-checkbox');
                const isChecked = e.target.checked;

                // Update all drone checkboxes in this group
                droneCheckboxes.forEach(dc => {
                    dc.checked = isChecked;
                    const sessionKey = dc.dataset.sessionKey;
                    const droneItem = dc.closest('.drone-item');

                    if (isChecked) {
                        this.visibleSessions.add(sessionKey);
                        droneItem.classList.remove('dimmed');
                        // Load track if not already loaded
                        if (!this.loadedTracks.has(sessionKey)) {
                            const uasId = droneItem.dataset.uasId;
                            const sessionId = droneItem.dataset.sessionId;
                            const dateStart = new Date(date + 'T00:00:00');
                            const dateEnd = new Date(date + 'T23:59:59.999');
                            if (sessionId) {
                                MapController.loadTrackSession(uasId, sessionId, dateStart, dateEnd);
                                this.loadedTracks.add(sessionKey);
                            }
                        }
                    } else {
                        this.visibleSessions.delete(sessionKey);
                        droneItem.classList.add('dimmed');
                        // Remove track
                        const uasId = droneItem.dataset.uasId;
                        MapController.removeTrack(uasId, sessionKey);
                        this.loadedTracks.delete(sessionKey);

                        // Close detail panel if this session is currently selected
                        if (this.selectedSession === droneItem.dataset.sessionId) {
                            this._closeDetailPanel();
                        }
                    }
                });

                e.target.indeterminate = false;
            });
        });

        // Add date header click handlers (toggle expand/collapse only, no checkbox change)
        list.querySelectorAll('.date-header').forEach(header => {
            header.addEventListener('click', async (e) => {
                // Don't toggle if clicking the checkbox
                if (e.target.classList.contains('date-checkbox')) {
                    return;
                }

                const group = header.closest('.date-group');
                const items = group.querySelector('.date-items');
                const chevron = header.querySelector('.date-chevron');
                const dateStr = group.dataset.date;

                if (items.classList.contains('collapsed')) {
                    items.classList.remove('collapsed');
                    chevron.classList.remove('fa-chevron-right');
                    chevron.classList.add('fa-chevron-down');
                    this.expandedDates.add(dateStr);
                } else {
                    items.classList.add('collapsed');
                    chevron.classList.remove('fa-chevron-down');
                    chevron.classList.add('fa-chevron-right');
                    this.expandedDates.delete(dateStr);
                }
            });
        });

        // Add drone checkbox handlers
        list.querySelectorAll('.drone-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                e.stopPropagation();
                const sessionKey = e.target.dataset.sessionKey;
                const droneItem = e.target.closest('.drone-item');
                const uasId = droneItem.dataset.uasId;
                const sessionId = droneItem.dataset.sessionId;
                const group = droneItem.closest('.date-group');
                const dateStr = group.dataset.date;
                const isChecked = e.target.checked;

                if (isChecked) {
                    this.visibleSessions.add(sessionKey);
                    droneItem.classList.remove('dimmed');
                    // Load track
                    if (!this.loadedTracks.has(sessionKey)) {
                        const dateStart = new Date(dateStr + 'T00:00:00');
                        const dateEnd = new Date(dateStr + 'T23:59:59.999');
                        if (sessionId) {
                            MapController.loadTrackSession(uasId, sessionId, dateStart, dateEnd);
                            this.loadedTracks.add(sessionKey);
                        }
                    }
                } else {
                    this.visibleSessions.delete(sessionKey);
                    droneItem.classList.add('dimmed');
                    // Remove track
                    MapController.removeTrack(uasId, sessionKey);
                    this.loadedTracks.delete(sessionKey);

                    // Close detail panel if this session is currently selected
                    if (this.selectedSession === sessionId) {
                        this._closeDetailPanel();
                    }
                }

                // Update date checkbox state
                this._updateDateCheckboxState(group);
            });
        });

        // Add click handlers for drone items (open detail panel only if visible)
        list.querySelectorAll('.drone-item').forEach(item => {
            item.addEventListener('click', (e) => {
                // Don't open detail if clicking checkbox or focus button
                if (e.target.classList.contains('drone-checkbox') ||
                    e.target.closest('.focus-btn')) {
                    return;
                }

                const uasId = item.dataset.uasId;
                const sessionKey = item.dataset.sessionKey;
                const sessionId = item.dataset.sessionId;

                // Only open detail if this session is visible (checked)
                if (!this.visibleSessions.has(sessionKey)) {
                    return;
                }

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

                // Toggle detail panel - close if same session clicked, open otherwise
                const isSameSession = this.selectedDrone === uasId && this.selectedSession === sessionId;
                if (isSameSession) {
                    this._closeDetailPanel();
                } else {
                    this._openDetailPanel(uasId, sessionId);
}

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
                const group = item.closest('.date-group');
                const dateStr = group ? group.dataset.date : null;

                // Only focus if visible
                if (!this.visibleSessions.has(sessionKey)) {
                    return;
                }

                MapController.panToDrone(uasId);

                // Also load the track for this session if not loaded
                if (sessionId && !this.loadedTracks.has(sessionKey)) {
                    const dateStart = new Date(dateStr + 'T00:00:00');
                    const dateEnd = new Date(dateStr + 'T23:59:59.999');
                    MapController.loadTrackSession(uasId, sessionId, dateStart, dateEnd);
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
     * Update the date checkbox state based on its sessions
     */
    _updateDateCheckboxState(group) {
        const dateCheckbox = group.querySelector('.date-checkbox');
        const droneCheckboxes = group.querySelectorAll('.drone-checkbox');

        const checkedCount = Array.from(droneCheckboxes).filter(cb => cb.checked).length;
        const totalCount = droneCheckboxes.length;

        if (checkedCount === 0) {
            dateCheckbox.checked = false;
            dateCheckbox.indeterminate = false;
        } else if (checkedCount === totalCount) {
            dateCheckbox.checked = true;
            dateCheckbox.indeterminate = false;
        } else {
            dateCheckbox.checked = false;
            dateCheckbox.indeterminate = true;
        }
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
        const displayName = this.getDroneName(uasId);

        if (sessionId) {
            const shortSessionId = sessionId.replace('session_', '');
            const nameSpan = document.createElement('span');
            nameSpan.textContent = displayName;
            const small = document.createElement('small');
            small.textContent = shortSessionId;
            this.elements.detailUasId.innerHTML = '';
            this.elements.detailUasId.appendChild(nameSpan);
            this.elements.detailUasId.appendChild(document.createElement('br'));
            this.elements.detailUasId.appendChild(small);
        } else {
            this.elements.detailUasId.textContent = displayName;
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
     * Close the settings panel
     */
    _closeSettingsPanel() {
        this.elements.settingsPanel.classList.remove('open');
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
        this.elements.detailMaxAlt.textContent = maxAlt > 0 ? Units.formatAltitude(maxAlt, true, 0) : 'N/A';

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

        this.elements.detailDistance.textContent = Units.formatDistance(totalDistance);

        this.elements.detailMaxSpeed.textContent = maxSpeed > 0
            ? Units.formatSpeed(maxSpeed)
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
            // Convert altitude for display
            const displayAlt = Units.useMetric ? a : a * 3.28084;
            ctx.fillText(`${displayAlt.toFixed(0)}`, padding.left - 4, y + 3);
        }

        // Altitude label
        ctx.save();
        ctx.translate(10, height / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.fillStyle = '#495057';
        ctx.font = '11px sans-serif';
        const altUnit = Units.getAltitudeUnit();
        ctx.fillText(`Altitude (${altUnit})`, 0, 0);
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
