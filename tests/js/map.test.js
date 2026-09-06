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
  polyline: jest.fn().mockImplementation((points) => ({
    addTo: jest.fn(),
    setStyle: jest.fn(),
    setLatLngs: jest.fn(),
    _latlngs: points,
  })),
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

  describe('getHeightColor', () => {
    beforeEach(() => {
      MapController.colorMode = 'drone';
    });

    test('returns green for 0 ft (0 m)', () => {
      expect(MapController.getHeightColor(0)).toBe('#16a34a');
    });

    test('stays green up to 100 ft', () => {
      expect(MapController.getHeightColor(100 * 0.3048)).toBe('#16a34a');
    });

    test('returns yellow for 100-200 ft', () => {
      expect(MapController.getHeightColor(150 * 0.3048)).toBe('#eab308');
    });

    test('returns pink for 200-300 ft', () => {
      expect(MapController.getHeightColor(250 * 0.3048)).toBe('#ec4899');
    });

    test('returns blue for 300-400 ft', () => {
      expect(MapController.getHeightColor(350 * 0.3048)).toBe('#3b82f6');
    });

    test('returns red at 400 ft and above', () => {
      expect(MapController.getHeightColor(401 * 0.3048)).toBe('#dc2626');
      expect(MapController.getHeightColor(401 * 0.3048 + 100)).toBe('#dc2626');
    });

    test('bands are discrete (same color within a band)', () => {
      expect(MapController.getHeightColor(30 * 0.3048)).toBe('#16a34a');
      expect(MapController.getHeightColor(90 * 0.3048)).toBe('#16a34a');
      expect(MapController.getHeightColor(150 * 0.3048)).toBe('#eab308');
      expect(MapController.getHeightColor(190 * 0.3048)).toBe('#eab308');
    });

    test('output is always a valid hex color', () => {
      const samples = [10, 50, 100, 150, 250, 350, 450].map(h =>
        MapController.getHeightColor(h * 0.3048)
      );
      for (const c of samples) {
        expect(c).toMatch(/^#[0-9a-f]{6}$/);
      }
    });

    test('returns neutral gray for null/undefined/NaN', () => {
      expect(MapController.getHeightColor(null)).toBe('#6c757d');
      expect(MapController.getHeightColor(undefined)).toBe('#6c757d');
      expect(MapController.getHeightColor(NaN)).toBe('#6c757d');
    });
  });

  describe('getHeightBandLabel', () => {
    test('labels bands in feet', () => {
      expect(MapController.getHeightBandLabel(50 * 0.3048)).toBe('0-100 ft');
      expect(MapController.getHeightBandLabel(150 * 0.3048)).toBe('100-200 ft');
      expect(MapController.getHeightBandLabel(250 * 0.3048)).toBe('200-300 ft');
      expect(MapController.getHeightBandLabel(350 * 0.3048)).toBe('300-400 ft');
      expect(MapController.getHeightBandLabel(450 * 0.3048)).toBe('400+ ft');
    });

    test('unknown height returns unknown label', () => {
      expect(MapController.getHeightBandLabel(null)).toBe('unknown');
    });
  });

  describe('setColorMode', () => {
    beforeEach(() => {
      MapController.colorMode = 'drone';
      MapController.tracks = {};
      MapController.replayState.replayMarkers = {};
    });

    test('accepts only drone or height', () => {
      MapController.setColorMode('height');
      expect(MapController.colorMode).toBe('height');
      MapController.setColorMode('drone');
      expect(MapController.colorMode).toBe('drone');
      MapController.setColorMode('bogus');
      expect(MapController.colorMode).toBe('drone');
    });

    test('setColorMode rebuilds track segments and recolors in-flight markers', () => {
      MapController.ready = true;
      MapController.layers.tracks = { removeLayer: jest.fn() };
      const positions = [
        { latitude: 1, longitude: 1, height: 50 * 0.3048 },
        { latitude: 2, longitude: 2, height: 250 * 0.3048 },
        { latitude: 3, longitude: 3, height: 50 * 0.3048 },
      ];
      const seg = L.polyline([[1, 1]], {});
      const marker = {
        setIcon: jest.fn(),
        _markerType: 'drone',
        _uasId: 'd1',
        _height: 150 * 0.3048,
      };
      const track = [seg];
      track.markers = [marker];
      MapController.sessionPositions = { 'd1:s1': positions };
      MapController.tracks = { 'd1:s1': track };

      MapController.setColorMode('height');

      // Track rebuilt: single drone-mode segment -> two banded segments
      expect(MapController.layers.tracks.removeLayer).toHaveBeenCalledWith(seg);
      expect(MapController.tracks['d1:s1'].length).toBe(2);
      expect(MapController.tracks['d1:s1'][0]._heightColor).toBe('#ec4899');
      expect(MapController.tracks['d1:s1'][1]._heightColor).toBe('#16a34a');
      expect(marker.setIcon).toHaveBeenCalled();

      MapController.setColorMode('drone');

      // Back to a single whole-flight segment in the drone color
      expect(MapController.tracks['d1:s1'].length).toBe(1);
      expect(MapController.tracks['d1:s1'][0]._heightColor).toBe(
        MapController.getDroneColor('d1')
      );
    });
  });

  describe('_buildTrackSegments', () => {
    beforeEach(() => {
      MapController.colorMode = 'drone';
      MapController.tracks = {};
    });

    test('drone mode builds a single segment in the drone color', () => {
      const positions = [
        { latitude: 1, longitude: 1, height: 10 },
        { latitude: 2, longitude: 2, height: 20 },
        { latitude: 3, longitude: 3, height: 300 },
      ];
      const segs = MapController._buildTrackSegments(positions, '#ff0000');
      expect(segs.length).toBe(1);
      expect(segs[0]._droneColor).toBe('#ff0000');
      expect(segs[0]._heightColor).toBe('#ff0000');
    });

    test('height mode colors each connection by its destination band', () => {
      MapController.colorMode = 'height';
      const positions = [
        { latitude: 1, longitude: 1, height: 50 * 0.3048 },
        { latitude: 2, longitude: 2, height: 250 * 0.3048 },
        { latitude: 3, longitude: 3, height: 350 * 0.3048 },
      ];
      const segs = MapController._buildTrackSegments(positions, 'hsl(10, 70%, 50%)');
      expect(segs.length).toBe(2);
      expect(segs[0]._heightColor).toBe('#ec4899');
      expect(segs[1]._heightColor).toBe('#3b82f6');
      expect(segs[0]._droneColor).toBe('hsl(10, 70%, 50%)');
    });

    test('height mode groups consecutive positions in the same band', () => {
      MapController.colorMode = 'height';
      const positions = [
        { latitude: 1, longitude: 1, height: 150 * 0.3048 },
        { latitude: 2, longitude: 2, height: 160 * 0.3048 },
        { latitude: 3, longitude: 3, height: 250 * 0.3048 },
      ];
      const segs = MapController._buildTrackSegments(positions, '#ff0000');
      expect(segs.length).toBe(2);
      expect(segs[0]._heightColor).toBe('#eab308');
      expect(segs[1]._heightColor).toBe('#ec4899');
    });

    test('height mode falls back to altitude when height is missing', () => {
      MapController.colorMode = 'height';
      const positions = [
        { latitude: 1, longitude: 1, altitude: 150 * 0.3048 },
        { latitude: 2, longitude: 2, altitude: 160 * 0.3048 },
      ];
      const segs = MapController._buildTrackSegments(positions, '#ff0000');
      expect(segs.length).toBe(1);
      expect(segs[0]._heightColor).toBe('#eab308');
    });

    test('height mode draws white when the destination height is missing', () => {
      MapController.colorMode = 'height';
      const positions = [
        { latitude: 1, longitude: 1, height: 150 * 0.3048 },
        { latitude: 2, longitude: 2 },
        { latitude: 3, longitude: 3, height: 250 * 0.3048 },
      ];
      const segs = MapController._buildTrackSegments(positions, '#ff0000');
      expect(segs.length).toBe(2);
      expect(segs[0]._heightColor).toBe('#ffffff');
      expect(segs[1]._heightColor).toBe('#ec4899');
    });

    test('every drawn connection keeps continuous multi-point segments', () => {
      MapController.colorMode = 'height';
      const positions = [
        { latitude: 1, longitude: 1, height: 50 * 0.3048 },
        { latitude: 2, longitude: 2 },
        { latitude: 3, longitude: 3, height: 100 * 0.3048 },
        { latitude: 4, longitude: 4 },
      ];
      const segs = MapController._buildTrackSegments(positions, '#00ff00');
      expect(segs.length).toBeGreaterThanOrEqual(1);
      // Each segment has both endpoints, so no connection is dropped.
      // Shared boundary points are duplicated, so total points are
      // positions.length + (number of color boundaries).
      const totalPoints = segs.reduce((sum, seg) => sum + seg._latlngs.length, 0);
      expect(totalPoints).toBe(positions.length + (segs.length - 1));
      for (const seg of segs) {
        expect(seg._latlngs.length).toBeGreaterThanOrEqual(2);
      }
    });
  });

  describe('_calculateDistance', () => {
    test('delegates to Units.haversineDistance', () => {
      MapController._calculateDistance(37, -122, 38, -123);
      expect(Units.haversineDistance).toHaveBeenCalledWith(37, -122, 38, -123);
    });
  });

  describe('_collectorNamesForPositions', () => {
    beforeEach(() => {
      MapController.collectorConfigs = [
        { name: 'Node1', color: '#ff0000' },
        { name: 'Node2', color: '#00ff00' },
      ];
    });

    test('returns distinct configured collector names in order', () => {
      const positions = [
        { source: 'Node1' },
        { source: 'Node2' },
        { source: 'Node1' },
        { source: 'Node2' },
      ];
      expect(MapController._collectorNamesForPositions(positions)).toEqual([
        'Node1',
        'Node2',
      ]);
    });

    test('ignores sources that are not configured collectors', () => {
      const positions = [{ source: 'Node1' }, { source: 'api-laptop' }];
      expect(MapController._collectorNamesForPositions(positions)).toEqual([
        'Node1',
      ]);
    });

    test('returns empty array when no positions have source', () => {
      expect(MapController._collectorNamesForPositions([{ source: null }, {}])).toEqual([]);
    });
  });

  describe('_createSessionPointPopup', () => {
    const pos = {
      latitude: 37.7749,
      longitude: -122.4194,
      altitude: 100,
      timestamp: '2024-01-01T12:00:00Z',
    };

    test('shows Seen By row when collector names provided', () => {
      const html = MapController._createSessionPointPopup(
        'drone-001',
        'session_abc',
        pos,
        'End',
        '#ff0000',
        ['Node1', 'Node2']
      );
      expect(html).toContain('Seen By:');
      expect(html).toContain('Node1, Node2');
    });

    test('omits Seen By row when no collector names', () => {
      const html = MapController._createSessionPointPopup(
        'drone-001',
        'session_abc',
        pos,
        'Start',
        '#ff0000',
        []
      );
      expect(html).not.toContain('Seen By:');
    });

    test('escapes collector names in Seen By row', () => {
      const html = MapController._createSessionPointPopup(
        'drone-001',
        'session_abc',
        pos,
        'End',
        '#ff0000',
        ['Node<1>']
      );
      expect(html).toContain('Node&lt;1&gt;');
      expect(html).not.toContain('Node<1>');
    });
  });
});
