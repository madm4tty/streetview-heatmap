/**
 * Street View Heatmap - Configuration Page
 *
 * Manages application configuration with authentication.
 */

const ConfigPage = (function() {
    // ===== Default Configuration =====
    const DEFAULTS = {
        scheduler_enabled: true,
        interval_hours: 24,
        batch_size: 50,
        concurrency: 20,
        min_age_days: 90,
        overpass_delay: 2,
        samples_per_road: 5,
        adaptive_sampling: true
    };

    // ===== State =====
    let currentConfig = null;
    let isAuthenticated = false;

    // ===== Initialization =====

    /**
     * Initialize the configuration page
     */
    async function init() {
        setupModals();
        setupForm();
        updateAuthStatus();
        await loadConfig();
    }

    /**
     * Setup modal dialogs
     */
    function setupModals() {
        // API Key Modal
        setupModal('apikey-modal', () => {
            const input = document.getElementById('apikey-input');
            const key = input.value.trim();
            if (key) {
                api.setApiKey(key);
                isAuthenticated = true;
                updateAuthStatus();
                closeModal('apikey-modal');
                showNotification('Authenticated successfully', 'success');
            }
        });

        // Reset Confirmation Modal
        setupModal('reset-modal', async () => {
            await resetToDefaults();
            closeModal('reset-modal');
        });
    }

    /**
     * Setup a modal with open/close handlers
     */
    function setupModal(modalId, onConfirm) {
        const modal = document.getElementById(modalId);
        const backdrop = document.getElementById(`${modalId}-backdrop`);
        const closeBtn = document.getElementById(`${modalId}-close`);
        const cancelBtn = document.getElementById(`${modalId}-cancel`);
        const confirmBtn = document.getElementById(`${modalId}-confirm`);

        [backdrop, closeBtn, cancelBtn].forEach(el => {
            if (el) {
                el.addEventListener('click', () => closeModal(modalId));
            }
        });

        if (confirmBtn) {
            confirmBtn.addEventListener('click', onConfirm);
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.classList.contains('show')) {
                closeModal(modalId);
            }
        });
    }

    /**
     * Open a modal
     */
    function openModal(modalId) {
        const modal = document.getElementById(modalId);
        const backdrop = document.getElementById(`${modalId}-backdrop`);

        if (modal && backdrop) {
            backdrop.classList.add('show');
            modal.classList.add('show');

            const input = modal.querySelector('input');
            if (input) {
                setTimeout(() => input.focus(), 100);
            }
        }
    }

    /**
     * Close a modal
     */
    function closeModal(modalId) {
        const modal = document.getElementById(modalId);
        const backdrop = document.getElementById(`${modalId}-backdrop`);

        if (modal && backdrop) {
            backdrop.classList.remove('show');
            modal.classList.remove('show');
        }
    }

    /**
     * Setup form handlers
     */
    function setupForm() {
        const form = document.getElementById('config-form');
        const authBtn = document.getElementById('auth-btn');
        const resetBtn = document.getElementById('reset-btn');

        // Auth button
        if (authBtn) {
            authBtn.addEventListener('click', () => openModal('apikey-modal'));
        }

        // Reset button
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                if (!isAuthenticated) {
                    openModal('apikey-modal');
                    return;
                }
                openModal('reset-modal');
            });
        }

        // Form submission
        if (form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                await saveConfig();
            });

            // Track changes
            form.querySelectorAll('input, select').forEach(input => {
                input.addEventListener('change', updateSaveButton);
            });
        }
    }

    /**
     * Update authentication status display
     */
    function updateAuthStatus() {
        isAuthenticated = api.isAuthenticated();

        const authAlert = document.getElementById('auth-alert');
        const authStatus = document.getElementById('auth-status');
        const saveBtn = document.getElementById('save-btn');

        if (authAlert) {
            authAlert.style.display = isAuthenticated ? 'none' : 'block';
        }

        if (authStatus) {
            if (isAuthenticated) {
                authStatus.innerHTML = `
                    <span class="badge badge-success">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" style="margin-right: 4px;">
                            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                            <polyline points="22 4 12 14.01 9 11.01"/>
                        </svg>
                        Authenticated
                    </span>
                    <button class="btn btn-sm btn-secondary ml-sm" id="logout-btn">Logout</button>
                `;
                document.getElementById('logout-btn')?.addEventListener('click', () => {
                    api.clearApiKey();
                    isAuthenticated = false;
                    updateAuthStatus();
                    showNotification('Logged out', 'info');
                });
            } else {
                authStatus.innerHTML = `
                    <span class="badge badge-warning">Not Authenticated</span>
                `;
            }
        }

        if (saveBtn) {
            saveBtn.disabled = !isAuthenticated;
        }
    }

    // ===== Configuration Loading =====

    /**
     * Load configuration from API
     */
    async function loadConfig() {
        try {
            const config = await api.getConfig();
            currentConfig = config;
            populateForm(config);
        } catch (error) {
            console.error('Failed to load configuration:', error);
            showNotification('Failed to load configuration', 'error');
            // Use defaults
            populateForm(DEFAULTS);
        }
    }

    /**
     * Populate form with configuration values
     */
    function populateForm(config) {
        // Scheduler settings
        setFormValue('scheduler-enabled', config.scheduler?.enabled ?? DEFAULTS.scheduler_enabled);
        setFormValue('interval-hours', config.scheduler?.interval_hours ?? DEFAULTS.interval_hours);

        // Update settings
        setFormValue('batch-size', config.update?.batch_size ?? DEFAULTS.batch_size);
        setFormValue('concurrency', config.update?.concurrency ?? DEFAULTS.concurrency);
        setFormValue('min-age', config.update?.min_age_days ?? DEFAULTS.min_age_days);
        setFormValue('overpass-delay', config.update?.overpass_delay ?? DEFAULTS.overpass_delay);
        setFormValue('samples-per-road', config.update?.samples_per_road ?? DEFAULTS.samples_per_road);
        setFormValue('adaptive-sampling', config.update?.adaptive_sampling ?? DEFAULTS.adaptive_sampling);
    }

    /**
     * Set a form field value
     */
    function setFormValue(id, value) {
        const element = document.getElementById(id);
        if (!element) return;

        if (element.type === 'checkbox') {
            element.checked = value;
        } else {
            element.value = value;
        }
    }

    /**
     * Get a form field value
     */
    function getFormValue(id) {
        const element = document.getElementById(id);
        if (!element) return null;

        if (element.type === 'checkbox') {
            return element.checked;
        } else if (element.type === 'number') {
            return parseFloat(element.value);
        }
        return element.value;
    }

    /**
     * Update save button state
     */
    function updateSaveButton() {
        const saveBtn = document.getElementById('save-btn');
        if (saveBtn && isAuthenticated) {
            saveBtn.disabled = false;
        }
    }

    // ===== Configuration Saving =====

    /**
     * Save configuration
     */
    async function saveConfig() {
        if (!isAuthenticated) {
            openModal('apikey-modal');
            return;
        }

        const saveBtn = document.getElementById('save-btn');
        const saveStatus = document.getElementById('save-status');

        // Collect form values
        const updates = {
            scheduler: {
                enabled: getFormValue('scheduler-enabled'),
                interval_hours: getFormValue('interval-hours')
            },
            update: {
                batch_size: getFormValue('batch-size'),
                concurrency: getFormValue('concurrency'),
                min_age_days: getFormValue('min-age'),
                overpass_delay: getFormValue('overpass-delay'),
                samples_per_road: getFormValue('samples-per-road'),
                adaptive_sampling: getFormValue('adaptive-sampling')
            }
        };

        // Validate
        const errors = validateConfig(updates);
        if (errors.length > 0) {
            showNotification(errors.join('. '), 'error');
            return;
        }

        // Disable button during save
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.innerHTML = `
                <div class="loading-spinner" style="width: 18px; height: 18px; border-width: 2px;"></div>
                Saving...
            `;
        }

        try {
            await api.updateConfig(updates);

            currentConfig = { ...currentConfig, ...updates };

            showNotification('Configuration saved successfully', 'success');

            if (saveStatus) {
                saveStatus.innerHTML = `
                    <div class="alert alert-success">
                        Configuration saved successfully at ${formatTimestamp(new Date().toISOString())}
                    </div>
                `;
                saveStatus.style.display = 'block';
            }
        } catch (error) {
            console.error('Failed to save configuration:', error);

            if (error.isAuthError && error.isAuthError()) {
                api.clearApiKey();
                isAuthenticated = false;
                updateAuthStatus();
                showNotification('Authentication failed. Please re-enter your API key.', 'error');
                openModal('apikey-modal');
            } else {
                showNotification('Failed to save configuration: ' + error.message, 'error');

                if (saveStatus) {
                    saveStatus.innerHTML = `
                        <div class="alert alert-danger">
                            Failed to save: ${escapeHtml(error.message)}
                        </div>
                    `;
                    saveStatus.style.display = 'block';
                }
            }
        } finally {
            if (saveBtn) {
                saveBtn.disabled = !isAuthenticated;
                saveBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                        <polyline points="17 21 17 13 7 13 7 21"/>
                        <polyline points="7 3 7 8 15 8"/>
                    </svg>
                    Save Changes
                `;
            }
        }
    }

    /**
     * Validate configuration values
     */
    function validateConfig(config) {
        const errors = [];

        if (config.scheduler) {
            const hours = config.scheduler.interval_hours;
            if (hours < 1 || hours > 168) {
                errors.push('Update interval must be between 1 and 168 hours');
            }
        }

        if (config.update) {
            if (config.update.batch_size < 1 || config.update.batch_size > 500) {
                errors.push('Batch size must be between 1 and 500');
            }
            if (config.update.concurrency < 1 || config.update.concurrency > 50) {
                errors.push('Concurrency must be between 1 and 50');
            }
            if (config.update.min_age_days < 1 || config.update.min_age_days > 365) {
                errors.push('Minimum age must be between 1 and 365 days');
            }
            if (config.update.samples_per_road < 1 || config.update.samples_per_road > 20) {
                errors.push('Samples per road must be between 1 and 20');
            }
        }

        return errors;
    }

    /**
     * Reset configuration to defaults
     */
    async function resetToDefaults() {
        if (!isAuthenticated) {
            openModal('apikey-modal');
            return;
        }

        try {
            // Populate form with defaults
            populateForm(DEFAULTS);

            // Save defaults to server
            await api.updateConfig({
                scheduler: {
                    enabled: DEFAULTS.scheduler_enabled,
                    interval_hours: DEFAULTS.interval_hours
                },
                update: {
                    batch_size: DEFAULTS.batch_size,
                    concurrency: DEFAULTS.concurrency,
                    min_age_days: DEFAULTS.min_age_days,
                    overpass_delay: DEFAULTS.overpass_delay,
                    samples_per_road: DEFAULTS.samples_per_road,
                    adaptive_sampling: DEFAULTS.adaptive_sampling
                }
            });

            showNotification('Configuration reset to defaults', 'success');
        } catch (error) {
            console.error('Failed to reset configuration:', error);
            showNotification('Failed to reset configuration: ' + error.message, 'error');
        }
    }

    // ===== Public API =====
    return {
        init,
        reload: loadConfig
    };
})();

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ConfigPage;
}
