"""Tests for notifier.py - notification dispatcher with ntfy support"""

import base64
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from config import NotificationTargetConfig
from notifier import (
    NotifierService, _send_ntfy, _send_discord, _render_push,
)


# ---------------------------------------------------------------------------
# _send_ntfy
# ---------------------------------------------------------------------------


def _mock_opener(resp_body=b"ok", status=200, side_effect=None):
    """Create a mock opener whose open() returns the given response."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = resp_body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    opener = MagicMock()
    if side_effect:
        opener.open.side_effect = side_effect
    else:
        opener.open.return_value = resp
    return opener


class TestSendNtfy:
    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_success(self, mock_build):
        mock_build.return_value = _mock_opener(b'{"id":"abc"}')

        _send_ntfy("https://ntfy.sh/mytopic", "hello world")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.full_url == "https://ntfy.sh/mytopic"
        assert req.get_method() == "POST"
        assert b"hello world" in req.data

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_with_token(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi", token="tk_abc")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer tk_abc"

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_no_token_omits_auth(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Authorization") is None

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_empty_token_omits_auth(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi", token="")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Authorization") is None

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_basic_auth(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi",
                    username="admin", password="secret")

        req = mock_build.return_value.open.call_args[0][0]
        expected = "Basic " + base64.b64encode(b"admin:secret").decode()
        assert req.get_header("Authorization") == expected

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_basic_auth_takes_precedence_over_token(self, mock_build):
        """When both username and token are set, Basic auth is used."""
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi",
                    token="tk_abc", username="admin", password="secret")

        req = mock_build.return_value.open.call_args[0][0]
        expected = "Basic " + base64.b64encode(b"admin:secret").decode()
        assert req.get_header("Authorization") == expected

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_basic_auth_empty_password(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi",
                    username="admin", password="")

        req = mock_build.return_value.open.call_args[0][0]
        expected = "Basic " + base64.b64encode(b"admin:").decode()
        assert req.get_header("Authorization") == expected

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_http_error(self, mock_build):
        body = b"topic not found"
        err = HTTPError(
            url="https://ntfy.sh/t", code=404, msg="Not Found",
            hdrs={}, fp=BytesIO(body),
        )
        mock_build.return_value = _mock_opener(side_effect=err)

        # Should not raise
        _send_ntfy("https://ntfy.sh/t", "hi")

    @patch("notifier._log_connection_debug")
    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_connection_error(self, mock_build, mock_debug):
        mock_build.return_value = _mock_opener(side_effect=URLError("Connection refused"))

        # Should not raise
        _send_ntfy("https://ntfy.sh/t", "hi")
        mock_debug.assert_called_once()

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_user_agent(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "hi")

        req = mock_build.return_value.open.call_args[0][0]
        assert "RemoteID-WebUI-Notifier" in req.get_header("User-agent")

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_title_header(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "msg", title="My Title")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Title") == "My Title"

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_priority_header(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "msg", priority=4)

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Priority") == "4"

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_tags_header(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "msg", tags="warning,drone")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Tags") == "warning,drone"

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_click_header(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "msg", click_url="https://example.com")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Click") == "https://example.com"

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_no_content_type(self, mock_build):
        """ntfy plain text does not set Content-Type header."""
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "msg")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Content-type") is None

    @patch("notifier.urllib.request.build_opener")
    def test_send_ntfy_omits_empty_headers(self, mock_build):
        mock_build.return_value = _mock_opener()

        _send_ntfy("https://ntfy.sh/t", "msg", title="", priority=0,
                    tags="", click_url="")

        req = mock_build.return_value.open.call_args[0][0]
        assert req.get_header("Title") is None
        assert req.get_header("Priority") is None
        assert req.get_header("Tags") is None
        assert req.get_header("Click") is None


# ---------------------------------------------------------------------------
# NotifierService dispatch
# ---------------------------------------------------------------------------


class TestNotifierServiceNtfy:
    def _make_target(self, **kwargs):
        defaults = {
            "name": "ntfy-test",
            "type": "ntfy",
            "webhook_url": "https://ntfy.sh/test-topic",
            "events": ["alert"],
            "token": "",
        }
        defaults.update(kwargs)
        return NotificationTargetConfig(**defaults)

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_alert(self, mock_send):
        target = self._make_target()
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("alert", name="Drone-1", geozone_name="ZoneA")

        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert "Drone-1" in payload
        assert "ZoneA" in payload
        assert mock_send.call_args[1]["title"] == "Geozone Alert"
        assert mock_send.call_args[1]["priority"] == 4
        assert mock_send.call_args[1]["tags"] == "warning,drone"
        assert mock_send.call_args[1]["click_url"] == "https://example.com"

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_new_session(self, mock_send):
        target = self._make_target(
            name="ntfy-sessions",
            events=["new_session"],
        )
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("new_session", name="Drone-2", session_id="abc12345",
                      altitude=100.0, height=50.0, height_type="agl",
                      use_metric=True)

        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert "Drone-2" in payload
        assert "100m" in payload
        assert mock_send.call_args[1]["title"] == "New Flight Detected"
        assert mock_send.call_args[1]["priority"] == 3
        assert mock_send.call_args[1]["tags"] == "drone"

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_with_token(self, mock_send):
        target = self._make_target(token="tk_secret123")
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("alert", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["token"] == "tk_secret123"

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_with_basic_auth(self, mock_send):
        target = self._make_target(username="admin", password="secret")
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("alert", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["username"] == "admin"
        assert mock_send.call_args[1]["password"] == "secret"

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_server_url_in_click(self, mock_send):
        target = self._make_target()
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("alert", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["click_url"] == "https://example.com"

    @patch("notifier._send_ntfy")
    def test_dispatch_skips_disabled_target(self, mock_send):
        target = self._make_target(enabled=False)
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("alert", name="Drone", geozone_name="Zone")

        mock_send.assert_not_called()

    @patch("notifier._send_ntfy")
    def test_dispatch_skips_unrelated_events(self, mock_send):
        target = self._make_target(events=["alert"])
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("new_session", name="Drone", session_id="abc",
                      altitude=None, height=None, height_type=None,
                      use_metric=True)

        mock_send.assert_not_called()

    @patch("notifier._send_ntfy")
    @patch("notifier._send_discord")
    def test_dispatch_mixed_types(self, mock_discord, mock_ntfy):
        ntfy_target = self._make_target(events=["alert", "new_session"])
        discord_target = NotificationTargetConfig(
            name="discord", type="discord",
            webhook_url="https://discord.com/api/webhooks/...",
            events=["alert"],
        )
        svc = NotifierService(
            notifications=[ntfy_target, discord_target],
            server_url="https://example.com",
        )

        svc.dispatch("alert", name="Drone", geozone_name="Zone")

        mock_ntfy.assert_called_once()
        mock_discord.assert_called_once()
