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
});
