/**
 * Street View Heatmap - Interactive Map
 *
 * Provides an interactive Leaflet map for visualizing Street View coverage
 * across the UK with viewport-based tile loading.
 */

const HeatmapMap = (function() {
    // ===== Configuration =====
    const CONFIG = {
        // UK center coordinates
        center: [54.5, -3.5],
        initialZoom: 6,
        minZoom: 5,
        maxZoom: 18,

        // Tile size in degrees (matches backend)
        tileSize: 0.05,

        // Minimum zoom level to show road data
        minZoomForRoads: 10,

        // Minimum zoom level to show grid overlay
        minZoomForGrid: 12,

        // Debounce delay for tile loading
        loadDelay: 300,

        // Maximum tiles to load at once
        maxTilesPerLoad: 50,

        // UK bounding box (display bounds for map constraint)
        ukBounds: {
            north: 60.85,
            south: 49.85,
            east: 1.77,
            west: -8.18
        },

        // UK tile grid origin (must match backend geographic_scope.py UK_BOUNDS)
        tileOrigin: {
            minLon: -8.0,
            minLat: 49.9
        }
    };

    // ===== State =====
    let map = null;
    let roadLayer = null;
    let gridLayer = null;
    let loadedTiles = new Set();
    let loadingTiles = new Set();
    let failedTiles = new Set();
    let cities = [];

    // UI Elements
    let loadingIndicator = null;
    let searchInput = null;
    let searchResults = null;

    // ===== Initialization =====

    /**
     * Initialize the map
     */
    async function init() {
        try {
            // Create the map
            createMap();

            // Setup layers
            setupLayers();

            // Setup controls
            setupControls();

            // Setup search
            setupSearch();

            // Load cities for search
            await loadCities();

            // Initial tile load
            loadVisibleTiles();

            // Set initial zoom display
            updateZoomDisplay(map.getZoom());

            // Hide loading overlay
            hideLoading();
        } catch (error) {
            console.error('Failed to initialize map:', error);
            showNotification('Failed to initialize map: ' + error.message, 'error');
        }
    }

    /**
     * Create the Leaflet map
     */
    function createMap() {
        // preferCanvas draws all CircleMarkers onto a single <canvas>
        // element instead of creating thousands of individual SVG nodes.
        // This dramatically improves pan/zoom performance when displaying
        // large numbers of data points.
        map = L.map('map', {
            center: CONFIG.center,
            zoom: CONFIG.initialZoom,
            minZoom: CONFIG.minZoom,
            maxZoom: CONFIG.maxZoom,
            zoomControl: false,
            attributionControl: true,
            preferCanvas: true
        });

        // Add zoom control to bottom-right to avoid overlapping search box
        L.control.zoom({ position: 'bottomright' }).addTo(map);

        // Add OpenStreetMap tile layer
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Street View data &copy; Google',
            maxZoom: 19
        }).addTo(map);

        // Setup event handlers
        map.on('moveend', debounce(() => {
            loadVisibleTiles();
            const gridCheckbox = document.getElementById('layer-grid');
            if (gridCheckbox && gridCheckbox.checked) {
                loadVisibleGrid();
            }
        }, CONFIG.loadDelay));
        map.on('zoomend', handleZoomChange);
        map.on('mousemove', updateCoordinates);

        // Fit to UK bounds
        map.setMaxBounds([
            [CONFIG.ukBounds.south - 1, CONFIG.ukBounds.west - 1],
            [CONFIG.ukBounds.north + 1, CONFIG.ukBounds.east + 1]
        ]);
    }

    /**
     * Setup map layers
     */
    function setupLayers() {
        // Road coverage layer (GeoJSON)
        roadLayer = L.layerGroup().addTo(map);

        // Coverage grid layer
        gridLayer = L.layerGroup();
    }

    /**
     * Setup layer controls
     */
    function setupControls() {
        // Layer toggle
        const layerToggle = document.getElementById('layer-toggle');
        const layerControl = document.getElementById('layer-control');

        if (layerToggle && layerControl) {
            layerToggle.addEventListener('click', () => {
                layerControl.classList.toggle('collapsed');
                const expanded = !layerControl.classList.contains('collapsed');
                layerToggle.setAttribute('aria-expanded', expanded);
            });
        }

        // Road layer checkbox
        const roadsCheckbox = document.getElementById('layer-roads');
        if (roadsCheckbox) {
            roadsCheckbox.addEventListener('change', (e) => {
                if (e.target.checked) {
                    roadLayer.addTo(map);
                } else {
                    roadLayer.remove();
                }
            });
        }

        // Grid layer checkbox
        const gridCheckbox = document.getElementById('layer-grid');
        if (gridCheckbox) {
            gridCheckbox.addEventListener('change', (e) => {
                if (e.target.checked) {
                    gridLayer.addTo(map);
                    loadVisibleGrid();
                } else {
                    gridLayer.remove();
                }
                updateGridZoomHint();
            });
        }

        // Loading indicator
        loadingIndicator = document.getElementById('tile-loading');
    }

    // ===== Search =====

    /**
     * Setup search functionality
     */
    function setupSearch() {
        searchInput = document.getElementById('search-input');
        searchResults = document.getElementById('search-results');

        if (!searchInput || !searchResults) return;

        let selectedIndex = -1;

        searchInput.addEventListener('input', debounce((e) => {
            const query = e.target.value.trim();
            if (query.length < 2) {
                hideSearchResults();
                return;
            }
            performSearch(query);
        }, 200));

        searchInput.addEventListener('keydown', (e) => {
            const items = searchResults.querySelectorAll('.search-result-item');

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
                updateSelectedItem(items, selectedIndex);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedIndex = Math.max(selectedIndex - 1, 0);
                updateSelectedItem(items, selectedIndex);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (selectedIndex >= 0 && items[selectedIndex]) {
                    items[selectedIndex].click();
                }
            } else if (e.key === 'Escape') {
                hideSearchResults();
                searchInput.blur();
            }
        });

        searchInput.addEventListener('focus', () => {
            if (searchInput.value.trim().length >= 2) {
                performSearch(searchInput.value.trim());
            }
        });

        // Close on click outside
        document.addEventListener('click', (e) => {
            if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
                hideSearchResults();
            }
        });
    }

    /**
     * Load cities from API
     */
    async function loadCities() {
        try {
            const response = await api.getCities();
            cities = response.cities || [];
        } catch (error) {
            console.warn('Failed to load cities:', error);
            cities = [];
        }
    }

    /**
     * Perform search
     */
    function performSearch(query) {
        const lowerQuery = query.toLowerCase();

        // Search in cities
        const matches = cities.filter(city =>
            city.name.toLowerCase().includes(lowerQuery)
        ).slice(0, 10);

        displaySearchResults(matches, query);
    }

    /**
     * Display search results
     */
    function displaySearchResults(results, query) {
        if (results.length === 0) {
            searchResults.innerHTML = '<div class="search-no-results">No results found</div>';
            searchResults.classList.add('show');
            return;
        }

        searchResults.innerHTML = results.map((city, index) => `
            <div class="search-result-item" data-lat="${city.lat}" data-lon="${city.lon}" data-index="${index}" role="option">
                <div class="search-result-name">${escapeHtml(city.name)}</div>
                <div class="search-result-type">${city.priority} priority</div>
            </div>
        `).join('');

        // Add click handlers
        searchResults.querySelectorAll('.search-result-item').forEach(item => {
            item.addEventListener('click', () => {
                const lat = parseFloat(item.dataset.lat);
                const lon = parseFloat(item.dataset.lon);
                goToLocation(lat, lon);
                searchInput.value = item.querySelector('.search-result-name').textContent;
                hideSearchResults();
            });
        });

        searchResults.classList.add('show');
    }

    /**
     * Hide search results
     */
    function hideSearchResults() {
        searchResults.classList.remove('show');
    }

    /**
     * Update selected search item
     */
    function updateSelectedItem(items, index) {
        items.forEach((item, i) => {
            item.classList.toggle('active', i === index);
        });
    }

    /**
     * Navigate to a location
     */
    function goToLocation(lat, lon) {
        map.setView([lat, lon], 13);
    }

    // ===== Tile Loading =====

    /**
     * Load tiles visible in the current viewport
     */
    async function loadVisibleTiles() {
        const zoom = map.getZoom();

        // Only load road data at higher zoom levels
        if (zoom < CONFIG.minZoomForRoads) {
            return;
        }

        const bounds = map.getBounds();
        const visibleTileIds = getVisibleTileIds(bounds);

        // Filter out already loaded and currently loading tiles
        // Allow failed tiles to retry
        const tilesToLoad = visibleTileIds.filter(id =>
            !loadedTiles.has(id) && !loadingTiles.has(id)
        );

        if (tilesToLoad.length === 0) {
            return;
        }

        // Limit tiles to load
        const limitedTiles = tilesToLoad.slice(0, CONFIG.maxTilesPerLoad);

        // Show loading indicator
        showTileLoading();

        // Mark as loading
        limitedTiles.forEach(id => loadingTiles.add(id));

        // Load tiles in parallel — the points format reads from the
        // database cache so there is no external API rate limit concern.
        try {
            await Promise.all(limitedTiles.map(tileId => loadTileData(tileId)));
        } catch (error) {
            console.error('Error loading tiles:', error);
        }

        // Hide loading indicator
        hideTileLoading();
    }

    /**
     * Get tile IDs visible in the current bounds.
     * Generates IDs in the backend's "tile_{lonIdx}_{latIdx}" format
     * using the same grid origin and tile size as geographic_scope.py.
     */
    function getVisibleTileIds(bounds) {
        const tileIds = [];
        const north = Math.min(bounds.getNorth(), CONFIG.ukBounds.north);
        const south = Math.max(bounds.getSouth(), CONFIG.ukBounds.south);
        const east = Math.min(bounds.getEast(), CONFIG.ukBounds.east);
        const west = Math.max(bounds.getWest(), CONFIG.ukBounds.west);

        const { minLon, minLat } = CONFIG.tileOrigin;

        // Calculate index range for visible tiles
        const startLonIdx = Math.floor((west - minLon) / CONFIG.tileSize);
        const endLonIdx = Math.ceil((east - minLon) / CONFIG.tileSize);
        const startLatIdx = Math.floor((south - minLat) / CONFIG.tileSize);
        const endLatIdx = Math.ceil((north - minLat) / CONFIG.tileSize);

        for (let lonIdx = startLonIdx; lonIdx < endLonIdx; lonIdx++) {
            for (let latIdx = startLatIdx; latIdx < endLatIdx; latIdx++) {
                if (lonIdx >= 0 && latIdx >= 0) {
                    tileIds.push(`tile_${lonIdx}_${latIdx}`);
                }
            }
        }

        return tileIds;
    }

    /**
     * Load data for a single tile
     */
    async function loadTileData(tileId) {
        try {
            const geojson = await api.getTileData(tileId);
            addGeoJSONToMap(geojson, tileId);
            loadedTiles.add(tileId);
            failedTiles.delete(tileId);
        } catch (error) {
            if (error.isNotFound && error.isNotFound()) {
                // 404 = tile genuinely has no data, don't retry
                loadedTiles.add(tileId);
            } else {
                // Server/network error (e.g. Overpass 429/504) — allow retry
                console.warn(`Failed to load tile ${tileId}:`, error.message);
                failedTiles.add(tileId);
            }
        } finally {
            loadingTiles.delete(tileId);
        }
    }

    /**
     * Add GeoJSON data to the map.
     *
     * The default "points" format returns Point features from the database
     * cache. Each point represents a sampled Street View location with a
     * capture date. We render these as coloured CircleMarkers so coverage
     * age is visible at a glance. If LineString features arrive (road
     * format), they are styled as coloured road segments instead.
     */
    function addGeoJSONToMap(geojson, tileId) {
        if (!geojson || !geojson.features || geojson.features.length === 0) {
            return;
        }

        const layer = L.geoJSON(geojson, {
            // pointToLayer is required for Point geometry — Leaflet ignores
            // the "style" callback for Points and would show default markers.
            pointToLayer: pointToLayer,
            // style is used for LineString / Polygon features (road format)
            style: featureStyle,
            onEachFeature: onEachFeature
        });

        layer.addTo(roadLayer);
    }

    /**
     * Convert a Point feature to a coloured CircleMarker.
     * Leaflet calls this for every GeoJSON Point instead of creating
     * a default pin marker.
     */
    function pointToLayer(feature, latlng) {
        const date = feature.properties?.date || feature.properties?.capture_date;
        const color = ageToColor(date);

        return L.circleMarker(latlng, {
            radius: 4,
            fillColor: color,
            color: color,
            weight: 1,
            opacity: 0.9,
            fillOpacity: 0.8
        });
    }

    /**
     * Style function for LineString/Polygon GeoJSON features (road format)
     */
    function featureStyle(feature) {
        const date = feature.properties?.date || feature.properties?.capture_date;
        const color = ageToColor(date);

        return {
            color: color,
            weight: 4,
            opacity: 0.8,
            lineCap: 'round',
            lineJoin: 'round'
        };
    }

    /**
     * Bind events to each feature
     */
    function onEachFeature(feature, layer) {
        const props = feature.properties || {};
        const date = props.date || props.capture_date;
        const formattedDate = date ? formatTimestamp(date, { includeTime: false }) : 'Unknown';
        const category = getAgeCategory(date);
        const geomType = feature.geometry?.type;
        const name = props.name || 'Unknown road';

        // Extract a representative coordinate for the Street View link.
        // For LineString use the midpoint; for Point use the coordinate directly.
        let lat, lon;
        if (geomType === 'LineString') {
            const coords = feature.geometry.coordinates;
            const mid = coords[Math.floor(coords.length / 2)];
            lon = mid[0];
            lat = mid[1];
        } else {
            const coords = feature.geometry?.coordinates;
            lat = coords ? coords[1] : null;
            lon = coords ? coords[0] : null;
        }

        // Build Google Maps Street View URL.
        const streetViewUrl = (lat && lon)
            ? `https://www.google.com/maps/@${lat},${lon},3a,75y,0h,90t/data=!3m6!1e1!3m4!1s!2e0!7i16384!8i8192`
            : null;

        // For LineString (road) features, add a sticky tooltip on hover
        // showing road name and date. This is performant because there are
        // far fewer road segments than individual points.
        if (geomType === 'LineString') {
            layer.bindTooltip(
                `<div><b>${name}</b><br>${formattedDate}</div>`,
                { sticky: true, direction: 'top', opacity: 0.9 }
            );
        }

        const titleText = geomType === 'LineString' ? name : 'Street View Coverage';

        layer.bindPopup(`
            <div class="popup-content">
                <h4 class="popup-title">${titleText}</h4>
                <ul class="popup-info">
                    <li>
                        <span class="popup-label">Capture Date</span>
                        <span class="popup-value">${formattedDate}</span>
                    </li>
                    <li>
                        <span class="popup-label">Age</span>
                        <span class="popup-value">
                            <span class="popup-age">
                                <span class="popup-age-dot" style="background: ${ageToColor(date)}"></span>
                                ${category}
                            </span>
                        </span>
                    </li>
                    ${lat && lon ? `
                    <li>
                        <span class="popup-label">Coordinates</span>
                        <span class="popup-value">${lat.toFixed(5)}, ${lon.toFixed(5)}</span>
                    </li>
                    ` : ''}
                </ul>
                ${streetViewUrl ? `
                <a href="${streetViewUrl}" target="_blank" rel="noopener noreferrer"
                   class="popup-streetview-link">
                    Open in Google Street View &rarr;
                </a>
                ` : ''}
            </div>
        `);
    }

    // ===== Coverage Grid =====

    /**
     * Load visible grid tiles
     */
    async function loadVisibleGrid() {
        const zoom = map.getZoom();

        // Don't load grid at low zoom levels — too many tiles cause lag
        if (zoom < CONFIG.minZoomForGrid) {
            gridLayer.clearLayers();
            return;
        }

        const bounds = map.getBounds();

        try {
            const response = await api.getTilesInBounds({
                north: bounds.getNorth(),
                south: bounds.getSouth(),
                east: bounds.getEast(),
                west: bounds.getWest()
            });

            // Clear existing grid
            gridLayer.clearLayers();

            // Add grid rectangles
            (response.tiles || []).forEach(tile => {
                const tileBounds = [
                    [tile.lat, tile.lon],
                    [tile.lat + CONFIG.tileSize, tile.lon + CONFIG.tileSize]
                ];

                const hasData = tile.location_count > 0;

                const rect = L.rectangle(tileBounds, {
                    color: hasData ? '#2563eb' : '#64748b',
                    weight: 2,
                    fillOpacity: 0.1,
                    dashArray: hasData ? null : '5, 5',
                    className: hasData ? 'coverage-tile has-data' : 'coverage-tile no-data'
                });

                rect.bindPopup(`
                    <div class="tile-popup">
                        <h4 class="popup-title">Tile ${tile.tile_id}</h4>
                        <ul class="popup-info">
                            <li>
                                <span class="popup-label">Locations</span>
                                <span class="popup-value">${formatNumber(tile.location_count || 0)}</span>
                            </li>
                            <li>
                                <span class="popup-label">With Dates</span>
                                <span class="popup-value">${formatNumber(tile.with_dates || 0)}</span>
                            </li>
                            <li>
                                <span class="popup-label">Last Updated</span>
                                <span class="popup-value">${tile.last_updated ? formatTimestamp(tile.last_updated, { relative: true }) : 'Never'}</span>
                            </li>
                            <li>
                                <span class="popup-label">Priority</span>
                                <span class="popup-value">${tile.priority || 'Unknown'}</span>
                            </li>
                        </ul>
                    </div>
                `);

                rect.addTo(gridLayer);
            });
        } catch (error) {
            console.error('Failed to load grid:', error);
            showNotification('Failed to load coverage grid', 'error');
        }
    }

    // ===== Event Handlers =====

    /**
     * Handle zoom change
     */
    function handleZoomChange() {
        const zoom = map.getZoom();

        // Update zoom level display
        updateZoomDisplay(zoom);

        // Reload grid if visible
        const gridCheckbox = document.getElementById('layer-grid');
        if (gridCheckbox && gridCheckbox.checked) {
            loadVisibleGrid();
        }

        updateGridZoomHint();
    }

    /**
     * Show or hide the "zoom to level 12+" hint for the coverage grid
     */
    function updateGridZoomHint() {
        const hint = document.getElementById('grid-zoom-hint');
        const gridCheckbox = document.getElementById('layer-grid');
        if (!hint || !gridCheckbox) return;

        const belowThreshold = map.getZoom() < CONFIG.minZoomForGrid;
        hint.classList.toggle('visible', gridCheckbox.checked && belowThreshold);
    }

    function updateZoomDisplay(zoom) {
        const coordZoom = document.getElementById('coord-zoom');
        if (coordZoom) {
            coordZoom.textContent = zoom;
        }
    }

    /**
     * Update coordinate display
     */
    function updateCoordinates(e) {
        const coordLat = document.getElementById('coord-lat');
        const coordLon = document.getElementById('coord-lon');

        if (coordLat && coordLon) {
            coordLat.textContent = e.latlng.lat.toFixed(5);
            coordLon.textContent = e.latlng.lng.toFixed(5);
        }
    }

    // ===== UI Helpers =====

    /**
     * Show tile loading indicator
     */
    function showTileLoading() {
        if (loadingIndicator) {
            loadingIndicator.classList.add('show');
        }
    }

    /**
     * Hide tile loading indicator
     */
    function hideTileLoading() {
        if (loadingIndicator) {
            loadingIndicator.classList.remove('show');
        }
    }

    /**
     * Hide initial loading overlay
     */
    function hideLoading() {
        const loadingOverlay = document.getElementById('map-loading');
        if (loadingOverlay) {
            loadingOverlay.classList.add('hidden');
        }
    }

    // ===== Public API =====
    return {
        init,
        goToLocation,
        getMap: () => map
    };
})();

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = HeatmapMap;
}
