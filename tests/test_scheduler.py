"""Tests for background scheduler module."""

import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

# Set test environment
os.environ['GOOGLE_MAPS_API_KEY'] = 'test_key'
os.environ['DATABASE_URL'] = ''


class TestJobIdGeneration:
    """Tests for job ID generation."""

    def test_job_id_format(self):
        """Job IDs follow expected format."""
        from app.scheduler import _generate_job_id
        job_id = _generate_job_id()
        assert job_id.startswith('job_')
        # Format: job_YYYYMMDD_HHMMSS
        parts = job_id.split('_')
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS


class TestJobRunningState:
    """Tests for job running state tracking."""

    def test_is_job_running_false_initially(self):
        """No job running initially."""
        from app import scheduler
        # Reset state
        scheduler._current_job = None
        assert scheduler.is_job_running() is False

    def test_is_job_running_true_when_set(self):
        """Reports running when job is active."""
        from app import scheduler
        scheduler._current_job = {'job_id': 'test'}
        try:
            assert scheduler.is_job_running() is True
        finally:
            scheduler._current_job = None

    def test_get_current_job_returns_copy(self):
        """Current job returns a copy, not the original."""
        from app import scheduler
        original = {'job_id': 'test', 'data': 'value'}
        scheduler._current_job = original
        try:
            copy = scheduler.get_current_job()
            copy['data'] = 'modified'
            # Original should be unchanged
            assert scheduler._current_job['data'] == 'value'
        finally:
            scheduler._current_job = None


class TestTileSelection:
    """Tests for tile selection logic."""

    @patch('database.is_postgresql', return_value=False)
    def test_sqlite_fallback(self, mock_is_pg):
        """Falls back to simple selection for SQLite."""
        from app.scheduler import _get_tiles_to_process
        tiles = _get_tiles_to_process(
            priority_filter='high',
            tile_limit=5,
            min_age_days=90
        )
        assert isinstance(tiles, list)
        assert len(tiles) <= 5

    @patch('database.is_postgresql', return_value=False)
    def test_respects_tile_limit(self, mock_is_pg):
        """Respects tile limit parameter."""
        from app.scheduler import _get_tiles_to_process
        tiles = _get_tiles_to_process(
            priority_filter=None,
            tile_limit=3,
            min_age_days=90
        )
        assert len(tiles) <= 3


class TestRunUpdateJob:
    """Tests for the main update job function."""

    def test_processes_tiles(self):
        """Update job processes tiles."""
        with patch('database.is_postgresql', return_value=False), \
             patch('database._conn', MagicMock()), \
             patch('app.scheduler.process_tile') as mock_process, \
             patch('app.scheduler._get_tiles_to_process', return_value=['tile_1', 'tile_2']):

            from app import scheduler

            # Reset state
            scheduler._current_job = None

            mock_process.return_value = {
                'tile_id': 'tile_1',
                'roads_found': 10,
                'locations_checked': 50,
                'locations_updated': 40,
                'api_calls': 20,
                'duration_seconds': 1.5
            }

            os.environ['GOOGLE_MAPS_API_KEY'] = 'test_key'

            result = scheduler.run_update_job(
                priority_filter='high',
                tile_limit=5,
                config={'samples_per_road': 3}
            )

            assert result['status'] == 'completed'
            assert mock_process.call_count == 2

    @patch('app.scheduler.is_job_running', return_value=True)
    def test_skips_if_already_running(self, mock_running):
        """Skips if job already running."""
        from app.scheduler import run_update_job
        result = run_update_job()
        assert result['status'] == 'skipped'

    @patch('database.is_postgresql', return_value=False)
    @patch('app.scheduler._get_tiles_to_process', return_value=[])
    def test_completes_with_no_tiles(self, mock_get_tiles, mock_is_pg):
        """Completes gracefully with no tiles to process."""
        from app import scheduler
        scheduler._current_job = None

        os.environ['GOOGLE_MAPS_API_KEY'] = 'test_key'
        result = scheduler.run_update_job()
        assert result['status'] == 'completed'
        assert result['reason'] == 'No tiles to process'


class TestTriggerUpdateJob:
    """Tests for async job triggering."""

    @patch('app.scheduler.is_job_running', return_value=True)
    def test_returns_false_when_running(self, mock_running):
        """Returns failure when job already running."""
        from app.scheduler import trigger_update_job
        success, message, job_id = trigger_update_job()
        assert success is False
        assert 'already running' in message.lower()
        assert job_id is None

    @patch('app.scheduler.is_job_running', return_value=False)
    @patch('threading.Thread')
    def test_starts_background_thread(self, mock_thread, mock_running):
        """Starts job in background thread."""
        from app.scheduler import trigger_update_job

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        success, message, job_id = trigger_update_job(priority_filter='high')

        assert success is True
        assert job_id is not None
        mock_thread_instance.start.assert_called_once()


class TestSchedulerLifecycle:
    """Tests for scheduler initialization and shutdown."""

    def test_get_scheduler_none_initially(self):
        """Scheduler is None before initialization."""
        from app import scheduler
        # Reset
        scheduler._scheduler = None
        assert scheduler.get_scheduler() is None

    def test_init_creates_scheduler(self):
        """Init creates BackgroundScheduler."""
        with patch('app.scheduler.BackgroundScheduler') as mock_scheduler_class:
            from app import scheduler as sched_module

            # Reset
            sched_module._scheduler = None

            mock_app = MagicMock()
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default=None: {
                'scheduler.enabled': True,
                'scheduler.cron': None,
                'scheduler.interval_hours': 24,
                'update.samples_per_road': 5,
                'update.concurrency': 20,
                'update.adaptive_sampling': True,
                'update.overpass_delay_seconds': 2,
                'update.min_age_for_recheck_days': 90,
                'update.batch_size': 50,
            }.get(key, default)

            mock_scheduler_instance = MagicMock()
            mock_scheduler_class.return_value = mock_scheduler_instance

            result = sched_module.init_scheduler(mock_app, mock_config)

            mock_scheduler_instance.start.assert_called_once()
            assert result is mock_scheduler_instance

            # Cleanup
            sched_module._scheduler = None

    def test_shutdown_handles_none(self):
        """Shutdown handles None scheduler gracefully."""
        from app import scheduler
        scheduler._scheduler = None
        # Should not raise
        scheduler.shutdown_scheduler()


class TestJobRecordTracking:
    """Tests for database job record tracking."""

    @patch('database.is_postgresql', return_value=False)
    def test_create_record_skips_sqlite(self, mock_is_pg):
        """Skips record creation for SQLite."""
        from app.scheduler import _create_job_record
        # Should not raise
        _create_job_record('job_123')

    @patch('database.is_postgresql', return_value=False)
    def test_update_progress_skips_sqlite(self, mock_is_pg):
        """Skips progress update for SQLite."""
        from app.scheduler import _update_job_progress
        # Should not raise
        _update_job_progress('job_123', 5, 100, 50)

    @patch('database.is_postgresql', return_value=False)
    def test_complete_record_skips_sqlite(self, mock_is_pg):
        """Skips completion for SQLite."""
        from app.scheduler import _complete_job_record
        # Should not raise
        _complete_job_record('job_123', 'completed')


class TestNextRunTime:
    """Tests for next run time calculation."""

    def test_returns_none_without_scheduler(self):
        """Returns None when scheduler not initialized."""
        from app import scheduler
        scheduler._scheduler = None
        assert scheduler.get_next_run_time() is None

    def test_returns_none_without_job(self):
        """Returns None when no scheduled job."""
        from app import scheduler

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = None
        scheduler._scheduler = mock_scheduler

        try:
            assert scheduler.get_next_run_time() is None
        finally:
            scheduler._scheduler = None

    def test_returns_next_run_time(self):
        """Returns next run time from job."""
        from app import scheduler

        expected_time = datetime.utcnow() + timedelta(hours=1)

        mock_job = MagicMock()
        mock_job.next_run_time = expected_time

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = mock_job
        scheduler._scheduler = mock_scheduler

        try:
            assert scheduler.get_next_run_time() == expected_time
        finally:
            scheduler._scheduler = None
