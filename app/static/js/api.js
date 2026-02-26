/**
 * Street View Heatmap API Client
 *
 * Provides a clean interface for interacting with the backend REST API.
 * Handles authentication, error handling, and request formatting.
 */

class HeatmapAPI {
    /**
     * Initialize the API client
     * @param {string} baseUrl - Base URL for API endpoints (default: '/api')
     */
    constructor(baseUrl = '/api') {
        this.baseUrl = baseUrl;
        this.apiKey = sessionStorage.getItem('heatmap_api_key') || null;
    }

    // ===== Authentication =====

    /**
     * Set the API key for authenticated requests
     * @param {string} key - The API key
     */
    setApiKey(key) {
        this.apiKey = key;
        if (key) {
            sessionStorage.setItem('heatmap_api_key', key);
        } else {
            sessionStorage.removeItem('heatmap_api_key');
        }
    }

    /**
     * Get the current API key
     * @returns {string|null} The current API key
     */
    getApiKey() {
        return this.apiKey;
    }

    /**
     * Check if an API key is set
     * @returns {boolean} True if API key is set
     */
    isAuthenticated() {
        return !!this.apiKey;
    }

    /**
     * Clear the API key
     */
    clearApiKey() {
        this.apiKey = null;
        sessionStorage.removeItem('heatmap_api_key');
    }

    // ===== Health & Status =====

    /**
     * Check API health
     * @returns {Promise<Object>} Health status
     */
    async getHealth() {
        return this._fetch(`${this.baseUrl}/health`);
    }

    /**
     * Get comprehensive system status
     * @returns {Promise<Object>} System status including coverage stats and current job
     */
    async getStatus() {
        return this._fetch(`${this.baseUrl}/status`);
    }

    // ===== Tiles =====

    /**
     * Get list of tiles with optional filtering
     * @param {Object} params - Query parameters
     * @param {string} params.priority - Filter by priority (high/medium/low)
     * @param {boolean} params.has_data - Filter by data presence
     * @param {number} params.min_lat - Minimum latitude
     * @param {number} params.max_lat - Maximum latitude
     * @param {number} params.min_lon - Minimum longitude
     * @param {number} params.max_lon - Maximum longitude
     * @param {number} params.page - Page number (default: 1)
     * @param {number} params.per_page - Items per page (default: 100)
     * @returns {Promise<Object>} Tiles list with pagination
     */
    async getTiles(params = {}) {
        const queryString = this._buildQueryString(params);
        return this._fetch(`${this.baseUrl}/tiles${queryString}`);
    }

    /**
     * Get tiles within a bounding box (convenience method)
     * @param {Object} bounds - Bounding box
     * @param {number} bounds.north - North latitude
     * @param {number} bounds.south - South latitude
     * @param {number} bounds.east - East longitude
     * @param {number} bounds.west - West longitude
     * @returns {Promise<Object>} Tiles within bounds
     */
    async getTilesInBounds(bounds) {
        return this.getTiles({
            min_lat: bounds.south,
            max_lat: bounds.north,
            min_lon: bounds.west,
            max_lon: bounds.east,
            per_page: 500  // Get more tiles for map view
        });
    }

    /**
     * Get GeoJSON data for a specific tile
     * @param {string} tileId - The tile ID (format: "lat_lon")
     * @returns {Promise<Object>} GeoJSON FeatureCollection
     */
    async getTileData(tileId) {
        return this._fetch(`${this.baseUrl}/tiles/${encodeURIComponent(tileId)}/data?format=roads`);
    }

    /**
     * Get lightweight tile summaries for a bounding box (low-zoom overview)
     * @param {Object} bounds - Bounding box {north, south, east, west}
     * @param {number} [resolution] - Optional grid cell size in degrees for sub-tile aggregation
     * @returns {Promise<Object>} Tile summaries with segment counts and latest dates
     */
    async getTileSummary(bounds, resolution) {
        const params = {
            north: bounds.north,
            south: bounds.south,
            east: bounds.east,
            west: bounds.west
        };
        if (resolution !== undefined) {
            params.resolution = resolution;
        }
        const queryString = this._buildQueryString(params);
        return this._fetch(`${this.baseUrl}/tiles/summary${queryString}`);
    }

    // ===== Jobs & Updates =====

    /**
     * Trigger a manual update job
     * @param {Object} options - Update options
     * @param {string} options.priority_filter - Priority filter (all/high/medium/low)
     * @param {number} options.tile_limit - Maximum tiles to process
     * @returns {Promise<Object>} Job information
     */
    async triggerUpdate(options = {}) {
        return this._fetch(`${this.baseUrl}/update/trigger`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(options)
        });
    }

    /**
     * Get current job status
     * @returns {Promise<Object>} Current job status
     */
    async getJobStatus() {
        return this._fetch(`${this.baseUrl}/update/status`);
    }

    /**
     * Cancel the current running job
     * @returns {Promise<Object>} Cancellation result
     */
    async cancelJob() {
        return this._fetch(`${this.baseUrl}/update/cancel`, {
            method: 'POST'
        });
    }

    // ===== Configuration =====

    /**
     * Get current configuration
     * @returns {Promise<Object>} Configuration object
     */
    async getConfig() {
        return this._fetch(`${this.baseUrl}/config`);
    }

    /**
     * Update configuration
     * @param {Object} updates - Configuration updates
     * @returns {Promise<Object>} Updated configuration
     */
    async updateConfig(updates) {
        return this._fetch(`${this.baseUrl}/config`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(updates)
        });
    }

    // ===== Cities =====

    /**
     * Get list of UK cities with priorities
     * @returns {Promise<Object>} Cities list
     */
    async getCities() {
        return this._fetch(`${this.baseUrl}/cities`);
    }

    // ===== Private Methods =====

    /**
     * Build query string from parameters object
     * @private
     * @param {Object} params - Parameters to encode
     * @returns {string} Query string (including '?') or empty string
     */
    _buildQueryString(params) {
        const filtered = Object.entries(params)
            .filter(([_, value]) => value !== undefined && value !== null && value !== '')
            .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`);

        return filtered.length > 0 ? `?${filtered.join('&')}` : '';
    }

    /**
     * Make a fetch request with error handling and authentication
     * @private
     * @param {string} url - URL to fetch
     * @param {Object} options - Fetch options
     * @returns {Promise<Object>} Response JSON
     * @throws {APIError} On request failure
     */
    async _fetch(url, options = {}) {
        // Add API key header for POST/PUT/DELETE requests
        const needsAuth = ['POST', 'PUT', 'DELETE'].includes(options.method?.toUpperCase());

        if (needsAuth && this.apiKey) {
            options.headers = {
                ...options.headers,
                'X-API-Key': this.apiKey
            };
        }

        try {
            const response = await fetch(url, options);

            // Parse response body
            let data;
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                data = await response.json();
            } else {
                data = await response.text();
            }

            // Handle errors
            if (!response.ok) {
                const error = new APIError(
                    data.error || data.message || `HTTP ${response.status}: ${response.statusText}`,
                    response.status,
                    data
                );
                throw error;
            }

            return data;
        } catch (error) {
            // Re-throw APIError as-is
            if (error instanceof APIError) {
                throw error;
            }

            // Wrap network errors
            throw new APIError(
                error.message || 'Network error',
                0,
                { originalError: error.name }
            );
        }
    }
}

/**
 * Custom error class for API errors
 */
class APIError extends Error {
    /**
     * Create an API error
     * @param {string} message - Error message
     * @param {number} status - HTTP status code (0 for network errors)
     * @param {Object} data - Response data
     */
    constructor(message, status, data = {}) {
        super(message);
        this.name = 'APIError';
        this.status = status;
        this.data = data;
    }

    /**
     * Check if error is an authentication error
     * @returns {boolean}
     */
    isAuthError() {
        return this.status === 401 || this.status === 403;
    }

    /**
     * Check if error is a network error
     * @returns {boolean}
     */
    isNetworkError() {
        return this.status === 0;
    }

    /**
     * Check if error is a not found error
     * @returns {boolean}
     */
    isNotFound() {
        return this.status === 404;
    }

    /**
     * Check if error is a server error
     * @returns {boolean}
     */
    isServerError() {
        return this.status >= 500;
    }
}

// Create a singleton instance
const api = new HeatmapAPI();

// Export for module usage (if needed)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { HeatmapAPI, APIError, api };
}
