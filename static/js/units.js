/**
 * Units utility module for metric/imperial conversions
 * Underlying data remains in meters, display is converted based on config
 */

/* exported Units */
const Units = {
    // Configuration
    useMetric: true,

    /**
     * Initialize units from config
     * @param {Object} config - The config object from /api/config
     */
    init(config) {
        this.useMetric = config.use_metric !== false; // default to true
    },

    /**
     * Format a distance value for display
     * @param {number} meters - Distance in meters
     * @param {boolean} includeUnit - Whether to include the unit suffix
     * @returns {string} Formatted distance with unit
     */
    formatDistance(meters, includeUnit = true) {
        if (meters === null || meters === undefined || isNaN(meters)) {
            return 'N/A';
        }

        if (this.useMetric) {
            if (meters >= 1000) {
                const km = (meters / 1000).toFixed(2);
                return includeUnit ? `${km} km` : km;
            } else {
                const m = meters.toFixed(0);
                return includeUnit ? `${m} m` : m;
            }
        } else {
            // Imperial: convert to feet
            const feet = meters * 3.28084;
            if (feet >= 5280) {
                const miles = (feet / 5280).toFixed(2);
                return includeUnit ? `${miles} mi` : miles;
            } else {
                const ft = feet.toFixed(0);
                return includeUnit ? `${ft} ft` : ft;
            }
        }
    },

    /**
     * Format an altitude/height value for display
     * @param {number} meters - Altitude in meters
     * @param {boolean} includeUnit - Whether to include the unit suffix
     * @param {number} decimals - Number of decimal places (default: 0)
     * @returns {string} Formatted altitude with unit
     */
    formatAltitude(meters, includeUnit = true, decimals = 0) {
        if (meters === null || meters === undefined || isNaN(meters)) {
            return 'N/A';
        }

        if (this.useMetric) {
            const m = meters.toFixed(decimals);
            return includeUnit ? `${m}m` : m;
        } else {
            // Imperial: convert to feet
            const feet = meters * 3.28084;
            const ft = feet.toFixed(decimals);
            return includeUnit ? `${ft}ft` : ft;
        }
    },

    /**
     * Format a speed value for display (m/s to km/h or mph)
     * @param {number} ms - Speed in meters per second
     * @param {boolean} includeUnit - Whether to include the unit suffix
     * @returns {string} Formatted speed with unit
     */
    formatSpeed(ms, includeUnit = true) {
        if (ms === null || ms === undefined || isNaN(ms)) {
            return 'N/A';
        }

        if (this.useMetric) {
            const kmh = (ms * 3.6).toFixed(1);
            return includeUnit ? `${kmh} km/h` : kmh;
        } else {
            // Imperial: convert to mph
            const mph = (ms * 2.23694).toFixed(1);
            return includeUnit ? `${mph} mph` : mph;
        }
    },

    /**
     * Get the current unit system name
     * @returns {string} "metric" or "imperial"
     */
    getSystem() {
        return this.useMetric ? 'metric' : 'imperial';
    },

    /**
     * Get altitude unit suffix
     * @returns {string} "m" or "ft"
     */
    getAltitudeUnit() {
        return this.useMetric ? 'm' : 'ft';
    },

    /**
     * Get distance unit suffix (short)
     * @returns {string} "m" or "ft"
     */
    getDistanceUnit() {
        return this.useMetric ? 'm' : 'ft';
    },

    /**
     * Get distance unit suffix (long)
     * @returns {string} "meters" or "feet"
     */
    getDistanceUnitLong() {
        return this.useMetric ? 'meters' : 'feet';
    }
};
