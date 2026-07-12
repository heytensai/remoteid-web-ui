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
    replayActive: false,
    replayPlaying: false,
    replaySpeed: 4,
    selectedDrone: null,
    selectedDroneTrack: null,
    visibleSessions: new Set(), // Track which sessions are checked/visible
    dismissedSessionKeys: new Set(), // Sessions manually unchecked by user
    showKnownDrones: true,
    showUnknownDrones: true,
    showGeozoneAlerts: false,
    alertEvents: [],
    alertLogModalOpen: false,
    alertLogPage: 0,
    alertLogLimit: 50,
    alertLogTotal: 0,
    expandedGroups: new Set(),
    viewMode: 'date', // 'date' or 'uas'
    remotes: [],
    remoteDetailOpen: false,
    _suppressTimeChange: false,
    wakeLock: null,
    keepScreenOn: false,
    notificationsEnabled: false,

    // Auth state
    permissions: [],
    currentUser: null,

    // Load More state for UAS view
    uasExtraSessions: {}, // { [uasId]: [drone, ...] } - extra sessions loaded via Load More
    uasExtraTotal: {},    // { [uasId]: total_count } - total sessions available
    uasExtraLoading: {},  // { [uasId]: true/false } - currently loading

    // Adaptive polling
    _initialized: false,
    pollTimer: null,
    pollFastMs: 2000,
    pollSlowMs: 10000,
    pollActivityThresholdMs: 300000,
    _pollMode: 'slow',
    lastActivityTime: null,
    _wasPolling: false,

    // Incremental update tracking
    droneTimestamps: {}, // Map of "uas_id:session_id" -> last known timestamp

    // DOM Elements
    elements: {},

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    /**
     * Check if the current user has a given permission.
     * The ``*`` wildcard grants all permissions.
     */
    hasPermission(permission) {
        return this.permissions.indexOf('*') !== -1 || this.permissions.indexOf(permission) !== -1;
    },

    /**
     * Initialize authentication: handle login tokens from URL, validate
     * existing session tokens, or create an ephemeral account.
     */
    async _initAuth() {
        // 1. Check for login token in URL parameters
        const params = new URLSearchParams(window.location.search);
        const loginToken = params.get('login_token');
        if (loginToken) {
            try {
                const result = await API.loginWithToken(loginToken);
                API.setAuthToken(result.token);
                // Clean the URL (remove login_token param) without reloading
                const url = new URL(window.location);
                url.searchParams.delete('login_token');
                window.history.replaceState({}, '', url);
            } catch (e) {
                console.warn('[Auth] Login token exchange failed:', e);
                // Token invalid/expired — clear and fall through to anon
                API.setAuthToken(null);
            }
        }

        // 2. If we have a stored token, validate it
        const storedToken = API.getAuthToken();
        if (storedToken) {
            try {
                const me = await API.getMe();
                if (me.authenticated) {
                    this.currentUser = me.user;
                    this.permissions = me.permissions || [];
                    return;
                }
            } catch (e) {
                console.warn('[Auth] Token validation failed:', e);
            }
            // Token expired or invalid — clear it
            API.setAuthToken(null);
            this.currentUser = null;
            this.permissions = [];
        }

        // 3. No valid token — create an ephemeral account
        try {
            const result = await API.anonLogin();
            API.setAuthToken(result.token);
            this.currentUser = result.user;
            this.permissions = ['view_map', 'view_drones', 'view_tracks',
                'view_operators', 'view_waypoints', 'use_replay'];
            console.info('[Auth] Ephemeral user created:', result.user.name);
        } catch (e) {
            console.warn('[Auth] Ephemeral account creation failed, proceeding without auth:', e);
        }
    },

    /**
     * Initialize UI
     */
    async init() {
        this._cacheElements();
        this._initEventListeners();

        // Auth initialization
        await this._initAuth();

        await this._loadConfig();
        this._restoreSettings();

        // Explicitly ensure keepScreenOn is off on every page load
        this.keepScreenOn = false;
        this.elements.keepScreenOnCheckbox.checked = false;
        this._releaseWakeLock();

        await this._initTimePicker();

        // Handle visibility change for wake lock re-acquisition
        document.addEventListener('visibilitychange', () => this._handleVisibilityChange());

        // Release wake lock on page unload
        window.addEventListener('beforeunload', () => this._releaseWakeLock());

        // Wait for MapController to be ready
        await this._waitForMapController();

        // Apply settings that depend on MapController
        if (this._pendingSettings) {
            if (this._pendingSettings.showOperators !== undefined) {
                MapController.toggleOperators(this._pendingSettings.showOperators);
            }
            if (this._pendingSettings.showTracks !== undefined) {
                MapController.toggleTracks(this._pendingSettings.showTracks);
            }
            if (this._pendingSettings.showFixedCollectors !== undefined) {
                MapController.toggleFixedCollectors(this._pendingSettings.showFixedCollectors);
            }
            if (this._pendingSettings.showMobileCollectors !== undefined) {
                MapController.toggleMobileCollectors(this._pendingSettings.showMobileCollectors);
            }
            if (this._pendingSettings.trackOpacity !== undefined) {
                MapController.setTrackOpacity(this._pendingSettings.trackOpacity);
            }
            if (this._pendingSettings.darkMode !== undefined) {
                document.body.classList.toggle('dark-mode', this._pendingSettings.darkMode);
            }
            this._pendingSettings = null;
        }

        // Populate waypoints dropdown and mobile list
        this._populateWaypointsLists();

        // Clear any existing markers before loading data
        MapController.clearAllTracks();
        MapController.clearAllOperators();
        await this.refreshData(false);
        this._initialized = true;
        this._applyPermissionGating();
        this._startPolling();
        // Register service worker (fire-and-forget)
        this._registerServiceWorker();
    },

    /**
     * Show/hide UI elements based on the current user's permissions.
     */
    _applyPermissionGating() {
        const show = (sel, visible) => {
            const el = document.querySelector(sel);
            if (el) el.style.display = visible ? '' : 'none';
        };
        show('.analytics-dropdown', this.hasPermission('view_stats'));
        show('.remote-bar', this.hasPermission('view_sources'));
        show('.waypoints-dropdown', this.hasPermission('view_waypoints'));
        show('#settingsPanel', this.hasPermission('view_settings'));
        show('#openSettings', this.hasPermission('view_settings'));
        show('#openAlertLogDropdown', this.hasPermission('view_alert_history'));

        // Push notifications toggle
        const notifyCheckbox = this.elements.enableNotificationsCheckbox;
        if (notifyCheckbox) {
            const container = notifyCheckbox.closest('.settings-item');
            if (container) {
                container.style.display = this.hasPermission('receive_notifications') ? '' : 'none';
            }
        }
    },

    /**
     * Start adaptive polling for data refreshes
     */
    _startPolling() {
        if (this.pollTimer) return;
        console.debug('[Poll] Starting timer (10s slow mode)');
        this._pollMode = 'slow';
        this.pollTimer = setInterval(() => this.refreshData(false), this.pollSlowMs);
    },

    /**
     * Stop adaptive polling
     */
    _stopPolling() {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
            console.debug('[Poll] Timer stopped');
        }
    },

    /**
     * Switch to fast (2s) polling interval
     */
    _switchToFastPoll() {
        if (!this.pollTimer || this._pollMode === 'fast') return;
        console.log(`[Poll] Switching to FAST (${this.pollFastMs}ms)`);
        this._pollMode = 'fast';
        clearInterval(this.pollTimer);
        this.pollTimer = setInterval(() => this.refreshData(false), this.pollFastMs);
    },

    /**
     * Switch to slow (10s) polling interval
     */
    _switchToSlowPoll() {
        if (!this.pollTimer || this._pollMode === 'slow') return;
        console.log('[Poll] Switching to SLOW (10s)');
        this._pollMode = 'slow';
        this.pollTimer = setInterval(() => this.refreshData(false), this.pollSlowMs);
    },

    /**
     * Adjust poll timer based on recent activity:
     * fast (2s) if activity within threshold, slow (10s) otherwise
     */
    _adjustPollTimer() {
        if (!this.pollTimer) return;
        const now = Date.now();
        const idle = !this.lastActivityTime || (now - this.lastActivityTime > this.pollActivityThresholdMs);
        if (idle) {
            this._switchToSlowPoll();
        } else {
            this._switchToFastPoll();
        }
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
            startTimeInput: document.getElementById('startTime'),
            endTimeInput: document.getElementById('endTime'),
            lastUpdateSpan: document.getElementById('lastUpdate'),
            showOperatorsCheckbox: document.getElementById('showOperators'),
            showTracksCheckbox: document.getElementById('showTracks'),
            showFixedCollectorsCheckbox: document.getElementById('showFixedCollectors'),
            showMobileCollectorsCheckbox: document.getElementById('showMobileCollectors'),
            trackOpacitySlider: document.getElementById('trackOpacity'),
            timePresets: document.querySelectorAll('.header-time-presets button'),
            settingsPanel: document.getElementById('settingsPanel'),

            openSettingsBtn: document.getElementById('openSettings'),
            closeSettingsBtn: document.getElementById('closeSettings'),
            opacityValue: document.getElementById('opacityValue'),
            showKnownDrones: document.getElementById('showKnownDrones'),
            showUnknownDrones: document.getElementById('showUnknownDrones'),
            darkModeCheckbox: document.getElementById('darkMode'),
            keepScreenOnCheckbox: document.getElementById('keepScreenOn'),
            notificationToggle: document.getElementById('enableNotifications'),
            startTimeMInput: document.getElementById('startTimeM'),
            endTimeMInput: document.getElementById('endTimeM'),
            settingsTimePresets: document.querySelectorAll('.settings-time-presets button'),
            waypointsBtn: document.getElementById('waypointsBtn'),
            waypointsDropdown: document.getElementById('waypointsDropdown'),
            waypointsList: document.getElementById('waypointsList'),
            geozoneAlertFilter: document.getElementById('geozoneAlertFilter'),
            alertFilterCount: document.getElementById('alertFilterCount'),
            analyticsBtn: document.getElementById('analyticsBtn'),
            analyticsDropdown: document.getElementById('analyticsDropdown'),
            openAlertLogBtn: document.getElementById('openAlertLogDropdown'),
            alertLogModal: document.getElementById('alertLogModal'),
            closeAlertLogBtn: document.getElementById('closeAlertLog'),
            alertLogBody: document.getElementById('alertLogBody'),
            alertLogTotal: document.getElementById('alertLogTotal'),
            alertLogPageInfo: document.getElementById('alertLogPageInfo'),
            alertLogPrev: document.getElementById('alertLogPrev'),
            alertLogNext: document.getElementById('alertLogNext'),
            alertLogSearchBtn: document.getElementById('alertLogSearchBtn'),
            alertLogUasFilter: document.getElementById('alertLogUasFilter'),
            alertLogGeozoneFilter: document.getElementById('alertLogGeozoneFilter'),
            alertLogFromDate: document.getElementById('alertLogFromDate'),
            alertLogToDate: document.getElementById('alertLogToDate'),
            alertLogExportBtn: document.getElementById('alertLogExportBtn'),
            statDrones: document.getElementById('statDrones'),
            statSessions: document.getElementById('statSessions'),
            statPositions: document.getElementById('statPositions'),
            statActiveAlerts: document.getElementById('statActiveAlerts'),
            statTotalAlerts: document.getElementById('statTotalAlerts'),
            remoteBar: document.getElementById('remoteBar'),
            remoteSummary: document.getElementById('remoteSummary'),
            remoteDetail: document.getElementById('remoteDetail'),
            remoteDetailBody: document.getElementById('remoteDetailBody'),
            replayPlayBtn: document.getElementById('replayPlayBtn'),
            replayControls: document.getElementById('replayControls'),
            replayPlayPauseBtn: document.getElementById('replayPlayPauseBtn'),
            replayStopBtn: document.getElementById('replayStopBtn'),
            replaySpeedBtns: document.querySelectorAll('.replay-speed-btn'),
            replayTimeline: document.getElementById('replayTimeline'),
            replayTimeDisplay: document.getElementById('replayTimeDisplay'),
        };

    },

    /**
     * Initialize event listeners
     */
    _initEventListeners() {
        // Sidebar toggle
        this.elements.openSidebarBtn.addEventListener('click', () => {
            if (window.innerWidth < 768) {
                this.elements.sidebar.classList.add('open');
            } else {
                this.elements.sidebar.classList.toggle('collapsed');
                setTimeout(() => MapController.map.invalidateSize(), 350);
            }
        });

        this.elements.closeSidebarBtn.addEventListener('click', () => {
            if (window.innerWidth < 768) {
                this.elements.sidebar.classList.remove('open');
            } else {
                this.elements.sidebar.classList.add('collapsed');
                setTimeout(() => MapController.map.invalidateSize(), 350);
            }
        });

        // Refresh button
        this.elements.refreshBtn.addEventListener('click', () => {
            this.refreshData();
        });

        // Close detail panel
        this.elements.closeDetailBtn.addEventListener('click', () => {
            this._closeDetailPanel();
        });

        // Replay play button (sidebar header)
        this.elements.replayPlayBtn.addEventListener('click', () => {
            this._startReplay();
        });

        // Replay play/pause button
        this.elements.replayPlayPauseBtn.addEventListener('click', () => {
            if (this.replayPlaying) {
                MapController.pauseReplay();
            } else {
                MapController.resumeReplay();
            }
        });

        // Replay stop button
        this.elements.replayStopBtn.addEventListener('click', () => {
            MapController.stopReplay();
        });

        // Replay speed buttons
        this.elements.replaySpeedBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const speed = parseInt(e.currentTarget.dataset.speed);
                this.elements.replaySpeedBtns.forEach(b => b.classList.remove('active'));
                e.currentTarget.classList.add('active');
                this.replaySpeed = speed;
                MapController.setReplaySpeed(speed);
            });
        });

        // Replay timeline seek
        this.elements.replayTimeline.addEventListener('input', (e) => {
            if (!this.replayActive) return;
            const val = parseFloat(e.currentTarget.value);
            const max = parseFloat(e.currentTarget.max);
            if (max > 0) {
                MapController.seekReplay(val);
            }
        });

        // Time preset buttons (header)
        this.elements.timePresets.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const hours = parseInt(e.currentTarget.dataset.hours);
                this._setStoredPreset(hours);
                this._setTimeRange(hours);
                this.droneTimestamps = {}; // Clear timestamps for new time window
                this.refreshData();
            });
        });

        // Time preset buttons (settings panel / mobile)
        if (this.elements.settingsTimePresets) {
            this.elements.settingsTimePresets.forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const hours = parseInt(e.currentTarget.dataset.hours);
                    this._setStoredPreset(hours);
                    this._setTimeRange(hours);
                    this.droneTimestamps = {}; // Clear timestamps for new time window
                    this.refreshData();
                });
            });
        }
    
        // Show/hide operators
        this.elements.showOperatorsCheckbox.addEventListener('change', (e) => {
            MapController.toggleOperators(e.target.checked);
            this._saveSettings();
        });

        // Show/hide tracks
        this.elements.showTracksCheckbox.addEventListener('change', (e) => {
            MapController.toggleTracks(e.target.checked);
            this._saveSettings();
        });

        // Show/hide fixed collectors
        this.elements.showFixedCollectorsCheckbox.addEventListener('change', (e) => {
            MapController.toggleFixedCollectors(e.target.checked);
            this._saveSettings();
        });

        // Show/hide mobile collectors
        this.elements.showMobileCollectorsCheckbox.addEventListener('change', (e) => {
            MapController.toggleMobileCollectors(e.target.checked);
            this._saveSettings();
        });

        // Track opacity
        let opacityTimeout = null;
        this.elements.trackOpacitySlider.addEventListener('input', (e) => {
            if (opacityTimeout) {
                clearTimeout(opacityTimeout);
            }
            opacityTimeout = setTimeout(() => {
                MapController.setTrackOpacity(e.target.value);
                this._saveSettings();
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
            this._saveSettings();
        });

        this.elements.showUnknownDrones.addEventListener('change', (e) => {
            this.showUnknownDrones = e.target.checked;
            this.refreshData();
            this._saveSettings();
        });

        // Dark mode toggle
        this.elements.darkModeCheckbox.addEventListener('change', (e) => {
            this._toggleDarkMode(e.target.checked);
            this._saveSettings();
        });

        // Keep screen on toggle
        this.elements.keepScreenOnCheckbox.addEventListener('change', (e) => {
            this._toggleKeepScreenOn(e.target.checked);
            this._saveSettings();
        });

        // Notifications toggle
        this.elements.notificationToggle.addEventListener('change', (e) => {
            this._toggleNotifications(e.target.checked);
            this._saveSettings();
        });

        // Close sidebar when clicking on map (mobile)
        document.addEventListener('click', (e) => {
            if (window.innerWidth < 768 &&
                !this.elements.sidebar.contains(e.target) &&
                !this.elements.openSidebarBtn.contains(e.target)) {
                this.elements.sidebar.classList.remove('open');
            }
        });

        // View tab toggle (sidebar view mode)
        document.querySelectorAll('.view-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                this._switchView(tab.dataset.view);
            });
        });

        // Waypoints dropdown toggle
        this.elements.waypointsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.elements.waypointsDropdown.classList.toggle('open');
        });

        // Close waypoints dropdown on outside click
        document.addEventListener('click', (e) => {
            if (!this.elements.waypointsDropdown) return;
            if (!this.elements.waypointsDropdown.contains(e.target) &&
                e.target !== this.elements.waypointsBtn &&
                !this.elements.waypointsBtn.contains(e.target)) {
                this.elements.waypointsDropdown.classList.remove('open');
            }
        });

        // Waypoints dropdown item click (event delegation)
        this.elements.waypointsList.addEventListener('click', (e) => {
            const item = e.target.closest('.dropdown-item');
            if (!item) return;
            const name = item.dataset.wpName;
            if (name) {
                MapController.panToWaypoint(name);
                this.elements.waypointsDropdown.classList.remove('open');
            }
        });

        // Analytics dropdown toggle
        this.elements.analyticsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.elements.analyticsDropdown.classList.toggle('open');
        });

        // Close analytics dropdown on outside click
        document.addEventListener('click', (e) => {
            if (!this.elements.analyticsDropdown) return;
            if (!this.elements.analyticsDropdown.contains(e.target) &&
                e.target !== this.elements.analyticsBtn &&
                !this.elements.analyticsBtn.contains(e.target)) {
                this.elements.analyticsDropdown.classList.remove('open');
            }
        });

        // Remote sources status bar toggle
        this.elements.remoteBar.addEventListener('click', () => {
            this.remoteDetailOpen = !this.remoteDetailOpen;
            this.elements.remoteDetail.style.display = this.remoteDetailOpen ? 'block' : 'none';
        });

        // Geozone alert filter toggle
        this.elements.geozoneAlertFilter.addEventListener('change', () => {
            this.showGeozoneAlerts = this.elements.geozoneAlertFilter.checked;
            this.refreshData();
        });

        // Alert log modal
        this.elements.openAlertLogBtn.addEventListener('click', () => {
            this._openAlertLog();
        });

        this.elements.closeAlertLogBtn.addEventListener('click', () => {
            this.alertLogModalOpen = false;
            this.elements.alertLogModal.style.display = 'none';
        });

        this.elements.alertLogModal.addEventListener('click', (e) => {
            if (e.target === this.elements.alertLogModal) {
                this.alertLogModalOpen = false;
                this.elements.alertLogModal.style.display = 'none';
            }
        });

        this.elements.alertLogSearchBtn.addEventListener('click', () => {
            this.alertLogPage = 0;
            this._loadAlertLog();
        });

        this.elements.alertLogPrev.addEventListener('click', () => {
            if (this.alertLogPage > 0) {
                this.alertLogPage--;
                this._loadAlertLog();
            }
        });

        this.elements.alertLogNext.addEventListener('click', () => {
            if ((this.alertLogPage + 1) * this.alertLogLimit < this.alertLogTotal) {
                this.alertLogPage++;
                this._loadAlertLog();
            }
        });

        this.elements.alertLogExportBtn.addEventListener('click', () => {
            this._exportAlertLog();
        });

        // Enter key in filter inputs triggers search
        this.elements.alertLogUasFilter.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                this.alertLogPage = 0;
                this._loadAlertLog();
            }
        });
        this.elements.alertLogGeozoneFilter.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                this.alertLogPage = 0;
                this._loadAlertLog();
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

        this._updateActivePresetForHours(this.defaultHours);

        const config = {
            enableTime: true,
            dateFormat: 'Y-m-d H:i',
            time_24hr: true,
            onChange: (selectedDates, dateStr, instance) => {
                if (this._suppressTimeChange) return;
                this._clearActivePreset();
                this._clearStoredPreset();
                if (instance.element.id === 'startTime' || instance.element.id === 'startTimeM') {
                    this.currentStartTime = selectedDates[0];
                } else {
                    this.currentEndTime = selectedDates[0];
                }
                this.droneTimestamps = {};
                this.refreshData();
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

        // Also init mobile (settings panel) time inputs if they exist
        if (this.elements.startTimeMInput) {
            flatpickr(this.elements.startTimeMInput, {
                ...config,
                defaultDate: startTime
            });
        }
        if (this.elements.endTimeMInput) {
            flatpickr(this.elements.endTimeMInput, {
                ...config,
                defaultDate: endTime
            });
        }
    },

    /**
     * Load configuration from server
     */
    async _loadConfig() {
        try {
            const config = await API.getConfig();
            this.defaultHours = config.default_hours || 24;
            this.droneAliases = config.drone_aliases || {};
            this.manufacturerPrefixes = config.manufacturer_prefixes || {};
            this.positionStaleMinutes = config.position_stale_minutes || 30;
            this.vapidPublicKey = config.vapid_public_key || null;

            // Merge server-side permissions if available (auth from middleware)
            if (config.auth && config.auth.authenticated) {
                this.isAuthenticated = true;
                if (config.auth.permissions) {
                    this.permissions = config.auth.permissions;
                }
            }

            // Override default with stored preset if available
            const stored = this._getStoredPreset();
            if (stored !== null) {
                this.defaultHours = stored;
            }

            // Load remote sources status
            await this._loadRemoteStatus();

            // Initialize units from config
            Units.init(config);

        } catch (e) {
            console.error('Failed to load config:', e);
        }
    },

    _populateWaypointsLists() {
        const wpList = this.elements.waypointsList;
        if (!wpList) return;

        const waypoints = MapController.waypoints || [];
        const enabled = waypoints.filter(wp => wp.enabled !== false);

        if (enabled.length === 0) {
            wpList.innerHTML = '<div class="dropdown-empty">No waypoints configured</div>';
            return;
        }

        const esc = (v) => this.escapeHtml(v);
        let html = '';
        for (const wp of enabled) {
            const icon = wp.icon || 'fa-map-pin';
            const color = wp.color || '#007bff';
            html += `
                <div class="dropdown-item" data-wp-name="${esc(wp.name)}">
                    <span class="dropdown-item-icon" style="color: ${color};">
                        <i class="fas ${esc(icon)}"></i>
                    </span>
                    <span class="dropdown-item-name">${esc(wp.name)}</span>
                </div>
            `;
        }
        wpList.innerHTML = html;
    },

    _getStoredPreset() {
        try {
            const val = localStorage.getItem('remoteid_time_preset');
            return val !== null ? parseInt(val, 10) : null;
        } catch {
            return null;
        }
    },

    _setStoredPreset(hours) {
        try {
            localStorage.setItem('remoteid_time_preset', String(hours));
        } catch {
            // localStorage unavailable
        }
    },

    _clearStoredPreset() {
        try {
            localStorage.removeItem('remoteid_time_preset');
        } catch {
            // localStorage unavailable
        }
    },

    _toggleDarkMode(enabled) {
        document.body.classList.toggle('dark-mode', enabled);
    },

    _toggleKeepScreenOn(enabled) {
        this.keepScreenOn = enabled;
        if (enabled) {
            this._requestWakeLock();
        } else {
            this._releaseWakeLock();
        }
    },

    _toggleNotifications(enabled) {
        this.notificationsEnabled = enabled;
        if (enabled) {
            this._subscribePush();
        } else {
            this._unsubscribePush();
        }
    },

    _urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/-/g, '+')
            .replace(/_/g, '/');
        const rawData = window.atob(base64);
        return Uint8Array.from([...rawData].map(ch => ch.charCodeAt(0)));
    },

    _arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return window.btoa(binary);
    },

    _registerServiceWorker() {
        if (!('serviceWorker' in navigator)) return;
        const swUrl = API.baseUrl + '/sw.js';
        console.debug('[SW] Registering', swUrl);
        navigator.serviceWorker.register(swUrl).catch(function(e) {
            console.error('[SW] Registration failed:', e);
        });
    },

    async _initServiceWorker() {
        if (!('serviceWorker' in navigator)) return null;
        try {
            // If already registered via _registerServiceWorker, get the ready state
            const reg = await Promise.race([
                navigator.serviceWorker.ready,
                new Promise((_, reject) =>
                    setTimeout(() => reject(new Error('SW ready timeout')), 10000)
                ),
            ]);
            return reg;
        } catch (e) {
            console.warn('_initServiceWorker: ready timed out, registering fresh:', e);
        }
        try {
            const registrations = await navigator.serviceWorker.getRegistrations();
            for (const reg of registrations) {
                if (reg.active) continue;
                console.log('_initServiceWorker: unregistering stale SW:', reg.scope);
                await reg.unregister();
            }
            const swUrl = API.baseUrl + '/sw.js';
            console.log('_initServiceWorker: registering', swUrl);
            const reg = await navigator.serviceWorker.register(swUrl);
            return await Promise.race([
                new Promise((resolve, reject) => {
                    if (reg.active) return resolve(reg);
                    const sw = reg.installing || reg.waiting;
                    if (!sw) return resolve(reg);
                    sw.addEventListener('statechange', () => {
                        if (sw.state === 'activated') resolve(reg);
                        if (sw.state === 'redundant') reject(new Error('SW redundant'));
                    });
                }),
                new Promise((_, reject) =>
                    setTimeout(() => reject(new Error('New SW activation timeout')), 10000)
                ),
            ]);
        } catch (e2) {
            console.error('_initServiceWorker: recovery failed:', e2);
            return null;
        }
    },

    async _subscribePush() {
        if (this._pushSubscribing) return;
        try {
            this._pushSubscribing = true;
            if (!this.vapidPublicKey) {
                console.warn('Push not supported: no VAPID public key from server');
                return;
            }
            if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
                console.warn('Push not supported: browser lacks PushManager');
                return;
            }
            if (Notification.permission === 'denied') {
                console.warn('Push not supported: notifications are blocked in browser settings');
                this._showToast('Notifications are blocked. Enable them in your browser site settings.', 5000);
                this.notificationsEnabled = false;
                if (this.elements.notificationToggle) {
                    this.elements.notificationToggle.checked = false;
                }
                return;
            }
            // Request permission explicitly so we control the UX
            if (Notification.permission === 'default') {
                const result = await Notification.requestPermission();
                if (result !== 'granted') {
                    console.warn('Push not supported: notification permission denied');
                    this._showToast('Notification permission was denied. Enable it in your browser site settings.', 5000);
                    this.notificationsEnabled = false;
                    if (this.elements.notificationToggle) {
                        this.elements.notificationToggle.checked = false;
                    }
                    return;
                }
            }
            console.log('_subscribePush: permission=', Notification.permission, 'vapidKey=', this.vapidPublicKey.slice(0, 20));
            const registration = await this._initServiceWorker();
            if (!registration) {
                throw new Error('Service worker not available');
            }
            console.log('_subscribePush: service worker ready');
            let sub = await registration.pushManager.getSubscription();
            if (sub) {
                const knownKey = localStorage.getItem('vapidKeyFingerprint');
                if (knownKey === this.vapidPublicKey) {
                    console.log('_subscribePush: reusing existing subscription:', sub.endpoint);
                } else {
                    // VAPID key changed (e.g. server regenerated keys).
                    // Unsubscribe first so the new subscription is bound to
                    // the current key, otherwise push services will reject
                    // with BadJwtToken (key mismatch).
                    console.log('_subscribePush: removing stale subscription:', sub.endpoint);
                    await API._post('/api/push/unsubscribe', { endpoint: sub.endpoint }).catch(() => {});
                    await sub.unsubscribe();
                    console.log('_subscribePush: subscribing with current VAPID key...');
                    sub = await registration.pushManager.subscribe({
                        userVisibleOnly: true,
                        applicationServerKey: this._urlBase64ToUint8Array(this.vapidPublicKey),
                    });
                    console.log('Push subscribed:', sub.endpoint);
                }
            } else {
                console.log('_subscribePush: no existing subscription, subscribing...');
                sub = await registration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: this._urlBase64ToUint8Array(this.vapidPublicKey),
                });
                console.log('Push subscribed:', sub.endpoint);
            }
            localStorage.setItem('vapidKeyFingerprint', this.vapidPublicKey);
            const p256dh = sub.getKey('p256dh');
            const auth = sub.getKey('auth');
            if (!p256dh || !auth) {
                throw new Error('Push subscription missing encryption keys');
            }
            const result = await API._post('/api/push/subscribe', {
                endpoint: sub.endpoint,
                keys: {
                    p256dh: this._arrayBufferToBase64(p256dh),
                    auth: this._arrayBufferToBase64(auth),
                },
            });
            console.log('Push subscription sent to server:', result);
            this._showToast('Push notifications enabled');
        } catch (e) {
            console.error('Failed to subscribe push:', e);
            this.notificationsEnabled = false;
            if (this.elements.notificationToggle) {
                this.elements.notificationToggle.checked = false;
            }
        } finally {
            this._pushSubscribing = false;
        }
    },

    async _unsubscribePush() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
        try {
            const registration = await navigator.serviceWorker.ready;
            const sub = await registration.pushManager.getSubscription();
            if (sub) {
                await API._post('/api/push/unsubscribe', { endpoint: sub.endpoint });
                await sub.unsubscribe();
                console.log('Push unsubscribed');
            }
        } catch (e) {
            console.error('Failed to unsubscribe push:', e);
        }
    },

    async _requestWakeLock() {
        if (!('wakeLock' in navigator)) {
            console.warn('Wake Lock API not supported');
            this.elements.keepScreenOnCheckbox.checked = false;
            this.keepScreenOn = false;
            return;
        }
        try {
            this.wakeLock = await navigator.wakeLock.request('screen');
            this.wakeLock.addEventListener('release', () => {
                console.log('Wake lock released');
            });
            console.log('Wake lock acquired');
        } catch (err) {
            console.error('Failed to acquire wake lock:', err);
            this.elements.keepScreenOnCheckbox.checked = false;
            this.keepScreenOn = false;
        }
    },

    _releaseWakeLock() {
        if (this.wakeLock) {
            this.wakeLock.release().then(() => {
                this.wakeLock = null;
                console.log('Wake lock released');
            }).catch(err => {
                console.error('Failed to release wake lock:', err);
            });
        }
    },

    _handleVisibilityChange() {
        if (document.hidden) {
            console.debug('[Poll] Page hidden — pausing timer');
            this._wasPolling = !!this.pollTimer;
            this._stopPolling();
        } else {
            console.debug('[Poll] Page visible — resuming');

            // Safety: reset stuck loading state so refresh can proceed
            if (this.isLoading) {
                console.warn('[Poll] isLoading was stuck true — resetting');
                this.isLoading = false;
                this.elements.refreshBtn.classList.remove('spinning');
            }

            // Re-acquire wake lock if needed
            if (this.keepScreenOn && 'wakeLock' in navigator) {
                this._requestWakeLock();
            }

            // Resume polling and immediately fetch fresh data
            if (this._wasPolling) {
                this._wasPolling = false;
                this._startPolling();
                this.refreshData(false);
            }
        }
    },

    _saveSettings() {
        try {
            const settings = {
                showOperators: this.elements.showOperatorsCheckbox.checked,
                showTracks: this.elements.showTracksCheckbox.checked,
                showFixedCollectors: this.elements.showFixedCollectorsCheckbox.checked,
                showMobileCollectors: this.elements.showMobileCollectorsCheckbox.checked,
                trackOpacity: parseInt(this.elements.trackOpacitySlider.value, 10),
                showKnownDrones: this.elements.showKnownDrones.checked,
                showUnknownDrones: this.elements.showUnknownDrones.checked,
                darkMode: this.elements.darkModeCheckbox.checked,
                notificationsEnabled: this.elements.notificationToggle.checked,
            };
            localStorage.setItem('remoteid_settings', JSON.stringify(settings));
        } catch {
            // localStorage unavailable
        }
    },

    _restoreSettings() {
        try {
            const raw = localStorage.getItem('remoteid_settings');
            if (!raw) return;
            const saved = JSON.parse(raw);

            if (saved.showOperators !== undefined) {
                this.elements.showOperatorsCheckbox.checked = saved.showOperators;
            }
            if (saved.showTracks !== undefined) {
                this.elements.showTracksCheckbox.checked = saved.showTracks;
            }
            if (saved.showFixedCollectors !== undefined) {
                this.elements.showFixedCollectorsCheckbox.checked = saved.showFixedCollectors;
            }
            if (saved.showMobileCollectors !== undefined) {
                this.elements.showMobileCollectorsCheckbox.checked = saved.showMobileCollectors;
            }
            if (saved.trackOpacity !== undefined) {
                this.elements.trackOpacitySlider.value = saved.trackOpacity;
                if (this.elements.opacityValue) {
                    this.elements.opacityValue.textContent = saved.trackOpacity + '%';
                }
            }
            if (saved.showKnownDrones !== undefined) {
                this.elements.showKnownDrones.checked = saved.showKnownDrones;
                this.showKnownDrones = saved.showKnownDrones;
            }
            if (saved.showUnknownDrones !== undefined) {
                this.elements.showUnknownDrones.checked = saved.showUnknownDrones;
                this.showUnknownDrones = saved.showUnknownDrones;
            }
            if (saved.darkMode !== undefined) {
                this.elements.darkModeCheckbox.checked = saved.darkMode;
                // Defer tile switching to pending settings (needs MapController)
            }
            // keepScreenOn is intentionally NOT restored - always starts off
            if (saved.notificationsEnabled !== undefined) {
                this.elements.notificationToggle.checked = saved.notificationsEnabled;
                this.notificationsEnabled = saved.notificationsEnabled;
            }

            // Defer MapController-applied settings until after map is ready
            this._pendingSettings = {};
            if (saved.showOperators !== undefined) {
                this._pendingSettings.showOperators = saved.showOperators;
            }
            if (saved.showTracks !== undefined) {
                this._pendingSettings.showTracks = saved.showTracks;
            }
            if (saved.showFixedCollectors !== undefined) {
                this._pendingSettings.showFixedCollectors = saved.showFixedCollectors;
            }
            if (saved.showMobileCollectors !== undefined) {
                this._pendingSettings.showMobileCollectors = saved.showMobileCollectors;
            }
            if (saved.trackOpacity !== undefined) {
                this._pendingSettings.trackOpacity = saved.trackOpacity;
            }
            if (saved.darkMode !== undefined) {
                this._pendingSettings.darkMode = saved.darkMode;
            }
            // Initialize push notifications if previously enabled
            if (saved.notificationsEnabled && this.vapidPublicKey) {
                this._subscribePush();
            }
        } catch {
            // ignore
        }
    },

    /**
     * Get display name for drone (alias or uas_id)
     */
    getDroneName(uasId) {
        return this.droneAliases[uasId] || uasId;
    },

    /**
     * Format duration in HH:MM:SS format
     */
    formatDuration(seconds) {
        if (seconds === null || seconds === undefined || isNaN(seconds)) {
            return 'N/A';
        }
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            return `${hours}h ${minutes}m`;
        } else {
            return `${minutes}m ${secs}s`;
        }
    },

    /**
     * Get manufacturer name from UAS ID using serial prefix matching.
     * Returns null if unknown.
     */
    getDroneManufacturer(uasId) {
        if (!this.manufacturerPrefixes) return null;
        let bestMatch = null;
        let bestLen = 0;
        for (const [prefix, name] of Object.entries(this.manufacturerPrefixes)) {
            if (uasId.startsWith(prefix) && prefix.length > bestLen) {
                bestMatch = name;
                bestLen = prefix.length;
            }
        }
        return bestMatch;
    },

    /**
     * Open the alert log modal
     */
    _openAlertLog() {
        this.alertLogModalOpen = true;
        this.elements.alertLogModal.style.display = 'flex';
        this.alertLogPage = 0;

        // Initialize flatpickr on the date inputs if not already done
        if (!this._alertLogFromFp) {
            this._alertLogFromFp = flatpickr(this.elements.alertLogFromDate, {
                enableTime: true,
                dateFormat: 'Y-m-d H:i',
                time_24hr: true,
            });
        }
        if (!this._alertLogToFp) {
            this._alertLogToFp = flatpickr(this.elements.alertLogToDate, {
                enableTime: true,
                dateFormat: 'Y-m-d H:i',
                time_24hr: true,
            });
        }

        this._loadAlertLog();
    },

    /**
     * Fetch and render alert log from API
     */
    async _loadAlertLog() {
        const filters = {};
        const uasVal = this.elements.alertLogUasFilter.value.trim();
        if (uasVal) filters.uas_id = uasVal;
        const geoVal = this.elements.alertLogGeozoneFilter.value.trim();
        if (geoVal) filters.geozone_name = geoVal;
        if (this._alertLogFromFp && this._alertLogFromFp.selectedDates.length > 0) {
            filters.from = this._alertLogFromFp.selectedDates[0].toISOString();
        }
        if (this._alertLogToFp && this._alertLogToFp.selectedDates.length > 0) {
            filters.to = this._alertLogToFp.selectedDates[0].toISOString();
        }
        filters.limit = this.alertLogLimit;
        filters.offset = this.alertLogPage * this.alertLogLimit;

        try {
            const data = await API.getAlertHistory(filters);
            this.alertLogTotal = data.total || 0;
            this._renderAlertLog(data.events || []);
            this._updateAlertLogPagination();
            if (this.elements.alertLogTotal) {
                this.elements.alertLogTotal.textContent = this.alertLogTotal;
            }
        } catch (e) {
            console.error('Failed to load alert history:', e);
        }
    },

    /**
     * Render alert events in the log table
     */
    _renderAlertLog(events) {
        const esc = (v) => this.escapeHtml(v);
        const tbody = this.elements.alertLogBody;
        if (!tbody) return;

        if (events.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="alert-log-empty">No events found</td></tr>';
            return;
        }

        let html = '';
        for (const ev of events) {
            const entered = ev.entered_at ? ev.entered_at.replace('T', ' ').substring(0, 16) : '-';
            const uasId = esc(ev.uas_id || '');
            const geozone = esc(ev.geozone_name || '');

            // Compute duration
            let duration = '-';
            if (ev.entered_at) {
                const enteredTime = new Date(ev.entered_at).getTime();
                const exitedTime = ev.exited_at ? new Date(ev.exited_at).getTime() : Date.now();
                const diffMs = exitedTime - enteredTime;
                if (diffMs >= 0) {
                    const mins = Math.floor(diffMs / 60000);
                    if (mins < 60) {
                        duration = `${mins}m`;
                    } else {
                        const hrs = Math.floor(mins / 60);
                        const remainMins = mins % 60;
                        duration = remainMins > 0 ? `${hrs}h ${remainMins}m` : `${hrs}h`;
                    }
                }
            }

            // Status
            let statusClass = 'active';
            let statusLabel = 'Active';
            if (ev.exited_at) {
                if (ev.exited_reason === 'timeout') {
                    statusClass = 'timeout';
                    statusLabel = 'Timed Out';
                } else {
                    statusClass = 'exited';
                    statusLabel = 'Exited';
                }
            }

            html += `<tr>
                <td>${esc(entered)}</td>
                <td>${uasId}</td>
                <td>${geozone}</td>
                <td>${esc(duration)}</td>
                <td><span class="alert-log-status ${statusClass}">${statusLabel}</span></td>
            </tr>`;
        }
        tbody.innerHTML = html;
    },

    /**
     * Update alert log pagination buttons and page info
     */
    _updateAlertLogPagination() {
        const totalPages = Math.ceil(this.alertLogTotal / this.alertLogLimit) || 1;
        const currentPage = this.alertLogPage + 1;
        if (this.elements.alertLogPageInfo) {
            this.elements.alertLogPageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
        }
        if (this.elements.alertLogPrev) {
            this.elements.alertLogPrev.disabled = this.alertLogPage <= 0;
        }
        if (this.elements.alertLogNext) {
            this.elements.alertLogNext.disabled = currentPage >= totalPages;
        }
    },

    /**
     * Export alert log as CSV with current filters
     */
    _exportAlertLog() {
        const params = new URLSearchParams();
        const uasVal = this.elements.alertLogUasFilter.value.trim();
        if (uasVal) params.set('uas_id', uasVal);
        const geoVal = this.elements.alertLogGeozoneFilter.value.trim();
        if (geoVal) params.set('geozone_name', geoVal);
        if (this._alertLogFromFp && this._alertLogFromFp.selectedDates.length > 0) {
            params.set('from', this._alertLogFromFp.selectedDates[0].toISOString());
        }
        if (this._alertLogToFp && this._alertLogToFp.selectedDates.length > 0) {
            params.set('to', this._alertLogToFp.selectedDates[0].toISOString());
        }
        const url = `/api/alerts/export/csv?${params.toString()}`;
        window.open(url, '_blank');
    },

    /**
     * Render stats into the settings panel stat cards
     */
    _renderStats(stats) {
        if (!stats) return;
        const setVal = (id, val) => {
            const el = this.elements[id];
            if (el) el.textContent = val != null ? String(val) : '-';
        };
        setVal('statDrones', stats.total_drones);
        setVal('statSessions', stats.total_sessions);
        setVal('statPositions', stats.total_positions);
        setVal('statActiveAlerts', stats.active_alerts);
        setVal('statTotalAlerts', stats.total_alerts);
    },

    /**
     * Get manufacturer badge HTML for a UAS ID.
     */
    _getManufacturerBadgeHtml(uasId) {
        const mfr = this.getDroneManufacturer(uasId);
        if (!mfr) {
            return '<span class="mfr-badge mfr-unknown" title="Unknown manufacturer"><i class="fas fa-question-circle"></i></span>';
        }
        const abbr = mfr.substring(0, 3).toUpperCase();
        return `<span class="mfr-badge mfr-${mfr.toLowerCase()}" title="${this.escapeHtml(mfr)}">${this.escapeHtml(abbr)}</span>`;
    },

    /**
     * Set time range based on hours back from now
     */
    _setTimeRange(hours) {
        const endTime = new Date();
        const startTime = new Date(endTime.getTime() - hours * 60 * 60 * 1000);

        this.currentStartTime = startTime;
        this.currentEndTime = endTime;

        this._updateActivePresetForHours(hours);

        this._suppressTimeChange = true;
        if (this.elements.startTimeInput._flatpickr) {
            this.elements.startTimeInput._flatpickr.setDate(startTime);
        }
        if (this.elements.endTimeInput._flatpickr) {
            this.elements.endTimeInput._flatpickr.setDate(endTime);
        }
        if (this.elements.startTimeMInput && this.elements.startTimeMInput._flatpickr) {
            this.elements.startTimeMInput._flatpickr.setDate(startTime);
        }
        if (this.elements.endTimeMInput && this.elements.endTimeMInput._flatpickr) {
            this.elements.endTimeMInput._flatpickr.setDate(endTime);
        }
        this._suppressTimeChange = false;
    },

    /**
     * Load remote sources status from server
     */
    async _loadRemoteStatus() {
        try {
            const data = await API.getSources();
            this.remotes = data.sources || [];
            this._updateRemoteSummary();
            this._renderRemoteDetail();
        } catch (e) {
            console.error('Failed to load remote status:', e);
        }
    },

    /**
     * Update remote sources summary bar
     */
    _updateRemoteSummary() {
        const el = this.elements.remoteSummary;
        if (!el) return;
        const total = this.remotes.length;
        if (total === 0) {
            el.innerHTML = 'No remotes configured';
            return;
        }
        const now = Date.now();
        const twentyMin = 20 * 60 * 1000;
        const staleMs = (this.positionStaleMinutes || 30) * 60 * 1000;
        let connected = 0;
        let latestDataTs = 0;
        for (const r of this.remotes) {
            if (r.last_sync && r.last_sync !== 'Never') {
                const ts = new Date(r.last_sync).getTime();
                const threshold = r.type === 'collector' ? staleMs : twentyMin;
                if (now - ts < threshold) connected++;
            }
            if (r.last_data && r.last_data !== 'Never') {
                const ts = new Date(r.last_data).getTime();
                if (ts > latestDataTs) latestDataTs = ts;
            }
        }
        const statusClass = connected === total
            ? 'remote-status-ok'
            : connected > 0 ? 'remote-status-partial' : 'remote-status-none';
        const latestStr = latestDataTs > 0
            ? new Date(latestDataTs).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
            : 'never';
        el.innerHTML = `Connected: <span class="${statusClass}">${connected}/${total}</span> &middot; Last Data: ${latestStr}`;
    },

    /**
     * Render remote sources detail panel
     */
    _renderRemoteDetail() {
        const body = this.elements.remoteDetailBody;
        const header = this.elements.remoteDetail?.querySelector('.remote-detail-header');
        if (!body) return;
        if (this.remotes.length === 0) {
            if (header) header.textContent = 'Remote Sources';
            body.innerHTML = '<div class="remote-empty">No remote sources configured</div>';
            return;
        }
        if (header) header.textContent = 'Remote Sources';
        const now = Date.now();
        const twentyMin = 20 * 60 * 1000;
        const staleMs = (this.positionStaleMinutes || 30) * 60 * 1000;
        const esc = (v) => this.escapeHtml(v);

        const fmt = (v) => {
            if (!v || v === 'Never') return 'Never';
            const d = new Date(v);
            return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
        };

        let html = '';
        for (const r of this.remotes) {
            const syncTs = r.last_sync && r.last_sync !== 'Never' ? new Date(r.last_sync) : null;
            const threshold = r.type === 'collector' ? staleMs : twentyMin;
            const isActive = syncTs && (now - syncTs.getTime() < threshold);
            const iconHtml = isActive
                ? '<i class="fas fa-check-circle remote-icon active" title="Active"></i>'
                : '<i class="fas fa-clock remote-icon stale" title="No recent activity"></i>';
            const badgeIcon = r.type === 'collector'
                ? '<i class="fas fa-satellite-dish"></i> Collector'
                : '<i class="fas fa-cloud-upload-alt"></i> API';
            html += `<div class="remote-row">
                <div class="remote-row-left">
                    ${iconHtml}
                    <span class="remote-name">${esc(r.name)}</span>
                    <span class="remote-type-badge">${badgeIcon}</span>
                </div>
                <div class="remote-row-right">
                    <div class="remote-time"><span class="remote-time-label">Data:</span> ${esc(fmt(r.last_data))}</div>
                    <div class="remote-time"><span class="remote-time-label">Last Seen:</span> ${esc(fmt(r.last_sync))}</div>
                </div>
            </div>`;
        }
        body.innerHTML = html;
    },

    /**
     * Activate the preset matching the given hours, clear others
     */
    _updateActivePresetForHours(hours) {
        this.elements.timePresets.forEach(btn => {
            btn.classList.toggle('active', parseInt(btn.dataset.hours) === hours);
        });
        if (this.elements.settingsTimePresets) {
            this.elements.settingsTimePresets.forEach(btn => {
                btn.classList.toggle('active', parseInt(btn.dataset.hours) === hours);
            });
        }
    },

    /**
     * Clear active preset
     */
    _clearActivePreset() {
        this.elements.timePresets.forEach(btn => btn.classList.remove('active'));
        if (this.elements.settingsTimePresets) {
            this.elements.settingsTimePresets.forEach(btn => btn.classList.remove('active'));
        }
    },

    /**
     * Refresh all data - uses incremental updates to preserve the detail panel
     * and existing DOM state.
     */
    async refreshData(showSpinner = true) {
        if (this.isLoading) {
            console.debug('[Refresh] Skipped — isLoading is true');
            return;
        }

        this.isLoading = true;
        if (showSpinner) {
            this.elements.refreshBtn.classList.add('spinning');
        }

        try {
            // Consolidated refresh: returns drones, alerts, stats, and sources
            const data = await API.getRefresh(
                this.currentStartTime, this.currentEndTime, this.droneTimestamps
            );

            // Update remote status
            this.remotes = data.sources || [];
            this._updateRemoteSummary();
            if (this.remoteDetailOpen) {
                this._renderRemoteDetail();
            }
            const newDrones = data.drones || [];
            if (newDrones.length > 0 && this._initialized) {
                const existingUasIds = new Set(Object.keys(this.droneMap).map(k => k.split(':')[0]));
                const hasNewUas = newDrones.some(d => !existingUasIds.has(d.uas_id));
                if (hasNewUas) {
                    this.lastActivityTime = Date.now();
                }
            }
            this.alertEvents = data.alerts ? data.alerts.active || [] : [];
            this._renderStats(data.stats || {});

            // Update map alert state
            MapController.updateAlertState(this.alertEvents);

            // Update alert filter count
            if (this.elements.alertFilterCount) {
                this.elements.alertFilterCount.textContent = this.alertEvents.length;
                this.elements.alertFilterCount.style.display = this.alertEvents.length > 0 ? '' : 'none';
            }

            // Merge new drones with existing droneMap
            this._mergeDrones(newDrones);

            // Update drone timestamps for all merged drones so next poll is accurate
            this._updateDroneTimestamps(newDrones);

            // Get all current drones from the merged map
            let drones = Object.values(this.droneMap);

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
            this.dismissedSessionKeys = new Set([...this.dismissedSessionKeys].filter(k => currentSessionKeys.has(k)));

            // Update drone list incrementally
            this._updateDroneList(drones);

            // Update markers on the map for changed drones only
            if (drones.length > 0) {
                MapController.updateDrones(drones);
                const allUasIds = new Set(drones.map(d => d.uas_id));
                MapController.filterOperatorsByUasIds(allUasIds);
            } else {
                MapController.dronePositions = {};
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

            // Update mobile collector positions
            await MapController._updateCollectors();

            // Update last update time
            this._updateLastUpdateTime();

        } catch (e) {
            console.error('Failed to refresh data:', e);
            this._showError('Failed to load data. Please try again.');
        } finally {
            this.isLoading = false;
            this.elements.refreshBtn.classList.remove('spinning');
            this._adjustPollTimer();
        }
    },

    /**
     * Force-refresh: reset loading state and trigger a refresh.
     * Useful for recovering from a stuck state (e.g. after device sleep).
     */
    forceRefresh() {
        if (this.isLoading) {
            console.warn('[Refresh] forceRefresh — was stuck, resetting isLoading');
            this.isLoading = false;
            this.elements.refreshBtn.classList.remove('spinning');
        }
        this.refreshData(true);
    },

    _mergeDrones(newDrones) {
        for (const d of newDrones) {
            const key = `${d.uas_id}:${d.computed_session_id || 'unknown'}`;
            this.droneMap[key] = d;
        }
    },

    _updateDroneTimestamps(drones) {
        for (const d of drones) {
            const key = `${d.uas_id}:${d.computed_session_id || 'unknown'}`;
            this.droneTimestamps[key] = d.timestamp;
        }
    },

    /**
     * Update the drone list in sidebar - uses caching to skip DOM updates
     * when data hasn't changed, preserving existing state (expanded groups, detail panel, etc.)
     */
    _updateDroneList(drones) {
        const list = this.elements.droneList;

        // Build a cache key from the drone data and current view mode to detect changes
        const cacheKey = JSON.stringify({
            mode: this.viewMode,
            drones: drones.map(d =>
                `${d.uas_id}:${d.computed_session_id || 'unknown'}:${d.timestamp}:${d.latitude}:${d.longitude}:${d.altitude}`
            )
        });
        if (cacheKey === this._droneListCacheKey) {
            return; // No changes, skip DOM update
        }
        this._droneListCacheKey = cacheKey;

        if (drones.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-satellite-dish"></i>
                    <p>No flights detected in time window</p>
                </div>
            `;
            return;
        }

        if (this.viewMode === 'date') {
            this._renderDateView(drones);
        } else {
            this._renderUASView(drones);
        }
    },

    /**
     * Render a single drone item HTML (shared by date and UAS views)
     */
    _renderDroneItem(drone, alertedUasIds) {
        const esc = (v) => this.escapeHtml(v);
        const color = MapController.getDroneColor(drone.uas_id);
        const altitude = drone.altitude !== null && drone.altitude !== undefined
            ? Units.formatAltitude(drone.altitude, true, 0)
            : 'N/A';
        const height = drone.height !== null && drone.height !== undefined
            ? Units.formatAltitude(drone.height, true, 0)
            : null;
        const time = new Date(drone.timestamp);
        const timeStr = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

        const rawSessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
        const sessionId = drone.computed_session_id ? drone.computed_session_id.replace('session_', '') : '';
        const isSelected = this.selectedDrones.has(rawSessionKey);
        const isVisible = this.visibleSessions.has(rawSessionKey);

        const hasAlert = alertedUasIds.has(drone.uas_id);

        let durationStr = 'N/A';
        if (drone.session_start) {
            const startTime = new Date(drone.session_start);
            const endTime = new Date(drone.timestamp);
            const durationSeconds = (endTime - startTime) / 1000;
            durationStr = this.formatDuration(durationSeconds);
        }

        return `
            <div class="drone-item ${isSelected ? 'active' : ''} ${isVisible ? '' : 'dimmed'} ${hasAlert ? 'has-geozone-alert' : ''}" data-uas-id="${esc(drone.uas_id)}" data-session-key="${esc(rawSessionKey)}" data-session-id="${esc(drone.computed_session_id || '')}">
                <input type="checkbox" class="drone-checkbox" data-session-key="${esc(rawSessionKey)}" ${isVisible ? 'checked' : ''}>
                <div class="drone-color" style="background-color: ${color};"></div>
                <div class="drone-info">
                    <div class="drone-id">${hasAlert ? '<i class="fas fa-exclamation-triangle alert-icon"></i> ' : ''}${esc(this.getDroneName(drone.uas_id))}</div>
                    <div class="drone-meta-row">
                        ${this._getManufacturerBadgeHtml(drone.uas_id)}
                        <div class="session-id">${esc(sessionId)}</div>
                        <div class="drone-meta">Alt: ${altitude}${height ? ` Ht: ${height}` : ''} | ${timeStr} | ${durationStr}</div>
                    </div>
                </div>
                <div class="drone-actions">
                    <button class="focus-btn" title="Focus on map">
                        <i class="fas fa-crosshairs"></i>
                    </button>
                    ${this.hasPermission('export_data') ? `<button class="export-btn" title="Export session" data-uas-id="${esc(drone.uas_id)}" data-session-id="${esc(drone.computed_session_id || '')}" data-session-key="${esc(rawSessionKey)}">
                        <i class="fas fa-download"></i>
                    </button>` : ''}
                </div>
            </div>
        `;
    },

    /**
     * Render the date-grouped view
     */
    _renderDateView(drones) {
        const list = this.elements.droneList;
        const esc = (v) => this.escapeHtml(v);

        // Build set of UAS IDs with active geozone alerts
        const alertedUasIds = new Set((this.alertEvents || []).map(e => e.uas_id));

        // Apply geozone alert filter if active
        if (this.showGeozoneAlerts) {
            drones = drones.filter(d => alertedUasIds.has(d.uas_id));
        }

        // Group flights by date
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
        const mostRecentDate = sortedDates.length > 0 ? sortedDates[0] : null;
        const isInitialLoad = this.visibleSessions.size === 0;

        // Auto-check new sessions on the most recent date
        if (mostRecentDate) {
            for (const drone of groups[mostRecentDate]) {
                const sessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
                if (!this.visibleSessions.has(sessionKey) && !this.loadedTracks.has(sessionKey) && !this.dismissedSessionKeys.has(sessionKey)) {
                    this.visibleSessions.add(sessionKey);
                }
            }
        }

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
                this.expandedGroups.add(date);
            }
            const isExpanded = this.expandedGroups.has(date);

            html += `
                <div class="date-group" data-group-key="${esc(date)}">
                    <div class="date-header">
                        <input type="checkbox" class="date-checkbox" data-group-key="${esc(date)}" ${allVisible ? 'checked' : ''} ${isIndeterminate ? 'data-indeterminate="true"' : ''}>
                        <span class="date-label">${esc(date)}</span>
                        <span class="date-count">${flightCount} flight${flightCount !== 1 ? 's' : ''}</span>
                        <i class="fas fa-chevron-${isExpanded ? 'down' : 'right'} date-chevron"></i>
                    </div>
                    <div class="date-items ${isExpanded ? '' : 'collapsed'}">
                        ${sortedSessions.map(drone => this._renderDroneItem(drone, alertedUasIds)).join('')}
                    </div>
                </div>
            `;
        });

        list.innerHTML = html;

        // Set indeterminate state
        list.querySelectorAll('.date-checkbox[data-indeterminate="true"]').forEach(cb => {
            cb.indeterminate = true;
        });

        // Load tracks for visible sessions on the most recent date via batch
        if (mostRecentDate) {
            this._batchLoadTracks(groups[mostRecentDate]);
        }

        this._updateReplayButtonState();
        this._attachListEventHandlers();
    },

    /**
     * Render the UAS-ID-grouped view
     */
    _renderUASView(drones) {
        const list = this.elements.droneList;
        const esc = (v) => this.escapeHtml(v);

        // Build set of UAS IDs with active geozone alerts
        const alertedUasIds = new Set((this.alertEvents || []).map(e => e.uas_id));

        // Apply geozone alert filter if active
        if (this.showGeozoneAlerts) {
            drones = drones.filter(d => alertedUasIds.has(d.uas_id));
        }

        // Group flights by UAS ID
        const groups = {};
        drones.forEach(drone => {
            if (!groups[drone.uas_id]) {
                groups[drone.uas_id] = [];
            }
            groups[drone.uas_id].push(drone);
        });

        // Sort groups by most recent timestamp (newest first)
        const sortedUasIds = Object.keys(groups).sort((a, b) => {
            const maxA = Math.max(...groups[a].map(d => new Date(d.timestamp).getTime()));
            const maxB = Math.max(...groups[b].map(d => new Date(d.timestamp).getTime()));
            return maxB - maxA;
        });

        const mostActiveUasId = sortedUasIds.length > 0 ? sortedUasIds[0] : null;
        const isInitialLoad = this.visibleSessions.size === 0;

        // Auto-check sessions from the most active UAS group
        if (mostActiveUasId && isInitialLoad) {
            for (const drone of groups[mostActiveUasId]) {
                const sessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
                if (!this.visibleSessions.has(sessionKey) && !this.loadedTracks.has(sessionKey) && !this.dismissedSessionKeys.has(sessionKey)) {
                    this.visibleSessions.add(sessionKey);
                }
            }
        }

        let html = '';
        sortedUasIds.forEach(uasId => {
            // Combine time-window sessions with extra loaded sessions, deduplicate
            const allSeen = new Set();
            const combined = [];

            const timelineSessions = groups[uasId] || [];
            const extraSessions = this.uasExtraSessions[uasId] || [];

            timelineSessions.concat(extraSessions).forEach(d => {
                const key = `${d.uas_id}:${d.computed_session_id || 'unknown'}`;
                if (!allSeen.has(key)) {
                    allSeen.add(key);
                    combined.push(d);
                }
            });

            combined.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

            const flightCount = combined.length;
            const totalSessions = this.uasExtraTotal[uasId];
            const loadedExtra = this.uasExtraSessions[uasId] ? this.uasExtraSessions[uasId].length : 0;
            const timeWindowCount = timelineSessions.length;
            const hasMore = totalSessions === undefined || (loadedExtra + timeWindowCount < totalSessions);
            const isLoading = this.uasExtraLoading[uasId];

            const uasSessionKeys = combined.map(d => `${d.uas_id}:${d.computed_session_id || 'unknown'}`);
            const allVisible = uasSessionKeys.every(key => this.visibleSessions.has(key));
            const someVisible = uasSessionKeys.some(key => this.visibleSessions.has(key));
            const isIndeterminate = someVisible && !allVisible;

            if (isInitialLoad) {
                this.expandedGroups.add(uasId);
            }
            const isExpanded = this.expandedGroups.has(uasId);

            const displayName = this.getDroneName(uasId);
            const hasAlias = displayName !== uasId;

            html += `
                <div class="date-group" data-group-key="${esc(uasId)}">
                    <div class="date-header">
                        <input type="checkbox" class="date-checkbox" data-group-key="${esc(uasId)}" ${allVisible ? 'checked' : ''} ${isIndeterminate ? 'data-indeterminate="true"' : ''}>
                        <span class="date-label">${esc(displayName)}</span>
                        ${hasAlias ? `<span class="uas-id-sub">${esc(uasId)}</span>` : ''}
                        <span class="date-count">${flightCount} flight${flightCount !== 1 ? 's' : ''}</span>
                        <i class="fas fa-chevron-${isExpanded ? 'down' : 'right'} date-chevron"></i>
                    </div>
                    <div class="date-items ${isExpanded ? '' : 'collapsed'}">
                        ${combined.map(drone => this._renderDroneItem(drone, alertedUasIds)).join('')}
                        ${hasMore
                            ? `<button class="load-more-btn" data-load-more-uas="${esc(uasId)}" ${isLoading ? 'disabled' : ''}>${isLoading ? 'Loading...' : 'Load More'}</button>`
                            : totalSessions !== undefined
                                ? '<div class="load-more-end">All results loaded</div>'
                                : ''}
                    </div>
                </div>
            `;
        });

        list.innerHTML = html;

        // Set indeterminate state
        list.querySelectorAll('.date-checkbox[data-indeterminate="true"]').forEach(cb => {
            cb.indeterminate = true;
        });

        // Load tracks for visible sessions on the most active UAS group via batch
        if (mostActiveUasId && isInitialLoad) {
            this._batchLoadTracks(groups[mostActiveUasId]);
        }

        // Load More button handlers
        list.querySelectorAll('.load-more-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const uasId = e.currentTarget.dataset.loadMoreUas;
                this._loadMoreUas(uasId);
            });
        });

        this._updateReplayButtonState();
        this._attachListEventHandlers();
    },

    /**
     * Load more sessions for a UAS ID (ignores time constraints).
     * Fetches 10 at a time, appends to the extra sessions cache, re-renders.
     */
    async _loadMoreUas(uasId) {
        if (this.uasExtraLoading[uasId]) return;
        this.uasExtraLoading[uasId] = true;

        const currentExtra = this.uasExtraSessions[uasId] || [];
        const offset = currentExtra.length;

        try {
            const data = await API.getUASSessions(uasId, offset, 10);
            const newSessions = data.sessions || [];

            this.uasExtraSessions[uasId] = currentExtra.concat(newSessions);
            this.uasExtraTotal[uasId] = data.total;
        } catch (e) {
            console.error('Failed to load more sessions for', uasId, e);
        } finally {
            this.uasExtraLoading[uasId] = false;
            this._updateReplayButtonState();
        }

        // Re-render the UAS view
        const allDrones = Object.values(this.droneMap);
        const filtered = allDrones.filter(d => {
            const isKnown = !!this.droneAliases[d.uas_id];
            if (isKnown && !this.showKnownDrones) return false;
            if (!isKnown && !this.showUnknownDrones) return false;
            return true;
        });
        this._droneListCacheKey = null;
        this._renderUASView(filtered);
    },

    /**
     * Batch-load tracks for a list of drones
     */
    _batchLoadTracks(drones) {
        const pending = [];
        for (const drone of drones) {
            const sessionKey = `${drone.uas_id}:${drone.computed_session_id || 'unknown'}`;
            const sessionId = drone.computed_session_id;
            if (sessionId && this.visibleSessions.has(sessionKey) && !this.loadedTracks.has(sessionKey)) {
                this.loadedTracks.add(sessionKey);
                pending.push({ uas_id: drone.uas_id, session_id: sessionId });
            }
        }
        if (pending.length > 0) {
            MapController.loadTracksBatch(pending).then(loaded => {
                const loadedSet = new Set(loaded);
                pending.forEach(s => {
                    const key = `${s.uas_id}:${s.session_id}`;
                    if (!loadedSet.has(key)) this.loadedTracks.delete(key);
                });
                this._updateReplayButtonState();
            });
        }
    },

    /**
     * Attach event handlers for the drone list (shared by date and UAS views)
     */
    _attachListEventHandlers() {
        const list = this.elements.droneList;

        // Add group checkbox handlers
        list.querySelectorAll('.date-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                const group = e.target.closest('.date-group');
                const droneCheckboxes = group.querySelectorAll('.drone-checkbox');
                const isChecked = e.target.checked;

                droneCheckboxes.forEach(dc => {
                    dc.checked = isChecked;
                    const sessionKey = dc.dataset.sessionKey;
                    const droneItem = dc.closest('.drone-item');

                    if (isChecked) {
                        this.visibleSessions.add(sessionKey);
                        droneItem.classList.remove('dimmed');
                        if (!this.loadedTracks.has(sessionKey)) {
                            const uasId = droneItem.dataset.uasId;
                            const sessionId = droneItem.dataset.sessionId;
                            if (sessionId) {
                                this.loadedTracks.add(sessionKey);
                                MapController.loadTrackSession(uasId, sessionId, this.currentStartTime, this.currentEndTime)
                                    .then(success => { if (!success) this.loadedTracks.delete(sessionKey); this._updateReplayButtonState(); });
                            }
                        }
                    } else {
                        this.visibleSessions.delete(sessionKey);
                        droneItem.classList.add('dimmed');
                        const uasId = droneItem.dataset.uasId;
                        MapController.removeTrack(uasId, sessionKey);
                        this.loadedTracks.delete(sessionKey);
                        this.dismissedSessionKeys.add(sessionKey);
                        if (this.selectedSession === droneItem.dataset.sessionId) {
                            this._closeDetailPanel();
                        }
                    }
                });

                e.target.indeterminate = false;
                this._updateReplayButtonState();
            });
        });

        // Add group header click handlers (toggle expand/collapse)
        list.querySelectorAll('.date-header').forEach(header => {
            header.addEventListener('click', async (e) => {
                if (e.target.classList.contains('date-checkbox')) {
                    return;
                }

                const group = header.closest('.date-group');
                const items = group.querySelector('.date-items');
                const chevron = header.querySelector('.date-chevron');
                const groupKey = group.dataset.groupKey;

                if (items.classList.contains('collapsed')) {
                    items.classList.remove('collapsed');
                    chevron.classList.remove('fa-chevron-right');
                    chevron.classList.add('fa-chevron-down');
                    this.expandedGroups.add(groupKey);
                } else {
                    items.classList.add('collapsed');
                    chevron.classList.remove('fa-chevron-down');
                    chevron.classList.add('fa-chevron-right');
                    this.expandedGroups.delete(groupKey);
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
                const isChecked = e.target.checked;

                if (isChecked) {
                    this.visibleSessions.add(sessionKey);
                    droneItem.classList.remove('dimmed');
                    if (!this.loadedTracks.has(sessionKey)) {
                        if (sessionId) {
                            this.loadedTracks.add(sessionKey);
                            MapController.loadTrackSession(uasId, sessionId, this.currentStartTime, this.currentEndTime)
                                .then(success => { if (!success) this.loadedTracks.delete(sessionKey); this._updateReplayButtonState(); });
                        }
                    }
                } else {
                    this.visibleSessions.delete(sessionKey);
                    droneItem.classList.add('dimmed');
                    MapController.removeTrack(uasId, sessionKey);
                    this.loadedTracks.delete(sessionKey);
                    this.dismissedSessionKeys.add(sessionKey);
                    if (this.selectedSession === sessionId) {
                        this._closeDetailPanel();
                    }
                }

                this._updateDateCheckboxState(group);
                this._updateReplayButtonState();
            });
        });

        // Add click handlers for drone items (open detail panel only if visible)
        list.querySelectorAll('.drone-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.classList.contains('drone-checkbox') ||
                    e.target.closest('.focus-btn')) {
                    return;
                }

                const uasId = item.dataset.uasId;
                const sessionKey = item.dataset.sessionKey;
                const sessionId = item.dataset.sessionId;

                if (!this.visibleSessions.has(sessionKey)) {
                    return;
                }

                if (this.selectedDrones.has(sessionKey)) {
                    this.selectedDrones.delete(sessionKey);
                    item.classList.remove('active');
                } else {
                    this.selectedDrones.clear();
                    this.selectedDrones.add(sessionKey);
                    list.querySelectorAll('.drone-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                }

                MapController.highlightDrone(uasId);

                const isSameSession = this.selectedDrone === uasId && this.selectedSession === sessionId;
                if (isSameSession) {
                    this._closeDetailPanel();
                } else {
                    this._openDetailPanel(uasId, sessionId);
                }

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

                if (!this.visibleSessions.has(sessionKey)) {
                    return;
                }

                MapController.panToDrone(uasId);

                if (sessionId && !this.loadedTracks.has(sessionKey)) {
                    this.loadedTracks.add(sessionKey);
                    MapController.loadTrackSession(uasId, sessionId, this.currentStartTime, this.currentEndTime)
                        .then(success => { if (!success) this.loadedTracks.delete(sessionKey); this._updateReplayButtonState(); });
                }

                if (window.innerWidth < 768) {
                    this.elements.sidebar.classList.remove('open');
                }
            });
        });

        // Export button click — open floating menu
        list.querySelectorAll('.export-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._openExportMenu(btn);
            });
        });

        // Close export menu on outside click
        document.addEventListener('click', (e) => {
            if (this._exportMenu && !this._exportMenu.contains(e.target) && !e.target.closest('.export-btn')) {
                this._closeExportMenu();
            }
        });
    },

    /**
     * Switch the sidebar list view mode
     */
    _switchView(mode) {
        if (mode === this.viewMode) return;
        this.viewMode = mode;

        // Update tab button active state
        document.querySelectorAll('.view-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.view === mode);
        });

        // Clear cache so list re-renders
        this._droneListCacheKey = null;

        // Re-render with current drone data
        const drones = Object.values(this.droneMap);
        const filtered = drones.filter(d => {
            const isKnown = !!this.droneAliases[d.uas_id];
            if (isKnown && !this.showKnownDrones) return false;
            if (!isKnown && !this.showUnknownDrones) return false;
            return true;
        });
        this._updateDroneList(filtered);
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
        this.elements.lastUpdateSpan.textContent = `Last updated: ${now.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}`;
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
            const dist = Units.haversineDistance(
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
     * Show error message
     */
    _showToast(message, duration = 3000) {
        const toast = document.createElement('div');
        toast.className = 'toast-notification';
        toast.textContent = message;
        document.body.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    _showError(message) {
        console.error(message);
    },

    /**
     * Open floating export format menu positioned near the given button
     */
    _openExportMenu(btn) {
        // Close any existing menu
        this._closeExportMenu();

        const uasId = btn.dataset.uasId;
        const sessionId = btn.dataset.sessionId;
        const rect = btn.getBoundingClientRect();

        const menu = document.createElement('div');
        menu.className = 'export-menu';
        menu.innerHTML = `
            <a class="export-option" data-format="csv">CSV</a>
            <a class="export-option" data-format="gpx">GPX</a>
            <a class="export-option" data-format="kml">KML</a>
        `;

        // Position menu — prefer below, flip above if not enough room
        const menuHeight = 120; // approx max height
        const spaceBelow = window.innerHeight - rect.bottom - 4;
        const spaceAbove = rect.top - 4;

        if (spaceBelow >= menuHeight || spaceBelow >= spaceAbove) {
            menu.style.top = (rect.bottom + 4) + 'px';
        } else {
            menu.style.top = (rect.top - menuHeight - 4) + 'px';
        }

        menu.style.left = Math.min(rect.left, window.innerWidth - 120) + 'px';

        document.body.appendChild(menu);
        this._exportMenu = menu;

        // Handle option clicks
        menu.querySelectorAll('.export-option').forEach(opt => {
            opt.addEventListener('click', (e) => {
                e.stopPropagation();
                const format = opt.dataset.format;
                this._closeExportMenu();

                // Build download URL
                const params = new URLSearchParams();
                if (this.currentStartTime) params.append('start', this.currentStartTime.toISOString());
                if (this.currentEndTime) params.append('end', this.currentEndTime.toISOString());
                if (sessionId) params.append('session_id', sessionId);
                const url = `${API.baseUrl}/api/export/${encodeURIComponent(format)}/${encodeURIComponent(uasId)}?${params}`;

                const a = document.createElement('a');
                a.href = url;
                a.download = '';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            });
        });
    },

    /**
     * Close the floating export menu
     */
    _closeExportMenu() {
        if (this._exportMenu) {
            this._exportMenu.remove();
            this._exportMenu = null;
        }
    },

    // ==============================
    // Replay Controls
    // ==============================

    _startReplay() {
        const checkedKeys = [];
        this.visibleSessions.forEach(key => {
            if (MapController.sessionPositions[key] && MapController.sessionPositions[key].length >= 2) {
                checkedKeys.push(key);
            }
        });
        if (checkedKeys.length === 0) return;
        MapController.startReplay(checkedKeys);
    },

    _onReplayStart() {
        this.replayActive = true;
        this.replayPlaying = true;
        this.elements.replayControls.style.display = 'flex';
        this.elements.replayPlayBtn.querySelector('i').className = 'fas fa-pause';
        this.elements.replayPlayPauseBtn.querySelector('i').className = 'fas fa-pause';
    },

    _onReplayStop() {
        this.replayActive = false;
        this.replayPlaying = false;
        this.elements.replayControls.style.display = 'none';
        const icon = this.elements.replayPlayBtn.querySelector('i');
        if (icon) icon.className = 'fas fa-play';
        // Clear replay-active indicators
        this.elements.droneList.querySelectorAll('.drone-item.replay-active').forEach(el => {
            el.classList.remove('replay-active');
        });
        this._updateReplayButtonState();
    },

    _onReplayActiveSessions(activeKeys) {
        const list = this.elements.droneList;
        list.querySelectorAll('.drone-item.replay-active').forEach(el => {
            el.classList.remove('replay-active');
        });
        const activeSet = new Set(activeKeys);
        list.querySelectorAll('.drone-item').forEach(el => {
            const key = el.dataset.sessionKey;
            if (key && activeSet.has(key)) {
                el.classList.add('replay-active');
            }
        });
    },

    _onReplayPause() {
        this.replayPlaying = false;
        this.elements.replayPlayPauseBtn.querySelector('i').className = 'fas fa-play';
    },

    _onReplayResume() {
        this.replayPlaying = true;
        this.elements.replayPlayPauseBtn.querySelector('i').className = 'fas fa-pause';
    },

    _onReplayTime(realTimeMs, displayTimeMs, totalDurationMs) {
        // Update time display
        const d = new Date(realTimeMs);
        const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        this.elements.replayTimeDisplay.textContent = timeStr;

        // Update timeline slider
        if (totalDurationMs > 0) {
            this.elements.replayTimeline.max = totalDurationMs;
            this.elements.replayTimeline.value = displayTimeMs;
        }
    },

    _onReplayEnd() {
        // Replay naturally ended
        this._onReplayStop();
    },

    /**
     * Update the play button enabled state based on checked sessions
     */
    _updateReplayButtonState() {
        let hasData = false;
        this.visibleSessions.forEach(key => {
            if (MapController.sessionPositions[key] && MapController.sessionPositions[key].length >= 2) {
                hasData = true;
            }
        });
        this.elements.replayPlayBtn.disabled = !hasData || this.replayActive;
    },
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    UIController.init();
});
