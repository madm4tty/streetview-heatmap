/**
 * Street View Heatmap - Dashboard
 *
 * Displays system status, coverage progress, and recent activity
 * with auto-refresh capabilities.
 */

const Dashboard = (function() {
    // ===== Configuration =====
    const CONFIG = {
        refreshIntervalIdle: 30000,    // 30 seconds when idle
        refreshIntervalRunning: 5000,  // 5 seconds when job running
    };

    // ===== State =====
    let refreshInterval = null;
    let isJobRunning = false;
    let pendingAction = null;

    // ===== Initialization =====

    /**
     * Initialize the dashboard
     */
    function init() {
        setupModals();
        loadDashboardData();
        startAutoRefresh();
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
                showNotification('API key saved', 'success');
                closeModal('apikey-modal');

                // Execute pending action if any
                if (pendingAction) {
                    pendingAction();
                    pendingAction = null;
                }
            }
        });

        // Update Modal
        setupModal('update-modal', async () => {
            await triggerUpdate();
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

        // Close handlers
        [backdrop, closeBtn, cancelBtn].forEach(el => {
            if (el) {
                el.addEventListener('click', () => closeModal(modalId));
            }
        });

        // Confirm handler
        if (confirmBtn) {
            confirmBtn.addEventListener('click', onConfirm);
        }

        // Close on escape
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

            // Focus first input
            const input = modal.querySelector('input, select');
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

    // ===== Data Loading =====

    /**
     * Load all dashboard data
     */
    async function loadDashboardData() {
        try {
            const status = await api.getStatus();
            updateSystemStatus(status);
            updateCurrentJob(status.current_job);
            updateCoverageStats(status.coverage);
            updateDbStats(status);
            updateRecentActivity(status.last_job);

            // Update refresh rate based on job status
            isJobRunning = status.current_job && status.current_job.status === 'running';
            updateRefreshRate();

            // Show live indicator
            const liveIndicator = document.getElementById('live-indicator');
            if (liveIndicator) {
                liveIndicator.style.display = isJobRunning ? 'flex' : 'none';
            }
        } catch (error) {
            console.error('Failed to load dashboard data:', error);
            showNotification('Failed to load dashboard data', 'error');
        }
    }

    /**
     * Update system status card
     */
    function updateSystemStatus(status) {
        const container = document.getElementById('system-status');
        if (!container) return;

        const schedulerEnabled = status.scheduler?.enabled ?? false;
        const nextUpdate = status.scheduler?.next_run;

        container.innerHTML = `
            <div class="d-flex flex-wrap gap-md">
                <div style="flex: 1; min-width: 200px;">
                    <div class="mb-md">
                        <div class="text-small text-muted mb-sm">Status</div>
                        <div class="d-flex align-center gap-sm">
                            <span class="status-dot ${isJobRunning ? 'status-running' : 'status-idle'}"></span>
                            <span class="badge ${isJobRunning ? 'badge-primary' : 'badge-success'}">${isJobRunning ? 'Running' : 'Idle'}</span>
                        </div>
                    </div>
                    <div class="mb-md">
                        <div class="text-small text-muted mb-sm">Scheduler</div>
                        <span class="badge ${schedulerEnabled ? 'badge-success' : 'badge-secondary'}">${schedulerEnabled ? 'Enabled' : 'Disabled'}</span>
                    </div>
                    <div>
                        <div class="text-small text-muted mb-sm">Next Scheduled Update</div>
                        <span>${nextUpdate ? formatTimestamp(nextUpdate) : 'Not scheduled'}</span>
                    </div>
                </div>
                <div style="flex: 1; min-width: 200px;">
                    <div class="mb-md">
                        <div class="text-small text-muted mb-sm">Last Update</div>
                        <span>${status.last_update ? formatTimestamp(status.last_update, { relative: true }) : 'Never'}</span>
                    </div>
                    <div>
                        <button class="btn btn-primary" id="trigger-update-btn">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                                <path d="M23 4v6h-6"/>
                                <path d="M1 20v-6h6"/>
                                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                            </svg>
                            Trigger Update
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Setup trigger button
        const triggerBtn = document.getElementById('trigger-update-btn');
        if (triggerBtn) {
            triggerBtn.addEventListener('click', () => {
                if (!api.isAuthenticated()) {
                    pendingAction = () => openModal('update-modal');
                    openModal('apikey-modal');
                } else {
                    openModal('update-modal');
                }
            });
        }
    }

    /**
     * Update current job card
     */
    function updateCurrentJob(job) {
        const container = document.getElementById('current-job');
        if (!container) return;

        if (!job || job.status !== 'running') {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="12" cy="12" r="10"/>
                            <polyline points="12 6 12 12 16 14"/>
                        </svg>
                    </div>
                    <p class="empty-state-title">No Active Job</p>
                    <p class="empty-state-text text-muted">The system is idle. Trigger an update to start processing.</p>
                </div>
            `;
            return;
        }

        const elapsed = job.started_at ? calculateElapsed(job.started_at) : 0;
        const progress = job.tiles_total > 0 ? (job.tiles_processed / job.tiles_total) * 100 : 0;
        const eta = calculateETA(job);

        container.innerHTML = `
            <div>
                <div class="d-flex justify-between align-center mb-md">
                    <div>
                        <span class="badge badge-primary">Running</span>
                        <span class="text-small text-muted ml-sm">Job ${job.job_id?.slice(0, 8) || 'Unknown'}</span>
                    </div>
                    <span class="text-small text-muted">${formatDuration(elapsed, true)} elapsed</span>
                </div>

                <div class="progress-wrapper">
                    <div class="progress-header">
                        <span class="progress-label">Progress</span>
                        <span class="progress-value">${job.tiles_processed || 0} / ${job.tiles_total || '?'} tiles</span>
                    </div>
                    <div class="progress">
                        <div class="progress-bar progress-primary progress-animated" style="width: ${progress}%"></div>
                    </div>
                </div>

                <div class="d-flex flex-wrap gap-md mt-md">
                    <div>
                        <div class="text-small text-muted">Priority</div>
                        <span class="badge badge-secondary">${job.priority_filter || 'All'}</span>
                    </div>
                    <div>
                        <div class="text-small text-muted">Locations Updated</div>
                        <strong>${formatNumber(job.locations_updated || 0)}</strong>
                    </div>
                    <div>
                        <div class="text-small text-muted">ETA</div>
                        <strong>${eta}</strong>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * Update coverage statistics
     */
    function updateCoverageStats(coverage) {
        const container = document.getElementById('coverage-stats');
        if (!container) return;

        if (!coverage) {
            container.innerHTML = '<p class="text-muted">No coverage data available</p>';
            return;
        }

        const priorities = [
            { key: 'high', label: 'High Priority', color: 'success' },
            { key: 'medium', label: 'Medium Priority', color: 'primary' },
            { key: 'low', label: 'Low Priority', color: 'warning' }
        ];

        container.innerHTML = priorities.map(p => {
            const data = coverage[p.key] || { with_data: 0, total: 0 };
            const percent = data.total > 0 ? (data.with_data / data.total) * 100 : 0;

            return `
                <div class="progress-wrapper">
                    <div class="progress-header">
                        <span class="progress-label">${p.label}</span>
                        <span class="progress-value">${formatPercentage(data.with_data, data.total)} (${formatNumber(data.with_data)} / ${formatNumber(data.total)} tiles)</span>
                    </div>
                    <div class="progress">
                        <div class="progress-bar progress-${p.color}" style="width: ${percent}%"></div>
                    </div>
                </div>
            `;
        }).join('');

        // Add total
        const totalWithData = (coverage.high?.with_data || 0) + (coverage.medium?.with_data || 0) + (coverage.low?.with_data || 0);
        const totalTiles = (coverage.high?.total || 0) + (coverage.medium?.total || 0) + (coverage.low?.total || 0);

        container.innerHTML += `
            <div class="mt-md pt-md" style="border-top: 1px solid var(--border-color);">
                <div class="d-flex justify-between">
                    <span class="text-muted">Total Coverage</span>
                    <strong>${formatPercentage(totalWithData, totalTiles)} (${formatNumber(totalWithData)} / ${formatNumber(totalTiles)} tiles)</strong>
                </div>
            </div>
        `;
    }

    /**
     * Update database statistics
     */
    function updateDbStats(status) {
        const container = document.getElementById('db-stats');
        if (!container) return;

        const stats = [
            { label: 'Total Entries', value: formatNumber(status.total_entries || 0), icon: 'database' },
            { label: 'With Dates', value: formatNumber(status.with_dates || 0), icon: 'calendar' },
            { label: 'Tiles Covered', value: formatNumber(status.tiles_covered || 0), icon: 'grid' },
            { label: 'Coverage Rate', value: status.total_entries > 0 ? formatPercentage(status.with_dates, status.total_entries) : '0%', icon: 'percent' }
        ];

        container.innerHTML = stats.map(stat => `
            <div class="stat-card">
                <div class="stat-value">${stat.value}</div>
                <div class="stat-label">${stat.label}</div>
            </div>
        `).join('');
    }

    /**
     * Update recent activity
     */
    function updateRecentActivity(lastJob) {
        const container = document.getElementById('recent-activity');
        if (!container) return;

        if (!lastJob) {
            container.innerHTML = `
                <div class="empty-state">
                    <p class="empty-state-text text-muted">No recent activity</p>
                </div>
            `;
            return;
        }

        // For now, just show the last job since we don't have history
        const duration = lastJob.started_at && lastJob.completed_at ?
            calculateDuration(lastJob.started_at, lastJob.completed_at) : null;

        container.innerHTML = `
            <div class="table" style="overflow-x: auto;">
                <table class="table">
                    <thead>
                        <tr>
                            <th>Job ID</th>
                            <th>Status</th>
                            <th>Priority</th>
                            <th>Tiles</th>
                            <th>Locations</th>
                            <th>Duration</th>
                            <th>Completed</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><code>${lastJob.job_id?.slice(0, 8) || '--'}</code></td>
                            <td><span class="badge ${getStatusBadgeClass(lastJob.status)}">${lastJob.status || 'Unknown'}</span></td>
                            <td>${lastJob.priority_filter || 'All'}</td>
                            <td>${formatNumber(lastJob.tiles_processed || 0)}</td>
                            <td>${formatNumber(lastJob.locations_updated || 0)}</td>
                            <td>${duration ? formatDuration(duration, true) : '--'}</td>
                            <td>${lastJob.completed_at ? formatTimestamp(lastJob.completed_at, { relative: true }) : '--'}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        `;
    }

    // ===== Actions =====

    /**
     * Trigger a manual update
     */
    async function triggerUpdate() {
        const prioritySelect = document.getElementById('update-priority');
        const limitInput = document.getElementById('update-limit');

        const options = {};
        if (prioritySelect.value !== 'all') {
            options.priority_filter = prioritySelect.value;
        }
        if (limitInput.value) {
            options.tile_limit = parseInt(limitInput.value, 10);
        }

        try {
            closeModal('update-modal');
            showNotification('Starting update...', 'info');

            await api.triggerUpdate(options);
            showNotification('Update started successfully', 'success');

            // Refresh dashboard
            await loadDashboardData();
        } catch (error) {
            console.error('Failed to trigger update:', error);

            if (error.isAuthError && error.isAuthError()) {
                api.clearApiKey();
                showNotification('Invalid API key. Please re-authenticate.', 'error');
                openModal('apikey-modal');
            } else {
                showNotification('Failed to start update: ' + error.message, 'error');
            }
        }
    }

    // ===== Auto-Refresh =====

    /**
     * Start auto-refresh
     */
    function startAutoRefresh() {
        updateRefreshRate();
    }

    /**
     * Update refresh rate based on job status
     */
    function updateRefreshRate() {
        if (refreshInterval) {
            clearInterval(refreshInterval);
        }

        const interval = isJobRunning ? CONFIG.refreshIntervalRunning : CONFIG.refreshIntervalIdle;
        refreshInterval = setInterval(loadDashboardData, interval);
    }

    /**
     * Stop auto-refresh
     */
    function stopAutoRefresh() {
        if (refreshInterval) {
            clearInterval(refreshInterval);
            refreshInterval = null;
        }
    }

    // ===== Helpers =====

    /**
     * Calculate elapsed time from start
     */
    function calculateElapsed(startTime) {
        const start = new Date(startTime);
        const now = new Date();
        return Math.floor((now - start) / 1000);
    }

    /**
     * Calculate duration between two timestamps
     */
    function calculateDuration(startTime, endTime) {
        const start = new Date(startTime);
        const end = new Date(endTime);
        return Math.floor((end - start) / 1000);
    }

    /**
     * Calculate ETA for job completion
     */
    function calculateETA(job) {
        if (!job.started_at || !job.tiles_processed || !job.tiles_total) {
            return 'Calculating...';
        }

        const elapsed = calculateElapsed(job.started_at);
        const remaining = job.tiles_total - job.tiles_processed;

        if (job.tiles_processed === 0 || remaining <= 0) {
            return 'Almost done';
        }

        const rate = job.tiles_processed / elapsed; // tiles per second
        const eta = remaining / rate;

        return formatDuration(eta, true);
    }

    /**
     * Get badge class for job status
     */
    function getStatusBadgeClass(status) {
        switch (status) {
            case 'completed': return 'badge-success';
            case 'running': return 'badge-primary';
            case 'failed': return 'badge-danger';
            case 'cancelled': return 'badge-warning';
            default: return 'badge-secondary';
        }
    }

    // ===== Public API =====
    return {
        init,
        refresh: loadDashboardData
    };
})();

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Dashboard;
}
