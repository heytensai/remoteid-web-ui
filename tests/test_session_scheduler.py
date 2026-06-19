"""Tests for session_scheduler.py"""

from datetime import datetime, timezone
from unittest.mock import call, patch

import pytest


@pytest.fixture
def mock_process_database():
    with patch("session_scheduler.process_database") as mock:
        yield mock


class TestSessionSchedulerConfig:
    def test_defaults(self):
        """SessionDetectionConfig default values"""
        from config import SessionDetectionConfig

        cfg = SessionDetectionConfig()
        assert cfg.enabled is False
        assert cfg.interval == 600
        assert cfg.gap_threshold == 600
        assert cfg.log_level == "INFO"

    def test_from_dict(self):
        from config import SessionDetectionConfig

        cfg = SessionDetectionConfig({
            "enabled": True,
            "interval": 300,
            "gap_threshold": 120,
            "log_level": "debug",
        })
        assert cfg.enabled is True
        assert cfg.interval == 300
        assert cfg.gap_threshold == 120
        assert cfg.log_level == "DEBUG"  # .upper() applied

    def test_empty_dict(self):
        from config import SessionDetectionConfig

        cfg = SessionDetectionConfig({})
        assert cfg.enabled is False
        assert cfg.interval == 600
        assert cfg.gap_threshold == 600
        assert cfg.log_level == "INFO"

    def test_validation_passes(self, sample_config_yaml):
        """Valid session_detection config passes validation"""
        config_path, _ = sample_config_yaml
        from config import WebConfig

        cfg = WebConfig(config_path)
        sd = cfg.session_detection
        assert sd.enabled is False
        assert sd.interval == 600
        assert sd.gap_threshold == 600
        assert sd.log_level == "INFO"

    def test_validation_rejects_bad_interval(self):
        """Negative interval fails validation"""
        import tempfile
        import yaml

        data = {
            "web_interface": {
                "database_path": "/tmp",
                "session_detection": {"enabled": True, "interval": -10},
            }
        }
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with open(fd, "w") as f:
            yaml.dump(data, f)

        from config import WebConfig

        with pytest.raises(ValueError, match="interval must be positive"):
            WebConfig(path)

    def test_validation_rejects_bad_log_level(self):
        """Invalid log_level fails validation"""
        import tempfile
        import yaml

        data = {
            "web_interface": {
                "database_path": "/tmp",
                "session_detection": {"log_level": "TRACE"},
            }
        }
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with open(fd, "w") as f:
            yaml.dump(data, f)

        from config import WebConfig

        with pytest.raises(ValueError, match="log_level must be one of"):
            WebConfig(path)


class TestSessionSchedulerLifecycle:
    def test_start_stop(self, mock_process_database):
        """Starting and stopping the scheduler works cleanly"""
        from config import SessionDetectionConfig
        from session_scheduler import SessionScheduler

        class FakeConfig:
            session_detection = SessionDetectionConfig({"enabled": False, "interval": 1})

        scheduler = SessionScheduler(FakeConfig(), "/fake/db.sqlite")
        assert scheduler.is_running is False

        scheduler.start()
        assert scheduler.is_running is True

        # Start again is a no-op
        scheduler.start()
        assert scheduler.is_running is True

        scheduler.stop()
        assert scheduler.is_running is False

        # Stop again is a no-op
        scheduler.stop()
        assert scheduler.is_running is False

        # process_database should never have been called (disabled)
        mock_process_database.assert_not_called()

    def test_runs_when_enabled(self, mock_process_database):
        """When enabled, process_database is called"""
        from config import SessionDetectionConfig
        from session_scheduler import SessionScheduler

        class FakeConfig:
            session_detection = SessionDetectionConfig({"enabled": True, "interval": 1, "gap_threshold": 120})

        scheduler = SessionScheduler(FakeConfig(), "/fake/db.sqlite")
        scheduler.start()

        import time
        time.sleep(1.5)

        scheduler.stop()

        # First call uses oldest-undetected heuristic. Since the fake DB
        # doesn't exist, it falls back to "now" (skip all on first cycle).
        first_call = mock_process_database.call_args_list[0]
        assert first_call.args == ("/fake/db.sqlite", 120)
        assert first_call.kwargs["dry_run"] is False
        since = first_call.kwargs["since"]
        assert since is not None
        assert isinstance(since, datetime)
        # Should be roughly "now" since the fake DB has no undetected records
        diff = abs((since - datetime.now(timezone.utc)).total_seconds())
        assert diff < 5, f"since={since} too far from now"

        assert scheduler.last_run is not None
        assert isinstance(scheduler.last_run, datetime)

        # Subsequent calls should pass the previous last_run as since
        assert len(mock_process_database.call_args_list) >= 2
        for call_ in mock_process_database.call_args_list[1:]:
            since = call_.kwargs["since"]
            assert since is not None
            assert isinstance(since, datetime)

    def test_skips_when_disabled(self, mock_process_database):
        """When disabled, process_database is NOT called"""
        from config import SessionDetectionConfig
        from session_scheduler import SessionScheduler

        class FakeConfig:
            session_detection = SessionDetectionConfig({"enabled": False, "interval": 1})

        scheduler = SessionScheduler(FakeConfig(), "/fake/db.sqlite")
        scheduler.start()

        import time
        time.sleep(1.5)

        scheduler.stop()

        mock_process_database.assert_not_called()

    def test_alert_engine_called(self, mock_process_database):
        """Alert engine evaluate_all and check_stale are called each cycle"""
        from config import SessionDetectionConfig
        from session_scheduler import SessionScheduler
        from unittest.mock import MagicMock

        class FakeConfig:
            session_detection = SessionDetectionConfig({"enabled": False, "interval": 1})

        mock_alert = MagicMock()
        scheduler = SessionScheduler(FakeConfig(), "/fake/db.sqlite", alert_engine=mock_alert)
        scheduler.start()

        import time
        time.sleep(1.5)

        scheduler.stop()

        mock_alert.evaluate_all.assert_called()
        mock_alert.check_stale.assert_called()

    def test_stop_is_responsive(self, mock_process_database):
        """stop() returns quickly even if interval is long"""
        from config import SessionDetectionConfig
        from session_scheduler import SessionScheduler

        class FakeConfig:
            session_detection = SessionDetectionConfig({"enabled": True, "interval": 3600})

        scheduler = SessionScheduler(FakeConfig(), "/fake/db.sqlite")
        scheduler.start()

        import time
        start = time.time()
        scheduler.stop(join_timeout=3.0)
        elapsed = time.time() - start

        # Should stop in well under 3 seconds
        assert elapsed < 2.0, f"stop took {elapsed:.2f}s, expected < 2s"
        assert scheduler.is_running is False


class TestConfigHotReload:
    def test_session_detection_reloadable(self, sample_config_yaml):
        """Changing session_detection in the YAML is picked up by reload_hot_config"""
        config_path, _ = sample_config_yaml
        from config import WebConfig

        cfg = WebConfig(config_path)
        assert cfg.session_detection.enabled is False

        # Simulate a YAML change by rewriting the file
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["session_detection"] = {
            "enabled": True,
            "interval": 60,
            "gap_threshold": 300,
            "log_level": "DEBUG",
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        cfg.reload_hot_config()
        assert cfg.session_detection.enabled is True
        assert cfg.session_detection.interval == 60
        assert cfg.session_detection.gap_threshold == 300
        assert cfg.session_detection.log_level == "DEBUG"

    def test_hot_reload_logs_change(self, sample_config_yaml, caplog):
        """reload_hot_config logs when session_detection changes"""
        config_path, _ = sample_config_yaml
        from config import WebConfig

        cfg = WebConfig(config_path)

        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["web_interface"]["session_detection"] = {"enabled": True}
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)

        with caplog.at_level("INFO"):
            cfg.reload_hot_config()

        assert any("Reloaded session_detection" in msg for msg in caplog.messages)
