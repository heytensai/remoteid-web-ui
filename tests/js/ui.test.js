/**
 * Tests for static/js/ui.js
 * Tests pure utility functions (escapeHtml, _niceStep).
 */
const fs = require('fs');
const path = require('path');

let uiCode = fs.readFileSync(
  path.resolve(__dirname, '../../static/js/ui.js'),
  'utf8'
);

// Mock MapController and Units before eval
global.L = {
  map: jest.fn().mockReturnValue({
    setView: jest.fn(),
    on: jest.fn(),
    invalidateSize: jest.fn(),
    addLayer: jest.fn(),
    removeLayer: jest.fn(),
    fitBounds: jest.fn(),
  }),
  tileLayer: jest.fn().mockReturnValue({ addTo: jest.fn() }),
  marker: jest.fn().mockReturnValue({
    addTo: jest.fn(),
    bindPopup: jest.fn(),
    getLatLng: jest.fn().mockReturnValue({ lat: 0, lng: 0 }),
    setZIndexOffset: jest.fn(),
    openPopup: jest.fn(),
  }),
  polyline: jest.fn().mockReturnValue({
    addTo: jest.fn(),
    setStyle: jest.fn(),
  }),
  divIcon: jest.fn().mockReturnValue({}),
  layerGroup: jest.fn().mockReturnValue({
    addTo: jest.fn(),
    clearLayers: jest.fn(),
    removeLayer: jest.fn(),
  }),
};

global.MapController = {
  ready: false,
  config: {},
  markers: {},
  tracks: {},
  operatorMarkers: {},
  layers: {
    drones: { clearLayers: jest.fn(), addTo: jest.fn() },
    tracks: { clearLayers: jest.fn(), addTo: jest.fn() },
    operators: { clearLayers: jest.fn(), addTo: jest.fn() },
  },
  clearAllTracks: jest.fn(),
  clearAllOperators: jest.fn(),
  getDroneColor: jest.fn().mockReturnValue('hsl(120, 70%, 50%)'),
  updateDrones: jest.fn(),
  toggleOperators: jest.fn(),
  toggleTracks: jest.fn(),
  setTrackOpacity: jest.fn(),
  fitBounds: jest.fn(),
  panToDrone: jest.fn(),
  highlightDrone: jest.fn(),
  filterOperatorsByUasIds: jest.fn(),
  removeTrack: jest.fn(),
  loadTrackSession: jest.fn(),
};

global.Units = {
  formatDistance: jest.fn().mockReturnValue('100 m'),
  formatAltitude: jest.fn().mockReturnValue('100m'),
  formatSpeed: jest.fn().mockReturnValue('50 km/h'),
  useMetric: true,
  getAltitudeUnit: jest.fn().mockReturnValue('m'),
  haversineDistance: jest.fn().mockReturnValue(0),
};

// Mock flatpickr
global.flatpickr = jest.fn().mockReturnValue({
  setDate: jest.fn(),
  destroy: jest.fn(),
});

// Remove the auto-init, strip const so eval assigns globally
uiCode = uiCode
  .replace(/\/\/ Initialize when DOM is ready\n.*$/, '')
  .replace(/^const /m, '');
(0, eval)(uiCode);

describe('UIController', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    UIController.selectedDrones.clear();
    UIController.selectedSession = null;
    UIController.visibleSessions.clear();
    UIController.loadedTracks.clear();
    UIController.elements = {};
  });

  describe('escapeHtml', () => {
    test('escapes HTML', () => {
      expect(UIController.escapeHtml('<b>bold</b>')).toBe(
        '&lt;b&gt;bold&lt;/b&gt;'
      );
    });

    test('returns empty for null', () => {
      expect(UIController.escapeHtml(null)).toBe('');
    });

    test('returns empty for undefined', () => {
      expect(UIController.escapeHtml(undefined)).toBe('');
    });
  });

  describe('_niceStep', () => {
    test('computes nice step values', () => {
      expect(UIController._niceStep(100, 4)).toBe(20);
      expect(UIController._niceStep(50, 4)).toBe(10);
      expect(UIController._niceStep(10, 4)).toBe(2);
      expect(UIController._niceStep(1000, 4)).toBe(200);
    });

    test('handles zero range', () => {
      expect(UIController._niceStep(0, 4)).toBe(0);
    });

    test('handles very small ranges', () => {
      expect(UIController._niceStep(0.5, 4)).toBe(0.1);
    });
  });

  describe('_haversineDistance', () => {
    test('same point returns 0', () => {
      Units.haversineDistance(37, -122, 37, -122);
      expect(Units.haversineDistance).toHaveBeenCalledWith(37, -122, 37, -122);
    });
  });

  describe('_updateDateCheckboxState', () => {
    function createGroup(checkedCount, totalCount) {
      const group = document.createElement('div');
      const dateCb = document.createElement('input');
      dateCb.type = 'checkbox';
      dateCb.className = 'date-checkbox';
      group.appendChild(dateCb);

      for (let i = 0; i < totalCount; i++) {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'drone-checkbox';
        cb.checked = i < checkedCount;
        group.appendChild(cb);
      }

      return group;
    }

    test('unchecks when none selected', () => {
      const group = createGroup(0, 3);
      UIController._updateDateCheckboxState(group);
      const dateCb = group.querySelector('.date-checkbox');
      expect(dateCb.checked).toBe(false);
      expect(dateCb.indeterminate).toBe(false);
    });

    test('checks when all selected', () => {
      const group = createGroup(3, 3);
      UIController._updateDateCheckboxState(group);
      const dateCb = group.querySelector('.date-checkbox');
      expect(dateCb.checked).toBe(true);
      expect(dateCb.indeterminate).toBe(false);
    });

    test('indeterminate when some selected', () => {
      const group = createGroup(1, 3);
      UIController._updateDateCheckboxState(group);
      const dateCb = group.querySelector('.date-checkbox');
      expect(dateCb.checked).toBe(false);
      expect(dateCb.indeterminate).toBe(true);
    });
  });

  describe('_switchToLive / _switchToArchive', () => {
    beforeEach(() => {
      UIController._dataMode = 'live';
      UIController.droneTimestamps = {};
      UIController.elements = {
        liveBtn: document.createElement('button'),
        liveBtnM: document.createElement('button'),
        headerTimeControls: document.createElement('div'),
        settingsTimeControls: document.createElement('div'),
        droneList: document.createElement('div'),
      };
      UIController.elements.headerTimeControls.classList.add('disabled');
      UIController.elements.settingsTimeControls.classList.add('disabled');
      // Mock refreshData to avoid network calls
      UIController.refreshData = jest.fn();
      UIController._clearActivePreset = jest.fn();
      UIController._clearStoredPreset = jest.fn();
      UIController._setStoredPreset = jest.fn();
      UIController._setTimeRange = jest.fn();
      UIController._switchView = jest.fn();
    });

    test('_switchToLive sets mode and adds active class', () => {
      UIController._dataMode = 'archive';
      UIController.droneTimestamps = { 'd1:s1': '2024-01-01T12:00:00Z' };
      UIController._switchToLive();
      expect(UIController._dataMode).toBe('live');
      expect(UIController.droneTimestamps).toEqual({});
      expect(UIController.elements.liveBtn.classList.contains('active')).toBe(true);
      expect(UIController.elements.liveBtnM.classList.contains('active')).toBe(true);
      expect(UIController.elements.headerTimeControls.classList.contains('disabled')).toBe(true);
      expect(UIController.elements.settingsTimeControls.classList.contains('disabled')).toBe(true);
      expect(UIController.refreshData).toHaveBeenCalled();
    });

    test('_switchToLive toggles to archive when already live', () => {
      UIController._dataMode = 'live';
      UIController.refreshData.mockClear();
      UIController._switchToLive();
      expect(UIController._dataMode).toBe('archive');
      expect(UIController.refreshData).toHaveBeenCalled();
    });

    test('_switchToArchive sets mode and removes active class', () => {
      UIController.droneTimestamps = { 'd1:s1': '2024-01-01T12:00:00Z' };
      UIController._switchToArchive(24);
      expect(UIController._dataMode).toBe('archive');
      expect(UIController.droneTimestamps).toEqual({});
      expect(UIController.elements.liveBtn.classList.contains('active')).toBe(false);
      expect(UIController.elements.liveBtnM.classList.contains('active')).toBe(false);
      expect(UIController.elements.headerTimeControls.classList.contains('disabled')).toBe(false);
      expect(UIController.elements.settingsTimeControls.classList.contains('disabled')).toBe(false);
      expect(UIController._setStoredPreset).toHaveBeenCalledWith(24);
      expect(UIController._setTimeRange).toHaveBeenCalledWith(24);
      expect(UIController.refreshData).toHaveBeenCalled();
    });

    test('_switchToArchive without preset does not set time range', () => {
      UIController._dataMode = 'live';
      UIController.droneTimestamps = { 'd1:s1': '2024-01-01T12:00:00Z' };
      UIController._switchToArchive();
      expect(UIController._dataMode).toBe('archive');
      expect(UIController.droneTimestamps).toEqual({});
      expect(UIController._setTimeRange).not.toHaveBeenCalled();
      expect(UIController.refreshData).toHaveBeenCalled();
    });

    test('live -> archive -> live round trip resets correctly', () => {
      UIController._switchToArchive(24);
      expect(UIController._dataMode).toBe('archive');
      expect(UIController.elements.liveBtn.classList.contains('active')).toBe(false);

      UIController._switchToLive();
      expect(UIController._dataMode).toBe('live');
      expect(UIController.elements.liveBtn.classList.contains('active')).toBe(true);
      expect(UIController.elements.headerTimeControls.classList.contains('disabled')).toBe(true);
      expect(UIController.refreshData).toHaveBeenCalledTimes(2);
    });
  });

  describe('showToast', () => {
    let container;

    beforeEach(() => {
      container = document.createElement('div');
      container.id = 'toastContainer';
      document.body.appendChild(container);
      UIController._consecutiveFailures = 0;
      UIController._connectionLostShown = false;
    });

    afterEach(() => {
      document.body.removeChild(container);
    });

    test('creates a toast element with correct type', () => {
      UIController.showToast('Test error', 'error');
      const toast = container.querySelector('.toast-error');
      expect(toast).not.toBeNull();
      expect(toast.textContent).toContain('Test error');
    });

    test('defaults to error type', () => {
      UIController.showToast('Something broke');
      expect(container.querySelector('.toast-error')).not.toBeNull();
    });

    test('deduplicates by key', () => {
      UIController.showToast('First', 'error', { dedupeKey: 'dup-test' });
      UIController.showToast('Second', 'error', { dedupeKey: 'dup-test' });
      const toasts = container.querySelectorAll('.toast');
      expect(toasts.length).toBe(1);
      expect(toasts[0].textContent).toContain('First');
    });

    test('respects max visible toasts', () => {
      UIController._toastMaxVisible = 2;
      UIController.showToast('one', 'info');
      UIController.showToast('two', 'info');
      UIController.showToast('three', 'info');
      const toasts = container.querySelectorAll('.toast');
      expect(toasts.length).toBe(2);
    });

    test('_dismissToast adds removing class', () => {
      UIController.showToast('Dismiss me', 'info', { duration: 0 });
      const toast = container.querySelector('.toast');
      UIController._dismissToast(toast);
      expect(toast.classList.contains('removing')).toBe(true);
    });
  });

  describe('_trackRefreshFailure', () => {
    let banner;

    beforeEach(() => {
      banner = document.createElement('div');
      banner.id = 'connectionBanner';
      document.body.appendChild(banner);
      UIController._consecutiveFailures = 0;
      UIController._connectionLostShown = false;
    });

    afterEach(() => {
      document.body.removeChild(banner);
    });

    test('increments failure counter on failure', () => {
      UIController._trackRefreshFailure(false);
      UIController._trackRefreshFailure(false);
      expect(UIController._consecutiveFailures).toBe(2);
    });

    test('resets counter on success', () => {
      UIController._consecutiveFailures = 5;
      UIController._trackRefreshFailure(true);
      expect(UIController._consecutiveFailures).toBe(0);
    });

    test('shows banner after 3 consecutive failures', () => {
      UIController._trackRefreshFailure(false);
      UIController._trackRefreshFailure(false);
      UIController._trackRefreshFailure(false);
      expect(banner.style.display).toBe('flex');
      expect(UIController._connectionLostShown).toBe(true);
    });

    test('hides banner on recovery', () => {
      UIController._connectionLostShown = true;
      banner.style.display = 'flex';
      UIController._trackRefreshFailure(true);
      expect(banner.style.display).toBe('none');
      expect(UIController._connectionLostShown).toBe(false);
    });
  });
});
