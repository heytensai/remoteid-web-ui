"""Tests for maintenance_scheduler.py"""

from unittest.mock import MagicMock, patch

import pytest


class TestMaintenanceSchedulerConfig:
    def test_defaults(self):
        """MaintenanceConfig default values"""
        from config import MaintenanceConfig

        cfg = MaintenanceConfig()
        assert cfg.enabled is False
        assert cfg.interval == 3600
        assert cfg.delete_expired_tokens is True
        assert cfg.delete_expired_login_tokens is True
        assert cfg.delete_orphaned_ephemeral_users is True

    def test_from_dict(self):
        from config import MaintenanceConfig

        cfg = MaintenanceConfig({
            "enabled": True,
            "interval": 7200,
            "delete_expired_tokens": False,
        })
        assert cfg.enabled is True
        assert cfg.interval == 7200
        assert cfg.delete_expired_tokens is False
        assert cfg.delete_expired_login_tokens is True  # default


class TestMaintenanceSchedulerLifecycle:
    def test_start_stop(self):
        """Starting and stopping the scheduler works cleanly"""
        from config import MaintenanceConfig
        from maintenance_scheduler import MaintenanceScheduler

        class FakeConfig:
            maintenance = MaintenanceConfig({"enabled": False, "interval": 1})

        database = MagicMock()
        scheduler = MaintenanceScheduler(FakeConfig(), database)
        scheduler.start()
        assert scheduler.is_running

        scheduler.stop()
        assert scheduler.is_running is False

        # Stop again is a no-op
        scheduler.stop()
        assert scheduler.is_running is False

    def test_skips_when_disabled(self):
        """When disabled, cleanup methods are NOT called"""
        from config import MaintenanceConfig
        from maintenance_scheduler import MaintenanceScheduler

        class FakeConfig:
            maintenance = MaintenanceConfig({"enabled": False, "interval": 1})

        database = MagicMock()
        scheduler = MaintenanceScheduler(FakeConfig(), database)
        scheduler.start()
        import time
        time.sleep(1.5)
        scheduler.stop()

        database.cleanup_expired_auth_tokens.assert_not_called()
        database.cleanup_expired_login_tokens.assert_not_called()
        database.cleanup_orphaned_ephemeral_users.assert_not_called()

    def test_runs_when_enabled(self):
        """When enabled, cleanup methods are called"""
        from config import MaintenanceConfig
        from maintenance_scheduler import MaintenanceScheduler

        class FakeConfig:
            maintenance = MaintenanceConfig({"enabled": True, "interval": 1})

        database = MagicMock()
        scheduler = MaintenanceScheduler(FakeConfig(), database)
        scheduler.start()
        import time
        time.sleep(1.5)
        scheduler.stop()

        database.cleanup_expired_auth_tokens.assert_called()
        database.cleanup_expired_login_tokens.assert_called()
        database.cleanup_orphaned_ephemeral_users.assert_called()

    def test_skips_individual_tasks_when_disabled(self):
        """Each cleanup task can be individually disabled"""
        from config import MaintenanceConfig
        from maintenance_scheduler import MaintenanceScheduler

        class FakeConfig:
            maintenance = MaintenanceConfig({
                "enabled": True,
                "interval": 1,
                "delete_expired_tokens": False,
                "delete_expired_login_tokens": True,
                "delete_orphaned_ephemeral_users": False,
            })

        database = MagicMock()
        scheduler = MaintenanceScheduler(FakeConfig(), database)
        scheduler.start()
        import time
        time.sleep(1.5)
        scheduler.stop()

        database.cleanup_expired_auth_tokens.assert_not_called()
        database.cleanup_expired_login_tokens.assert_called()
        database.cleanup_orphaned_ephemeral_users.assert_not_called()

    def test_stop_is_responsive(self):
        """stop() returns quickly even with a long interval"""
        from config import MaintenanceConfig
        from maintenance_scheduler import MaintenanceScheduler

        class FakeConfig:
            maintenance = MaintenanceConfig({"enabled": False, "interval": 9999})

        database = MagicMock()
        scheduler = MaintenanceScheduler(FakeConfig(), database)
        scheduler.start()

        import time
        start = time.monotonic()
        scheduler.stop(join_timeout=2.0)
        elapsed = time.monotonic() - start

        assert elapsed < 3.0, f"stop() took {elapsed:.2f}s, expected < 3.0s"
