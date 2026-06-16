/**
 * Tests for static/js/units.js
 * Load the file as a global, then test the Units singleton.
 */
const fs = require('fs');
const path = require('path');

let unitsCode = fs.readFileSync(
  path.resolve(__dirname, '../../static/js/units.js'),
  'utf8'
);
unitsCode = unitsCode.replace(/^const /m, '');
(0, eval)(unitsCode);

describe('Units', () => {
  beforeEach(() => {
    Units.useMetric = true;
  });

  describe('init', () => {
    test('sets metric from config', () => {
      Units.init({ use_metric: true });
      expect(Units.useMetric).toBe(true);
    });

    test('sets imperial from config', () => {
      Units.init({ use_metric: false });
      expect(Units.useMetric).toBe(false);
    });

    test('defaults to true when not in config', () => {
      Units.init({});
      expect(Units.useMetric).toBe(true);
    });
  });

  describe('formatDistance', () => {
    describe('metric', () => {
      test('formats meters', () => {
        expect(Units.formatDistance(500)).toBe('500 m');
      });

      test('formats kilometers', () => {
        expect(Units.formatDistance(1500)).toBe('1.50 km');
      });

      test('handles zero', () => {
        expect(Units.formatDistance(0)).toBe('0 m');
      });

      test('handles null/undefined', () => {
        expect(Units.formatDistance(null)).toBe('N/A');
        expect(Units.formatDistance(undefined)).toBe('N/A');
        expect(Units.formatDistance(NaN)).toBe('N/A');
      });

      test('formats without unit', () => {
        expect(Units.formatDistance(500, false)).toBe('500');
      });

      test('formats exact km boundary', () => {
        expect(Units.formatDistance(1000)).toBe('1.00 km');
      });
    });

    describe('imperial', () => {
      beforeEach(() => {
        Units.useMetric = false;
      });

      test('formats feet', () => {
        expect(Units.formatDistance(100)).toBe('328 ft');
      });

      test('formats miles', () => {
        expect(Units.formatDistance(2000)).toBe('1.24 mi');
      });

      test('handles zero', () => {
        expect(Units.formatDistance(0)).toBe('0 ft');
      });

      test('handles null', () => {
        expect(Units.formatDistance(null)).toBe('N/A');
      });
    });
  });

  describe('formatAltitude', () => {
    describe('metric', () => {
      test('formats altitude', () => {
        expect(Units.formatAltitude(100)).toBe('100m');
      });

      test('formats with decimals', () => {
        expect(Units.formatAltitude(100.5, true, 1)).toBe('100.5m');
      });

      test('handles null', () => {
        expect(Units.formatAltitude(null)).toBe('N/A');
      });
    });

    describe('imperial', () => {
      beforeEach(() => {
        Units.useMetric = false;
      });

      test('converts to feet', () => {
        expect(Units.formatAltitude(100)).toBe('328ft');
      });

      test('formats with decimals', () => {
        expect(Units.formatAltitude(100, true, 1)).toBe('328.1ft');
      });
    });
  });

  describe('formatSpeed', () => {
    describe('metric', () => {
      test('converts m/s to km/h', () => {
        expect(Units.formatSpeed(10)).toBe('36.0 km/h');
      });

      test('handles zero', () => {
        expect(Units.formatSpeed(0)).toBe('0.0 km/h');
      });

      test('handles null', () => {
        expect(Units.formatSpeed(null)).toBe('N/A');
      });
    });

    describe('imperial', () => {
      beforeEach(() => {
        Units.useMetric = false;
      });

      test('converts m/s to mph', () => {
        expect(Units.formatSpeed(10)).toBe('22.4 mph');
      });
    });
  });

  describe('getSystem', () => {
    test('returns metric', () => {
      Units.useMetric = true;
      expect(Units.getSystem()).toBe('metric');
    });

    test('returns imperial', () => {
      Units.useMetric = false;
      expect(Units.getSystem()).toBe('imperial');
    });
  });

  describe('getAltitudeUnit', () => {
    test('returns m for metric', () => {
      Units.useMetric = true;
      expect(Units.getAltitudeUnit()).toBe('m');
    });

    test('returns ft for imperial', () => {
      Units.useMetric = false;
      expect(Units.getAltitudeUnit()).toBe('ft');
    });
  });

  describe('getDistanceUnit', () => {
    test('returns m for metric', () => {
      Units.useMetric = true;
      expect(Units.getDistanceUnit()).toBe('m');
    });

    test('returns ft for imperial', () => {
      Units.useMetric = false;
      expect(Units.getDistanceUnit()).toBe('ft');
    });
  });

  describe('getDistanceUnitLong', () => {
    test('returns meters for metric', () => {
      Units.useMetric = true;
      expect(Units.getDistanceUnitLong()).toBe('meters');
    });

    test('returns feet for imperial', () => {
      Units.useMetric = false;
      expect(Units.getDistanceUnitLong()).toBe('feet');
    });
  });
});
