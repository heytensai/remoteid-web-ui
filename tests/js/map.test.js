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
  marker: jest.fn().mockImplementation(() => {
    const m = {
      addTo: jest.fn(),
      bindPopup: jest.fn(),
      getLatLng: jest.fn().mockReturnValue({ lat: 0, lng: 0 }),
      setZIndexOffset: jest.fn(),
      openPopup: jest.fn(),
    };
    m.addTo.mockReturnValue(m);
    return m;
  }),
  polyline: jest.fn().mockImplementation((points) => ({
    addTo: jest.fn(),
    setStyle: jest.fn(),
    setLatLngs: jest.fn(),
    addLatLng: jest.fn(),
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
    drone_aliases: { 'drone-001': { alias: 'Alpha', trusted: false } },
  }),
  getTrack: jest.fn().mockResolvedValue({ sessions: [] }),
};

// Mock Units
global.Units = {
  formatDistance: jest.fn().mockReturnValue('100 m'),
  formatAltitude: jest.fn().mockReturnValue('100m'),
  formatFrequency: jest.fn((band) => {
    if (band === 'ble') return 'BLE';
    if (band === '2.4ghz') return '2.4 GHz';
    if (band === '5.8ghz') return '5.8 GHz';
    return 'Unknown';
  }),
  frequencyBadgeClass: jest.fn((band) => 'freq-band-' + String(band).replace('.', '-')),
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

  describe('_sanitizeColor', () => {
    test('passes through hex colors', () => {
      expect(MapController._sanitizeColor('#e67e22', '#007bff')).toBe('#e67e22');
      expect(MapController._sanitizeColor('#abc', '#007bff')).toBe('#abc');
      expect(MapController._sanitizeColor('#11223344', '#007bff')).toBe('#11223344');
    });

    test('passes through simple named colors', () => {
      expect(MapController._sanitizeColor('red', '#007bff')).toBe('red');
      expect(MapController._sanitizeColor('rebeccapurple', '#007bff')).toBe('rebeccapurple');
    });

    test('rejects injected HTML/attribute breakout', () => {
      expect(MapController._sanitizeColor('red" onmouseover="alert(1)', '#007bff')).toBe('#007bff');
      expect(MapController._sanitizeColor('<script>alert(1)</script>', '#007bff')).toBe('#007bff');
      expect(MapController._sanitizeColor('red; background:url(x)', '#007bff')).toBe('#007bff');
      expect(MapController._sanitizeColor('`backtick`', '#007bff')).toBe('#007bff');
    });

    test('rejects non-string and empty values with fallback', () => {
      expect(MapController._sanitizeColor(null, '#007bff')).toBe('#007bff');
      expect(MapController._sanitizeColor(12345, '#007bff')).toBe('#007bff');
      expect(MapController._sanitizeColor('', '#007bff')).toBe('#007bff');
      expect(MapController._sanitizeColor(undefined, '#007bff')).toBe('#007bff');
    });

    test('uses the provided fallback, not a hardcoded default', () => {
      expect(MapController._sanitizeColor('bad value', '#e67e22')).toBe('#e67e22');
    });
  });

  describe('getDroneName', () => {
    test('returns alias if available', () => {
      MapController.droneAliases = {
        'drone-001': { alias: 'Alpha', trusted: false },
      };
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

    test('tolerates the legacy string alias shape', () => {
      MapController.droneAliases = { 'drone-001': 'Alpha' };
      expect(MapController.getDroneName('drone-001')).toBe('Alpha');
    });
  });

  describe('isDroneTrusted', () => {
    test('true only for aliased drones with trusted flag', () => {
      MapController.droneAliases = {
        'drone-001': { alias: 'Alpha', trusted: false },
        'drone-002': { alias: 'Trusted', trusted: true },
      };
      expect(MapController.isDroneTrusted('drone-001')).toBe(false);
      expect(MapController.isDroneTrusted('drone-002')).toBe(true);
    });

    test('false for unlisted drones', () => {
      expect(MapController.isDroneTrusted('unknown-drone')).toBe(false);
    });

    test('false for the legacy string alias shape', () => {
      MapController.droneAliases = { 'drone-001': 'Alpha' };
      expect(MapController.isDroneTrusted('drone-001')).toBe(false);
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

  describe('_appendTrackPositions', () => {
    beforeEach(() => {
      MapController.ready = true;
      MapController.isLiveMode = false;
      MapController.colorMode = 'drone';
      MapController.layers.tracks = { removeLayer: jest.fn() };
      MapController.collectorConfigs = [];
      MapController.alertUasIds = new Set();
      MapController.staleTimeout = 300;
      MapController.droneAliases = {};
      MapController.tracks = {};
      MapController.sessionPositions = {};
    });

    test('returns false when the session was never drawn', () => {
      const result = MapController._appendTrackPositions(
        'd1', 's1',
        [{ latitude: 2, longitude: 2, height: 20, timestamp: '2024-01-01T00:00:01Z' }],
        '#ff0000'
      );
      expect(result).toBe(false);
    });

    test('extends the existing polyline in place when the color band continues', () => {
      const existing = [
        { latitude: 1, longitude: 1, height: 10, timestamp: '2024-01-01T00:00:00Z' },
      ];
      const seg = L.polyline([[1, 1]], {});
      seg._heightColor = '#ff0000';
      seg._droneColor = '#ff0000';
      const startMarker = { _markerType: undefined };
      const endMarker = { _markerType: 'stop', addTo: jest.fn(), bindPopup: jest.fn() };
      const track = [seg];
      track.markers = [startMarker, endMarker];
      MapController.sessionPositions = { 'd1:s1': existing };
      MapController.tracks = { 'd1:s1': track };

      const result = MapController._appendTrackPositions(
        'd1', 's1',
        [{ latitude: 2, longitude: 2, height: 20, timestamp: '2024-01-01T00:00:01Z' }],
        '#ff0000'
      );

      expect(result).toBe(true);
      expect(seg.addLatLng).toHaveBeenCalledWith([2, 2]);
      expect(track.length).toBe(1);
      expect(track[0]).toBe(seg);
      expect(MapController.sessionPositions['d1:s1'].length).toBe(2);
      // Start marker kept; old end marker removed and replaced with a new one.
      expect(MapController.layers.tracks.removeLayer).toHaveBeenCalledWith(endMarker);
      expect(track.markers[0]).toBe(startMarker);
      expect(track.markers.length).toBe(2);
      expect(track.markers[1]).not.toBe(endMarker);
    });

    test('adds a new segment when the height band changes', () => {
      MapController.colorMode = 'height';
      const existing = [
        { latitude: 1, longitude: 1, height: 150 * 0.3048, timestamp: '2024-01-01T00:00:00Z' },
      ];
      const seg = L.polyline([[1, 1]], {});
      seg._heightColor = '#eab308';
      seg._droneColor = '#ff0000';
      const track = [seg];
      track.markers = [];
      MapController.sessionPositions = { 'd1:s1': existing };
      MapController.tracks = { 'd1:s1': track };

      MapController._appendTrackPositions(
        'd1', 's1',
        [{ latitude: 2, longitude: 2, height: 250 * 0.3048, timestamp: '2024-01-01T00:00:01Z' }],
        '#ff0000'
      );

      expect(track.length).toBe(2);
      expect(track[1]._heightColor).toBe('#ec4899');
      expect(track[1].addTo).toHaveBeenCalledWith(MapController.layers.tracks);
      expect(seg.addLatLng).not.toHaveBeenCalled();
    });

    test('ignores positions no newer than the last drawn point', () => {
      const existing = [
        { latitude: 1, longitude: 1, height: 10, timestamp: '2024-01-01T00:00:00Z' },
      ];
      const seg = L.polyline([[1, 1]], {});
      seg._heightColor = '#ff0000';
      seg._droneColor = '#ff0000';
      const endMarker = { _markerType: 'stop', addTo: jest.fn(), bindPopup: jest.fn() };
      const track = [seg];
      track.markers = [{ _markerType: undefined }, endMarker];
      MapController.sessionPositions = { 'd1:s1': existing };
      MapController.tracks = { 'd1:s1': track };

      const result = MapController._appendTrackPositions(
        'd1', 's1',
        [{ latitude: 2, longitude: 2, height: 20, timestamp: '2024-01-01T00:00:00Z' }],
        '#ff0000'
      );

      expect(result).toBe(true);
      expect(seg.addLatLng).not.toHaveBeenCalled();
      expect(MapController.sessionPositions['d1:s1'].length).toBe(1);
      expect(MapController.layers.tracks.removeLayer).not.toHaveBeenCalledWith(endMarker);
    });

    test('upgrades a single-position live marker into start+end markers', () => {
      const existing = [
        { latitude: 1, longitude: 1, height: 10, timestamp: '2024-01-01T00:00:00Z' },
      ];
      const single = { _markerType: 'drone', addTo: jest.fn(), bindPopup: jest.fn() };
      const track = [];
      track.markers = [single];
      MapController.sessionPositions = { 'd1:s1': existing };
      MapController.tracks = { 'd1:s1': track };

      MapController._appendTrackPositions(
        'd1', 's1',
        [{ latitude: 2, longitude: 2, height: 20, timestamp: '2024-01-01T00:00:01Z' }],
        '#ff0000'
      );

      expect(MapController.layers.tracks.removeLayer).toHaveBeenCalledWith(single);
      expect(track.markers.length).toBe(2);
      expect(track.markers[0]._markerType).toBe(undefined);
      expect(track.markers[1]._markerType).toBe('stop');
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

    test('uses sources array when present (collapsed multi-collector point)', () => {
      const positions = [
        { source: 'Node1', sources: ['Node1', 'Node2'] },
        { source: 'Node2', sources: ['Node2', 'Node1'] },
      ];
      expect(MapController._collectorNamesForPositions(positions)).toEqual([
        'Node1',
        'Node2',
      ]);
    });

    test('falls back to source when sources array is empty', () => {
      const positions = [{ source: 'Node1', sources: [] }];
      expect(MapController._collectorNamesForPositions(positions)).toEqual(['Node1']);
    });
  });

  describe('_frequencyLabelsForPositions', () => {
    test('formats frequencies array, distinct and in order', () => {
      const positions = [
        { source: 'Node1', frequencies: ['2.4ghz', 'ble'] },
        { source: 'Node2', frequencies: ['2.4ghz', '5.8ghz'] },
      ];
      expect(MapController._frequencyLabelsForPositions(positions)).toEqual([
        '2.4 GHz',
        'BLE',
        '5.8 GHz',
      ]);
    });

    test('falls back to single frequency field', () => {
      const positions = [{ source: 'Node1', frequency: '5.8ghz' }];
      expect(MapController._frequencyLabelsForPositions(positions)).toEqual([
        '5.8 GHz',
      ]);
    });

    test('returns empty when no band data', () => {
      expect(MapController._frequencyLabelsForPositions([])).toEqual([]);
      expect(MapController._frequencyLabelsForPositions([{ source: 'Node1' }])).toEqual([]);
      expect(MapController._frequencyLabelsForPositions([{ source: 'Node1', frequency: 'unknown' }])).toEqual([]);
    });
  });

  describe('_droneAnnotationContent', () => {
    beforeEach(() => {
      MapController.droneAliases = {};
      Units.formatAltitude.mockClear();
      Units.formatAltitude.mockReturnValue('100m');
    });

    const withHeight = {
      latitude: 37.77,
      longitude: -122.41,
      height: 150,
      altitude: 100,
      timestamp: '2024-01-01T12:00:00Z',
    };

    test('shows the friendly alias on the first line when available', () => {
      MapController.droneAliases = { 'drone-001': 'Alpha' };
      const html = MapController._droneAnnotationContent('drone-001', withHeight);
      expect(html).toContain('class="drone-annotation-id"');
      expect(html).toContain('Alpha');
    });

    test('shows the serial (uas_id) when no alias is set', () => {
      const html = MapController._droneAnnotationContent('W34-ABC123', withHeight);
      expect(html).toContain('W34-ABC123');
    });

    test('escapes the display name', () => {
      MapController.droneAliases = { 'drone-001': '<script>alert(1)</script>' };
      const html = MapController._droneAnnotationContent('drone-001', withHeight);
      expect(html).not.toContain('<script>');
      expect(html).toContain('&lt;script&gt;');
    });

    test('formats the height of the latest packet on the second line', () => {
      MapController._droneAnnotationContent('drone-001', withHeight);
      expect(Units.formatAltitude).toHaveBeenCalledWith(150, true, 0);
      const html = MapController._droneAnnotationContent('drone-001', withHeight);
      expect(html).toContain('class="drone-annotation-meta"');
      expect(html).toContain('100m');
    });

    test('falls back to altitude when height is missing', () => {
      const pos = { latitude: 1, longitude: 2, altitude: 88, timestamp: '2024-01-01T12:00:00Z' };
      MapController._droneAnnotationContent('drone-001', pos);
      expect(Units.formatAltitude).toHaveBeenCalledWith(88, true, 0);
    });

    test('shows the time (no date) of the latest packet', () => {
      const html = MapController._droneAnnotationContent('drone-001', withHeight);
      expect(html).toContain('&middot;');
      expect(html).not.toContain('2024-01-01');
    });

    test('renders both lines, no field labels', () => {
      const html = MapController._droneAnnotationContent('drone-001', withHeight);
      expect(html).toContain('drone-annotation-id');
      expect(html).toContain('drone-annotation-meta');
      expect(html).not.toMatch(/alias|height|time|altitude/i);
    });
  });

  describe('_bindDroneAnnotation', () => {
    beforeEach(() => {
      MapController.isLiveMode = false;
    });

    test('does nothing outside live mode', () => {
      const marker = { bindTooltip: jest.fn() };
      MapController._bindDroneAnnotation(marker, 'drone-001', {});
      expect(marker.bindTooltip).not.toHaveBeenCalled();
    });

    test('binds a permanent top tooltip in live mode', () => {
      MapController.isLiveMode = true;
      MapController.droneAliases = { 'drone-001': 'Alpha' };
      const marker = { bindTooltip: jest.fn() };
      const pos = { height: 20, timestamp: '2024-01-01T12:00:00Z' };
      MapController._bindDroneAnnotation(marker, 'drone-001', pos);
      expect(marker.bindTooltip).toHaveBeenCalledWith(
        expect.stringContaining('Alpha'),
        expect.objectContaining({
          permanent: true,
          direction: 'top',
          className: 'drone-annotation',
        })
      );
    });
  });

  describe('_refreshAllDroneAnnotations', () => {
    const key = 'drone-001:session_abc';
    const pos = { height: 20, timestamp: '2024-01-01T12:00:00Z' };

    const setup = (markers) => {
      MapController.tracks = { [key]: { markers } };
      MapController.sessionPositions = { [key]: [pos] };
    };

    beforeEach(() => {
      MapController.tracks = {};
      MapController.sessionPositions = {};
      MapController.isLiveMode = false;
    });

    test('binds annotations to existing drone markers when entering live mode', () => {
      const marker = { _markerType: 'drone', _uasId: 'drone-001', bindTooltip: jest.fn() };
      setup([marker]);
      MapController.isLiveMode = true;
      MapController._refreshAllDroneAnnotations();
      expect(marker.bindTooltip).toHaveBeenCalledWith(
        expect.stringContaining('drone-001'),
        expect.objectContaining({ permanent: true })
      );
    });

    test('unbinds annotations when leaving live mode', () => {
      const marker = { _markerType: 'drone', _uasId: 'drone-001', unbindTooltip: jest.fn() };
      setup([marker]);
      MapController.isLiveMode = false;
      MapController._refreshAllDroneAnnotations();
      expect(marker.unbindTooltip).toHaveBeenCalled();
    });

    test('leaves start/stop markers untouched', () => {
      const start = { _markerType: 'start', bindTooltip: jest.fn(), unbindTooltip: jest.fn() };
      const stop = { _markerType: 'stop', bindTooltip: jest.fn(), unbindTooltip: jest.fn() };
      setup([start, stop]);
      MapController.isLiveMode = true;
      MapController._refreshAllDroneAnnotations();
      expect(start.bindTooltip).not.toHaveBeenCalled();
      expect(stop.bindTooltip).not.toHaveBeenCalled();
    });

    test('skips tracks with no stored positions', () => {
      const marker = { _markerType: 'drone', _uasId: 'drone-001', bindTooltip: jest.fn() };
      MapController.tracks = { [key]: { markers: [marker] } };
      MapController.sessionPositions = {};
      MapController.isLiveMode = true;
      MapController._refreshAllDroneAnnotations();
      expect(marker.bindTooltip).not.toHaveBeenCalled();
    });

    test('setLiveMode(true) annotates already-drawn markers', () => {
      const marker = { _markerType: 'drone', _uasId: 'drone-001', bindTooltip: jest.fn() };
      setup([marker]);
      MapController.setLiveMode(true);
      expect(marker.bindTooltip).toHaveBeenCalled();
    });

    test('setLiveMode(false) strips annotations from already-drawn markers', () => {
      const marker = { _markerType: 'drone', _uasId: 'drone-001', unbindTooltip: jest.fn() };
      setup([marker]);
      MapController.setLiveMode(false);
      expect(marker.unbindTooltip).toHaveBeenCalled();
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

  describe('tileProviders registry', () => {
    test('every provider declares url, attribution and maxNativeZoom', () => {
      const names = Object.keys(MapController.tileProviders);
      expect(names.length).toBeGreaterThan(0);
      for (const name of names) {
        const p = MapController.tileProviders[name];
        expect(typeof p.url).toBe('string');
        expect(p.url).toMatch(/^https:\/\//);
        // Attribution strings are license-required, never optional.
        expect(typeof p.attribution).toBe('string');
        expect(p.attribution.length).toBeGreaterThan(0);
        expect(Number.isInteger(p.maxNativeZoom)).toBe(true);
        expect(typeof p.invertInDarkMode).toBe('boolean');
      }
    });

    test('no provider exceeds the shared map maxZoom', () => {
      for (const [name, p] of Object.entries(MapController.tileProviders)) {
        expect(p.maxNativeZoom).toBeLessThanOrEqual(MapController.tileMaxZoom);
        // Otherwise the map would zoom past what it is allowed to request.
        expect(name).toBeTruthy();
      }
    });

    test('esri uses the ArcGIS {z}/{y}/{x} ordering', () => {
      const url = MapController.tileProviders['esri-satellite'].url;
      expect(url).toContain('/tile/{z}/{y}/{x}');
      expect(url).not.toContain('{z}/{x}/{y}');
    });

    test('esri has no {s} subdomain placeholder', () => {
      expect(MapController.tileProviders['esri-satellite'].url).not.toContain('{s}');
    });

    test('satellite opts out of the dark-mode tile invert', () => {
      expect(MapController.tileProviders['esri-satellite'].invertInDarkMode).toBe(false);
    });

    test('raster providers stay inverted in dark mode', () => {
      expect(MapController.tileProviders.osm.invertInDarkMode).toBe(true);
      expect(MapController.tileProviders.opentopomap.invertInDarkMode).toBe(true);
    });

    test('opentopomap declares its native z17 limit', () => {
      expect(MapController.tileProviders.opentopomap.maxNativeZoom).toBe(17);
    });

    test('includes the two providers added for issue #152', () => {
      expect(Object.keys(MapController.tileProviders)).toEqual(
        expect.arrayContaining(['esri-satellite', 'opentopomap'])
      );
    });

    test('every provider has a human-readable label', () => {
      // The Settings → Base Map picker shows this verbatim.
      for (const [name, p] of Object.entries(MapController.tileProviders)) {
        expect(typeof p.label).toBe('string');
        expect(p.label.length).toBeGreaterThan(0);
        // A label identical to its key means nobody wrote one.
        expect(p.label).not.toBe(name);
      }
    });

    test('no provider points at a CARTO host', () => {
      // CARTO withdrew keyless basemap access; leaving an entry would render a
      // layer that never loads.
      for (const [name, p] of Object.entries(MapController.tileProviders)) {
        expect(p.url).not.toContain('cartocdn');
        expect(name).not.toMatch(/^carto-/);
      }
    });
  });

  describe('getEnabledTileProviders', () => {
    afterEach(() => {
      MapController.enabledTileProviders = [];
    });

    test('an empty list means every known provider', () => {
      expect(MapController.getEnabledTileProviders()).toEqual(
        Object.keys(MapController.tileProviders)
      );
    });

    test('a missing list means every known provider', () => {
      MapController.enabledTileProviders = null;
      expect(MapController.getEnabledTileProviders()).toEqual(
        Object.keys(MapController.tileProviders)
      );
    });

    test('honors the server-supplied subset in order', () => {
      MapController.enabledTileProviders = ['opentopomap', 'osm'];
      expect(MapController.getEnabledTileProviders()).toEqual(['opentopomap', 'osm']);
    });

    test('drops names that are not in the registry', () => {
      MapController.enabledTileProviders = ['osm', 'not-a-provider'];
      expect(MapController.getEnabledTileProviders()).toEqual(['osm']);
    });

    test('falls back to all when every entry is unknown', () => {
      MapController.enabledTileProviders = ['nope', 'also-nope'];
      expect(MapController.getEnabledTileProviders()).toEqual(
        Object.keys(MapController.tileProviders)
      );
    });
  });

  describe('_clampToEnabled', () => {
    beforeEach(() => {
      MapController.config = { tile_provider: 'osm' };
      MapController.enabledTileProviders = ['osm', 'esri-satellite'];
    });

    afterEach(() => {
      MapController.enabledTileProviders = [];
      MapController.config = { tile_provider: 'osm' };
    });

    test('passes an enabled provider through', () => {
      expect(MapController._clampToEnabled('esri-satellite').name).toBe('esri-satellite');
    });

    test('rejects a provider the admin disabled', () => {
      // Falls back to the configured startup provider, never to a blocked one.
      expect(MapController._clampToEnabled('opentopomap').name).toBe('osm');
    });

    test('falls back to the configured provider when the default is disabled', () => {
      MapController.config = { tile_provider: 'esri-satellite' };
      expect(MapController._clampToEnabled('opentopomap').name).toBe('esri-satellite');
    });

    test('never returns a provider outside the enabled set', () => {
      for (const candidate of [undefined, null, '', 'bogus', 'opentopomap']) {
        expect(MapController.getEnabledTileProviders()).toContain(
          MapController._clampToEnabled(candidate).name
        );
      }
    });
  });

  describe('setTileProvider', () => {
    let container;

    beforeEach(() => {
      L.tileLayer.mockClear();
      container = document.createElement('div');
      document.body.appendChild(container);
      const removed = [];
      MapController.map = {
        getContainer: () => container,
        removeLayer: (layer) => removed.push(layer),
      };
      MapController._removedLayers = removed;
      MapController.config = { tile_provider: 'osm' };
      MapController.activeTileProvider = null;
      MapController.enabledTileProviders = [];
    });

    afterEach(() => {
      container.remove();
      MapController.map = null;
      MapController.tileLayer = null;
      MapController.activeTileProvider = null;
      MapController.enabledTileProviders = [];
    });

    test('swaps the rendered layer and records the active provider', () => {
      MapController.tileLayer = { id: 'old' };
      const applied = MapController.setTileProvider('esri-satellite');
      expect(applied).toBe('esri-satellite');
      expect(MapController.activeTileProvider).toBe('esri-satellite');
      expect(MapController._removedLayers).toContainEqual({ id: 'old' });
      expect(L.tileLayer).toHaveBeenCalledWith(
        MapController.tileProviders['esri-satellite'].url,
        expect.anything()
      );
    });

    test('does not mutate the server-provided startup config', () => {
      // config.tile_provider must keep meaning "startup default".
      MapController.tileLayer = { id: 'old' };
      MapController.setTileProvider('opentopomap');
      expect(MapController.config.tile_provider).toBe('osm');
    });

    test('clamps a disabled provider instead of rendering blocked tiles', () => {
      MapController.enabledTileProviders = ['osm'];
      MapController.tileLayer = { id: 'old' };
      expect(MapController.setTileProvider('esri-satellite')).toBe('osm');
      expect(L.tileLayer).toHaveBeenCalledWith(
        MapController.tileProviders.osm.url,
        expect.anything()
      );
    });

    test('records the active provider without a map yet', () => {
      MapController.map = null;
      MapController.tileLayer = null;
      expect(MapController.setTileProvider('opentopomap')).toBe('opentopomap');
      expect(MapController.activeTileProvider).toBe('opentopomap');
      expect(L.tileLayer).not.toHaveBeenCalled();
    });

    test('still swaps the layer when no prior layer exists', () => {
      // Guards the gate on `this.map` rather than `this.tileLayer`.
      MapController.tileLayer = null;
      expect(MapController.setTileProvider('opentopomap')).toBe('opentopomap');
      expect(L.tileLayer).toHaveBeenCalledWith(
        MapController.tileProviders.opentopomap.url,
        expect.anything()
      );
      expect(MapController._removedLayers).toEqual([]);
    });

    test('toggles the dark-mode invert class with the provider', () => {
      MapController.tileLayer = { id: 'old' };
      MapController.setTileProvider('esri-satellite');
      expect(container.classList.contains('no-tile-invert')).toBe(true);
      MapController.setTileProvider('osm');
      expect(container.classList.contains('no-tile-invert')).toBe(false);
    });
  });

  describe('_resolveTileProvider', () => {
    test('resolves a known provider', () => {
      const p = MapController._resolveTileProvider('opentopomap');
      expect(p.name).toBe('opentopomap');
      expect(p.maxNativeZoom).toBe(17);
    });

    test('falls back to osm for an unknown provider', () => {
      const p = MapController._resolveTileProvider('not-a-provider');
      expect(p.name).toBe(MapController.defaultTileProvider);
      expect(p.url).toBe(MapController.tileProviders.osm.url);
    });

    test('falls back for undefined/null/empty input', () => {
      for (const bad of [undefined, null, '']) {
        expect(MapController._resolveTileProvider(bad).name).toBe('osm');
      }
    });

    test('a provider entry cannot spoof its own resolved name', () => {
      // The config key is authoritative: a stray `name` field in a registry
      // entry must not win, or attribution/debug output would mislabel the layer.
      const saved = MapController.tileProviders.opentopomap;
      MapController.tileProviders.opentopomap = Object.assign({}, saved, { name: 'spoofed' });
      try {
        expect(MapController._resolveTileProvider('opentopomap').name).toBe('opentopomap');
      } finally {
        MapController.tileProviders.opentopomap = saved;
      }
    });
  });

  describe('_addTileLayer', () => {
    let container;

    beforeEach(() => {
      L.tileLayer.mockClear();
      container = document.createElement('div');
      container.id = 'map';
      document.body.appendChild(container);
      MapController.map = { getContainer: () => container };
      MapController.config = { tile_provider: 'osm' };
      // No runtime choice yet: the layer must follow config.tile_provider.
      MapController.activeTileProvider = null;
      MapController.enabledTileProviders = [];
    });

    afterEach(() => {
      container.remove();
      MapController.map = null;
      MapController.activeTileProvider = null;
      MapController.enabledTileProviders = [];
    });

    test('uses the configured provider url and attribution', () => {
      MapController.config.tile_provider = 'esri-satellite';
      MapController._addTileLayer();
      expect(L.tileLayer).toHaveBeenCalledWith(
        MapController.tileProviders['esri-satellite'].url,
        expect.objectContaining({
          attribution: MapController.tileProviders['esri-satellite'].attribution,
        })
      );
    });

    test('passes maxNativeZoom so low-zoom-limit providers up-scale', () => {
      MapController.config.tile_provider = 'opentopomap';
      MapController._addTileLayer();
      const [, opts] = L.tileLayer.mock.calls[0];
      expect(opts.maxNativeZoom).toBe(17);
      expect(opts.maxZoom).toBe(MapController.tileMaxZoom);
    });

    test('unknown provider still renders the default basemap', () => {
      MapController.config.tile_provider = 'bogus';
      MapController._addTileLayer();
      expect(L.tileLayer).toHaveBeenCalledWith(
        MapController.tileProviders.osm.url,
        expect.anything()
      );
    });

    test('marks the container to skip the dark-mode invert for imagery', () => {
      MapController.config.tile_provider = 'esri-satellite';
      MapController._addTileLayer();
      expect(container.classList.contains('no-tile-invert')).toBe(true);
    });

    test('does not mark the container for raster providers', () => {
      MapController.config.tile_provider = 'osm';
      MapController._addTileLayer();
      expect(container.classList.contains('no-tile-invert')).toBe(false);
    });
  });
});
