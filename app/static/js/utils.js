/**
 * Street View Heatmap Utilities
 *
 * Shared utility functions used across the application.
 */

// ===== Debounce & Throttle =====

/**
 * Debounce a function - only execute after wait period with no new calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func.apply(this, args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Throttle a function - execute at most once per wait period
 * @param {Function} func - Function to throttle
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Throttled function
 */
function throttle(func, wait) {
    let lastTime = 0;
    return function executedFunction(...args) {
        const now = Date.now();
        if (now - lastTime >= wait) {
            lastTime = now;
            func.apply(this, args);
        }
    };
}

// ===== Date & Time Formatting =====

/**
 * Format an ISO timestamp to a human-readable string
 * @param {string} isoString - ISO 8601 timestamp
 * @param {Object} options - Formatting options
 * @param {boolean} options.includeTime - Include time (default: true)
 * @param {boolean} options.relative - Use relative time if recent (default: false)
 * @returns {string} Formatted date string
 */
function formatTimestamp(isoString, options = {}) {
    if (!isoString) return 'Never';

    const { includeTime = true, relative = false } = options;
    const date = new Date(isoString);

    if (isNaN(date.getTime())) return 'Invalid date';

    // Use relative time for recent dates if requested
    if (relative) {
        const now = new Date();
        const diffMs = now - date;
        const diffSeconds = Math.floor(diffMs / 1000);

        if (diffSeconds < 60) return 'Just now';
        if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m ago`;
        if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}h ago`;
        if (diffSeconds < 604800) return `${Math.floor(diffSeconds / 86400)}d ago`;
    }

    const dateOptions = {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    };

    if (includeTime) {
        dateOptions.hour = '2-digit';
        dateOptions.minute = '2-digit';
    }

    return date.toLocaleDateString('en-GB', dateOptions);
}

/**
 * Format a duration in seconds to human-readable string
 * @param {number} seconds - Duration in seconds
 * @param {boolean} short - Use short format (default: false)
 * @returns {string} Formatted duration
 */
function formatDuration(seconds, short = false) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) {
        return short ? '0s' : '0 seconds';
    }

    seconds = Math.floor(seconds);

    if (seconds < 60) {
        return short ? `${seconds}s` : `${seconds} second${seconds !== 1 ? 's' : ''}`;
    }

    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;

    if (minutes < 60) {
        if (short) {
            return remainingSeconds > 0 ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
        }
        const minStr = `${minutes} minute${minutes !== 1 ? 's' : ''}`;
        if (remainingSeconds > 0) {
            return `${minStr} ${remainingSeconds} second${remainingSeconds !== 1 ? 's' : ''}`;
        }
        return minStr;
    }

    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;

    if (short) {
        return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
    }

    const hourStr = `${hours} hour${hours !== 1 ? 's' : ''}`;
    if (remainingMinutes > 0) {
        return `${hourStr} ${remainingMinutes} minute${remainingMinutes !== 1 ? 's' : ''}`;
    }
    return hourStr;
}

/**
 * Calculate age of a date in months
 * @param {string} dateString - Date string (YYYY-MM format or ISO)
 * @returns {number} Age in months
 */
function calculateAgeMonths(dateString) {
    if (!dateString) return Infinity;

    const date = new Date(dateString);
    if (isNaN(date.getTime())) return Infinity;

    const now = new Date();
    const months = (now.getFullYear() - date.getFullYear()) * 12 +
        (now.getMonth() - date.getMonth());

    return Math.max(0, months);
}

// ===== Age-Based Colors =====

/**
 * Age thresholds in months for color coding
 */
const AGE_THRESHOLDS = {
    FRESH: 6,      // < 6 months = bright green
    RECENT: 12,    // < 1 year = lime/yellow-green
    AGING: 36,     // < 3 years = yellow
    OLD: 60,       // < 5 years = orange
    STALE: 120     // < 10 years = red, >= 10 years = deep red
};

/**
 * Color scheme for age-based visualization
 */
const AGE_COLORS = {
    FRESH: '#22c55e',   // Bright green
    RECENT: '#84cc16',  // Lime/yellow-green
    AGING: '#eab308',   // Yellow
    OLD: '#f97316',     // Orange
    STALE: '#ef4444',   // Red
    VERY_STALE: '#b91c1c', // Deep red/maroon
    UNKNOWN: '#6b7280'  // Gray
};

/**
 * Get color based on Street View capture date
 * @param {string} dateString - Capture date string
 * @returns {string} Hex color code
 */
function ageToColor(dateString) {
    if (!dateString) return AGE_COLORS.UNKNOWN;

    const ageMonths = calculateAgeMonths(dateString);

    if (ageMonths < AGE_THRESHOLDS.FRESH) return AGE_COLORS.FRESH;
    if (ageMonths < AGE_THRESHOLDS.RECENT) return AGE_COLORS.RECENT;
    if (ageMonths < AGE_THRESHOLDS.AGING) return AGE_COLORS.AGING;
    if (ageMonths < AGE_THRESHOLDS.OLD) return AGE_COLORS.OLD;
    if (ageMonths < AGE_THRESHOLDS.STALE) return AGE_COLORS.STALE;
    return AGE_COLORS.VERY_STALE;
}

/**
 * Get age category label
 * @param {string} dateString - Capture date string
 * @returns {string} Age category label
 */
function getAgeCategory(dateString) {
    if (!dateString) return 'Unknown';

    const ageMonths = calculateAgeMonths(dateString);

    if (ageMonths < AGE_THRESHOLDS.FRESH) return 'Fresh (<6 months)';
    if (ageMonths < AGE_THRESHOLDS.RECENT) return 'Recent (<1 year)';
    if (ageMonths < AGE_THRESHOLDS.AGING) return 'Aging (<3 years)';
    if (ageMonths < AGE_THRESHOLDS.OLD) return 'Old (<5 years)';
    if (ageMonths < AGE_THRESHOLDS.STALE) return 'Stale (<10 years)';
    return 'Very stale (10+ years)';
}

// ===== Number Formatting =====

/**
 * Format a large number with thousands separators
 * @param {number} num - Number to format
 * @returns {string} Formatted number (e.g., "1,234,567")
 */
function formatNumber(num) {
    if (num === null || num === undefined) return '0';
    return num.toLocaleString('en-GB');
}

/**
 * Format a number as a percentage
 * @param {number} value - Value
 * @param {number} total - Total
 * @param {number} decimals - Decimal places (default: 1)
 * @returns {string} Formatted percentage
 */
function formatPercentage(value, total, decimals = 1) {
    if (!total || total === 0) return '0%';
    const percentage = (value / total) * 100;
    return `${percentage.toFixed(decimals)}%`;
}

/**
 * Format bytes to human-readable size
 * @param {number} bytes - Size in bytes
 * @returns {string} Formatted size (e.g., "1.5 MB")
 */
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';

    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// ===== Notifications =====

/**
 * Toast notification container
 */
let toastContainer = null;

/**
 * Initialize toast container
 */
function initToastContainer() {
    if (toastContainer) return;

    toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    toastContainer.className = 'toast-container';
    document.body.appendChild(toastContainer);
}

/**
 * Show a toast notification
 * @param {string} message - Notification message
 * @param {string} type - Type: 'info', 'success', 'warning', 'error' (default: 'info')
 * @param {number} duration - Duration in milliseconds (default: 5000)
 */
function showNotification(message, type = 'info', duration = 5000) {
    initToastContainer();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icon = getToastIcon(type);
    toast.innerHTML = `
        <span class="toast-icon">${icon}</span>
        <span class="toast-message">${escapeHtml(message)}</span>
        <button class="toast-close" aria-label="Close">&times;</button>
    `;

    // Add close handler
    toast.querySelector('.toast-close').addEventListener('click', () => {
        removeToast(toast);
    });

    toastContainer.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => {
        toast.classList.add('toast-visible');
    });

    // Auto-remove after duration
    if (duration > 0) {
        setTimeout(() => removeToast(toast), duration);
    }
}

/**
 * Remove a toast notification
 * @param {HTMLElement} toast - Toast element to remove
 */
function removeToast(toast) {
    toast.classList.remove('toast-visible');
    setTimeout(() => toast.remove(), 300);
}

/**
 * Get icon for toast type
 * @param {string} type - Toast type
 * @returns {string} Icon HTML
 */
function getToastIcon(type) {
    switch (type) {
        case 'success': return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
        case 'warning': return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
        case 'error': return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
        default: return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
    }
}

// ===== DOM Utilities =====

/**
 * Escape HTML to prevent XSS
 * @param {string} text - Text to escape
 * @returns {string} Escaped HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Create an element with attributes and content
 * @param {string} tag - Tag name
 * @param {Object} attrs - Attributes object
 * @param {string|HTMLElement|Array} content - Content (text, element, or array)
 * @returns {HTMLElement} Created element
 */
function createElement(tag, attrs = {}, content = null) {
    const el = document.createElement(tag);

    for (const [key, value] of Object.entries(attrs)) {
        if (key === 'className') {
            el.className = value;
        } else if (key === 'dataset') {
            Object.assign(el.dataset, value);
        } else if (key.startsWith('on') && typeof value === 'function') {
            el.addEventListener(key.slice(2).toLowerCase(), value);
        } else {
            el.setAttribute(key, value);
        }
    }

    if (content !== null) {
        if (typeof content === 'string') {
            el.textContent = content;
        } else if (Array.isArray(content)) {
            content.forEach(child => {
                if (typeof child === 'string') {
                    el.appendChild(document.createTextNode(child));
                } else if (child instanceof HTMLElement) {
                    el.appendChild(child);
                }
            });
        } else if (content instanceof HTMLElement) {
            el.appendChild(content);
        }
    }

    return el;
}

/**
 * Show a loading spinner in a container
 * @param {HTMLElement} container - Container element
 * @param {string} message - Loading message (optional)
 */
function showLoading(container, message = 'Loading...') {
    container.innerHTML = `
        <div class="loading-container">
            <div class="loading-spinner"></div>
            <p class="loading-message">${escapeHtml(message)}</p>
        </div>
    `;
}

/**
 * Show an error message in a container
 * @param {HTMLElement} container - Container element
 * @param {string} message - Error message
 * @param {Function} retryFn - Retry function (optional)
 */
function showError(container, message, retryFn = null) {
    const retryBtn = retryFn ?
        `<button class="btn btn-secondary retry-btn">Try Again</button>` : '';

    container.innerHTML = `
        <div class="error-container">
            <div class="error-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="15" y1="9" x2="9" y2="15"/>
                    <line x1="9" y1="9" x2="15" y2="15"/>
                </svg>
            </div>
            <p class="error-message">${escapeHtml(message)}</p>
            ${retryBtn}
        </div>
    `;

    if (retryFn) {
        container.querySelector('.retry-btn').addEventListener('click', retryFn);
    }
}

// ===== Tile Utilities =====

/**
 * Parse tile ID to coordinates
 * @param {string} tileId - Tile ID (format: "lat_lon")
 * @returns {Object|null} { lat, lon } or null if invalid
 */
function parseTileId(tileId) {
    if (!tileId) return null;

    const parts = tileId.split('_');
    if (parts.length !== 2) return null;

    const lat = parseFloat(parts[0]);
    const lon = parseFloat(parts[1]);

    if (isNaN(lat) || isNaN(lon)) return null;

    return { lat, lon };
}

/**
 * Create tile ID from coordinates
 * @param {number} lat - Latitude
 * @param {number} lon - Longitude
 * @param {number} tileSize - Tile size in degrees (default: 0.05)
 * @returns {string} Tile ID
 */
function createTileId(lat, lon, tileSize = 0.05) {
    const tileLat = Math.floor(lat / tileSize) * tileSize;
    const tileLon = Math.floor(lon / tileSize) * tileSize;
    return `${tileLat.toFixed(2)}_${tileLon.toFixed(2)}`;
}

/**
 * Get tile bounds from tile ID
 * @param {string} tileId - Tile ID
 * @param {number} tileSize - Tile size in degrees (default: 0.05)
 * @returns {Object|null} Bounds { south, north, west, east }
 */
function getTileBounds(tileId, tileSize = 0.05) {
    const coords = parseTileId(tileId);
    if (!coords) return null;

    return {
        south: coords.lat,
        north: coords.lat + tileSize,
        west: coords.lon,
        east: coords.lon + tileSize
    };
}

// ===== Local Storage Utilities =====

/**
 * Safely get item from localStorage
 * @param {string} key - Storage key
 * @param {*} defaultValue - Default value if not found
 * @returns {*} Stored value or default
 */
function getStorageItem(key, defaultValue = null) {
    try {
        const item = localStorage.getItem(key);
        return item ? JSON.parse(item) : defaultValue;
    } catch (e) {
        console.warn(`Failed to get storage item "${key}":`, e);
        return defaultValue;
    }
}

/**
 * Safely set item in localStorage
 * @param {string} key - Storage key
 * @param {*} value - Value to store
 * @returns {boolean} Success
 */
function setStorageItem(key, value) {
    try {
        localStorage.setItem(key, JSON.stringify(value));
        return true;
    } catch (e) {
        console.warn(`Failed to set storage item "${key}":`, e);
        return false;
    }
}

// ===== URL Utilities =====

/**
 * Get URL query parameters as object
 * @returns {Object} Query parameters
 */
function getQueryParams() {
    const params = {};
    const searchParams = new URLSearchParams(window.location.search);
    for (const [key, value] of searchParams) {
        params[key] = value;
    }
    return params;
}

/**
 * Update URL query parameter without page reload
 * @param {string} key - Parameter key
 * @param {string} value - Parameter value (null to remove)
 */
function updateQueryParam(key, value) {
    const url = new URL(window.location);
    if (value === null || value === undefined || value === '') {
        url.searchParams.delete(key);
    } else {
        url.searchParams.set(key, value);
    }
    window.history.replaceState({}, '', url);
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        debounce,
        throttle,
        formatTimestamp,
        formatDuration,
        calculateAgeMonths,
        AGE_THRESHOLDS,
        AGE_COLORS,
        ageToColor,
        getAgeCategory,
        formatNumber,
        formatPercentage,
        formatBytes,
        showNotification,
        escapeHtml,
        createElement,
        showLoading,
        showError,
        parseTileId,
        createTileId,
        getTileBounds,
        getStorageItem,
        setStorageItem,
        getQueryParams,
        updateQueryParam
    };
}
