/**
 * Tests for static/js/map.js
 * Tests pure functions only (escapeHtml, getDroneColor, getDroneName).
 * Leaflet-dependent methods require a real browser.
 */
const fs = require('fs');
const path = require('path');

let mapCode = fs.readFileSync(
  path.resolve(__dirname, '../../static/js/map.js'),
  'utf8'
);

// Mock Leaflet globally before eval
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
  marker: jest
    .fn()
    .mockReturnValue({ addTo: jest.fn(), bindPopup: jest.fn(), getLatLng: jest.fn().mockReturnValue({ lat: 0, lng: 0 }), setZIndexOffset: jest.fn(), openPopup: jest.fn() }),
  polyline: jest.fn().mockReturnValue({ addTo: jest.fn(), setStyle: jest.fn() }),
  divIcon: jest.fn().mockReturnValue({}),
  layerGroup: jest.fn().mockReturnValue({
    addTo: jest.fn(),
    clearLayers: jest.fn(),
    removeLayer: jest.fn(),
  }),
};

// Mock API
global.API = {
  getConfig: jest.fn().mockResolvedValue({
    map: { center_lat: 37, center_lon: -122, default_zoom: 11 },
    drone_aliases: { 'drone-001': 'Alpha' },
  }),
  getTrack: jest.fn().mockResolvedValue({ sessions: [] }),
};

// Mock Units
global.Units = {
  formatDistance: jest.fn().mockReturnValue('100 m'),
  formatAltitude: jest.fn().mockReturnValue('100m'),
  haversineDistance: jest.fn().mockReturnValue(0),
};

// Remove the auto-init at end, strip const so eval assigns globally
mapCode = mapCode
  .replace(/\/\/ Initialize map when DOM is ready\n.*$/, '')
  .replace(/^const /m, '');
(0, eval)(mapCode);

describe('MapController', () => {
  beforeEach(() => {
    MapController.droneAliases = {};
  });

  describe('escapeHtml', () => {
    test('escapes HTML special characters', () => {
      const result = MapController.escapeHtml('<script>alert("xss")</script>');
      expect(result).toContain('&lt;script&gt;');
      expect(result).toContain('&lt;/script&gt;');
      expect(result).not.toContain('<script>');
    });

    test('returns empty string for null', () => {
      expect(MapController.escapeHtml(null)).toBe('');
    });

    test('returns empty string for undefined', () => {
      expect(MapController.escapeHtml(undefined)).toBe('');
    });

    test('passes through safe strings', () => {
      expect(MapController.escapeHtml('hello world')).toBe('hello world');
    });

    test('escapes & < > "', () => {
      const result = MapController.escapeHtml('&<>"');
      expect(result).toContain('&amp;');
      expect(result).toContain('&lt;');
      expect(result).toContain('&gt;');
      expect(result).not.toContain('<');
      expect(result).not.toContain('>');
    });
  });

  describe('getDroneColor', () => {
    test('returns HSL string', () => {
      const color = MapController.getDroneColor('drone-001');
      expect(color).toMatch(/^hsl\(\d+, 70%, 50%\)$/);
    });

    test('same ID produces same color', () => {
      const c1 = MapController.getDroneColor('drone-001');
      const c2 = MapController.getDroneColor('drone-001');
      expect(c1).toBe(c2);
    });

    test('different IDs produce different colors', () => {
      const c1 = MapController.getDroneColor('drone-001');
      const c2 = MapController.getDroneColor('drone-002');
      expect(c1).not.toBe(c2);
    });

    test('hue is in valid range', () => {
      const ids = ['a', 'b', 'abc', 'longer-id-123', 'special_chars!@#'];
      for (const id of ids) {
        const color = MapController.getDroneColor(id);
        const hue = parseInt(color.match(/\d+/)[0], 10);
        expect(hue).toBeGreaterThanOrEqual(0);
        expect(hue).toBeLessThan(360);
      }
    });
  });

  describe('getDroneName', () => {
    test('returns alias if available', () => {
      MapController.droneAliases = { 'drone-001': 'Alpha' };
      expect(MapController.getDroneName('drone-001')).toBe('Alpha');
    });

    test('returns uas_id if no alias', () => {
      expect(MapController.getDroneName('unknown-drone')).toBe(
        'unknown-drone'
      );
    });

    test('returns uas_id when aliases empty', () => {
      expect(MapController.getDroneName('drone-001')).toBe('drone-001');
    });
  });

  describe('_calculateDistance', () => {
    test('delegates to Units.haversineDistance', () => {
      MapController._calculateDistance(37, -122, 38, -123);
      expect(Units.haversineDistance).toHaveBeenCalledWith(37, -122, 38, -123);
    });
  });
});
