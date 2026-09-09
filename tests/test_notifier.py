"""Tests for notifier.py - notification dispatcher with ntfy and teams support"""

import base64
import json
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from config import NotificationTargetConfig
from notifier import (
    NotifierService, _send_ntfy, _send_discord, _send_teams, _send_mqtt,
    _jinja_env,
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
            "events": ["geozone_enter"],
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

        svc.dispatch("geozone_enter", name="Drone-1", geozone_name="ZoneA")

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

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["token"] == "tk_secret123"

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_with_basic_auth(self, mock_send):
        target = self._make_target(username="admin", password="secret")
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["username"] == "admin"
        assert mock_send.call_args[1]["password"] == "secret"

    @patch("notifier._send_ntfy")
    def test_dispatch_ntfy_server_url_in_click(self, mock_send):
        target = self._make_target()
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["click_url"] == "https://example.com"

    @patch("notifier._send_ntfy")
    def test_dispatch_skips_disabled_target(self, mock_send):
        target = self._make_target(enabled=False)
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        mock_send.assert_not_called()

    @patch("notifier._send_ntfy")
    def test_dispatch_skips_unrelated_events(self, mock_send):
        target = self._make_target(events=["geozone_enter"])
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
        ntfy_target = self._make_target(events=["geozone_enter", "new_session"])
        discord_target = NotificationTargetConfig(
            name="discord", type="discord",
            webhook_url="https://discord.com/api/webhooks/...",
            events=["geozone_enter"],
        )
        svc = NotifierService(
            notifications=[ntfy_target, discord_target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        mock_ntfy.assert_called_once()
        mock_discord.assert_called_once()


# ---------------------------------------------------------------------------
# _send_teams
# ---------------------------------------------------------------------------


class TestSendTeams:
    @patch("notifier.urllib.request.urlopen")
    def test_send_teams_success(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b"1"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        payload = json.dumps({
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {"type": "AdaptiveCard", "body": []}
            }]
        })
        _send_teams("https://webhook.office.com/test", payload)

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://webhook.office.com/test"
        assert req.get_method() == "POST"
        assert req.get_header("Content-type") == "application/json"

    @patch("notifier.urllib.request.urlopen")
    def test_send_teams_with_token(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b"1"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        _send_teams("https://webhook.office.com/test", "{}", token="eyJ0eXAi")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer eyJ0eXAi"

    @patch("notifier.urllib.request.urlopen")
    def test_send_teams_no_token_omits_auth(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b"1"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        _send_teams("https://webhook.office.com/test", "{}")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") is None

    @patch("notifier.urllib.request.urlopen")
    def test_send_teams_http_error(self, mock_urlopen):
        body = b"invalid webhook"
        err = HTTPError(
            url="https://webhook.office.com/test", code=400, msg="Bad Request",
            hdrs={}, fp=BytesIO(body),
        )
        mock_urlopen.side_effect = err

        # Should not raise
        _send_teams("https://webhook.office.com/test", "{}")

    @patch("notifier.urllib.request.urlopen")
    def test_send_teams_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Connection refused")

        # Should not raise
        _send_teams("https://webhook.office.com/test", "{}")

    @patch("notifier.urllib.request.urlopen")
    def test_send_teams_user_agent(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b"1"
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        _send_teams("https://webhook.office.com/test", "{}")

        req = mock_urlopen.call_args[0][0]
        assert "RemoteID-WebUI-Notifier" in req.get_header("User-agent")


# ---------------------------------------------------------------------------
# NotifierService — Teams dispatch
# ---------------------------------------------------------------------------


class TestNotifierServiceTeams:
    def _make_target(self, **kwargs):
        defaults = {
            "name": "teams-test",
            "type": "teams",
            "webhook_url": "https://webhook.office.com/test",
            "events": ["geozone_enter"],
            "token": "",
        }
        defaults.update(kwargs)
        return NotificationTargetConfig(**defaults)

    @patch("notifier._send_teams")
    def test_dispatch_teams_alert(self, mock_send):
        target = self._make_target()
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone-1", geozone_name="ZoneA")

        mock_send.assert_called_once()
        payload_str = mock_send.call_args[0][1]
        payload = json.loads(payload_str)
        assert payload["type"] == "message"
        assert payload["attachments"][0]["contentType"] == \
            "application/vnd.microsoft.card.adaptive"
        card = payload["attachments"][0]["content"]
        texts = [b["text"] for b in card["body"] if b.get("type") == "TextBlock"]
        assert any("Geozone Alert" in t for t in texts)
        assert any("Drone-1" in t for t in texts)
        assert any("ZoneA" in t for t in texts)

    @patch("notifier._send_teams")
    def test_dispatch_teams_new_session(self, mock_send):
        target = self._make_target(
            name="teams-sessions",
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
        payload_str = mock_send.call_args[0][1]
        payload = json.loads(payload_str)
        card = payload["attachments"][0]["content"]
        texts = [b["text"] for b in card["body"] if b.get("type") == "TextBlock"]
        assert any("New Flight Detected" in t for t in texts)
        assert any("Drone-2" in t for t in texts)
        assert any("100m" in t for t in texts)

    @patch("notifier._send_teams")
    def test_dispatch_teams_with_token(self, mock_send):
        target = self._make_target(token="eyJ0eXAiOi")
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["token"] == "eyJ0eXAiOi"

    @patch("notifier._send_teams")
    def test_dispatch_teams_server_url_in_card(self, mock_send):
        target = self._make_target()
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        payload_str = mock_send.call_args[0][1]
        payload = json.loads(payload_str)
        card = payload["attachments"][0]["content"]
        texts = [b["text"] for b in card["body"] if b.get("type") == "TextBlock"]
        assert any("example.com" in t for t in texts)

    @patch("notifier._send_teams")
    def test_dispatch_teams_skips_disabled(self, mock_send):
        target = self._make_target(enabled=False)
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("geozone_enter", name="Drone", geozone_name="Zone")

        mock_send.assert_not_called()

    @patch("notifier._send_teams")
    def test_dispatch_teams_skips_unrelated_events(self, mock_send):
        target = self._make_target(events=["geozone_enter"])
        svc = NotifierService(
            notifications=[target],
            server_url="https://example.com",
        )

        svc.dispatch("new_session", name="Drone", session_id="abc",
                      altitude=None, height=None, height_type=None,
                      use_metric=True)

        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# json_escape filter
# ---------------------------------------------------------------------------


class TestJsonEscape:
    def test_escapes_quotes(self):
        f = _jinja_env.filters["json_escape"]
        assert f('say "hello"') == 'say \\"hello\\"'

    def test_escapes_backslash(self):
        f = _jinja_env.filters["json_escape"]
        assert f("path\\to") == "path\\\\to"

    def test_escapes_newlines(self):
        f = _jinja_env.filters["json_escape"]
        assert f("line1\nline2") == "line1\\nline2"

    def test_escapes_carriage_return(self):
        f = _jinja_env.filters["json_escape"]
        assert f("a\rb") == "a\\rb"

    def test_escapes_tab(self):
        f = _jinja_env.filters["json_escape"]
        assert f("a\tb") == "a\\tb"

    def test_safe_string_unchanged(self):
        f = _jinja_env.filters["json_escape"]
        assert f("normal-drone-123") == "normal-drone-123"

    def test_empty_string(self):
        f = _jinja_env.filters["json_escape"]
        assert f("") == ""


# ---------------------------------------------------------------------------
# Template escaping integration
# ---------------------------------------------------------------------------


class TestTemplateEscaping:
    """Verify that Discord/Teams templates produce valid JSON with escaped user data."""

    @patch("notifier._send_discord")
    def test_discord_geozone_enter_escapes_name(self, mock_send):
        target = NotificationTargetConfig(
            name="d-test", type="discord",
            webhook_url="https://discord.com/api/webhooks/test",
            events=["geozone_enter"],
        )
        svc = NotifierService(notifications=[target], server_url="https://example.com")
        svc.dispatch("geozone_enter", name='Drone "One"', geozone_name="Zone A")
        raw = mock_send.call_args[0][1]
        payload = json.loads(raw)
        assert payload["embeds"][0]["fields"][0]["value"] == 'Drone "One"'
        assert 'Drone \\"One\\"' in raw

    @patch("notifier._send_discord")
    def test_discord_proximity_escapes_names(self, mock_send):
        target = NotificationTargetConfig(
            name="d-test", type="discord",
            webhook_url="https://discord.com/api/webhooks/test",
            events=["drone_proximity"],
        )
        svc = NotifierService(notifications=[target], server_url="https://example.com")
        svc.dispatch("drone_proximity",
                      name_a='Drone "A"', name_b="Drone B\\C",
                      distance_m=50.0, distance_str="50 m", use_metric=True)
        raw = mock_send.call_args[0][1]
        payload = json.loads(raw)
        desc = payload["embeds"][0]["description"]
        assert 'Drone "A"' in desc
        assert 'Drone B\\C' in desc
        assert 'Drone \\"A\\"' in raw
        assert 'Drone B\\\\C' in raw

    @patch("notifier._send_teams")
    def test_teams_geozone_enter_escapes_name(self, mock_send):
        target = NotificationTargetConfig(
            name="t-test", type="teams",
            webhook_url="https://example.com/teams/webhook",
            events=["geozone_enter"],
        )
        svc = NotifierService(notifications=[target], server_url="https://example.com")
        svc.dispatch("geozone_enter", name='Drone "One"', geozone_name="Zone A")
        raw = mock_send.call_args[0][1]
        payload = json.loads(raw)
        card = payload["attachments"][0]["content"]
        text_blocks = [b for b in card["body"] if b.get("type") == "TextBlock"]
        assert 'Drone "One"' in text_blocks[1]["text"]
        assert 'Drone \\"One\\"' in raw

    @patch("notifier._send_ntfy")
    def test_ntfy_not_affected_by_json_escape(self, mock_send):
        target = NotificationTargetConfig(
            name="n-test", type="ntfy",
            webhook_url="https://ntfy.sh/test",
            events=["geozone_enter"],
        )
        svc = NotifierService(notifications=[target], server_url="https://example.com")
        svc.dispatch("geozone_enter", name='Drone "One"', geozone_name="Zone A")
        payload = mock_send.call_args[0][1]
        assert 'Drone "One"' in payload


# ---------------------------------------------------------------------------
# _send_mqtt (minimal publish-only MQTT 3.1.1 client)
# ---------------------------------------------------------------------------


class _FakeSocket:
    """Socket stand-in that records sent bytes and returns a canned CONNACK."""

    def __init__(self, connack=b"\x20\x02\x00\x00"):
        self.sent = b""
        self.closed = False
        self._connack = connack

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        return self._connack

    def close(self):
        self.closed = True


class TestSendMqtt:
    @patch("notifier.socket.create_connection")
    def test_send_mqtt_success(self, mock_conn):
        fake = _FakeSocket()
        mock_conn.return_value = fake

        _send_mqtt("mqtt://mqtt.local:1883", '{"event":"geozone_enter"}',
                   "remoteid/alerts/geozone_enter")

        mock_conn.assert_called_once_with(("mqtt.local", 1883), timeout=15)
        assert fake.sent.startswith(b"\x10")  # CONNECT
        assert b"\x30" in fake.sent  # PUBLISH (QoS 0)
        assert b"remoteid/alerts/geozone_enter" in fake.sent
        assert b'{"event":"geozone_enter"}' in fake.sent
        assert fake.sent.endswith(b"\xe0\x00")  # DISCONNECT
        assert fake.closed

    @patch("notifier.ssl.create_default_context")
    @patch("notifier.socket.create_connection")
    def test_send_mqtt_default_ports(self, mock_conn, mock_ctx):
        fake = _FakeSocket()
        mock_conn.return_value = fake
        ctx = MagicMock()
        ctx.wrap_socket.return_value = fake
        mock_ctx.return_value = ctx

        _send_mqtt("mqtt://mqtt.local", "p", "t")
        assert mock_conn.call_args[0][0] == ("mqtt.local", 1883)

        _send_mqtt("mqtts://mqtt.local", "p", "t")
        assert mock_conn.call_args[0][0] == ("mqtt.local", 8883)

    @patch("notifier.socket.create_connection")
    def test_send_mqtt_with_auth(self, mock_conn):
        fake = _FakeSocket()
        mock_conn.return_value = fake

        _send_mqtt("mqtt://mqtt.local", "p", "t",
                   username="admin", password="secret")

        # CONNECT flags: clean session (0x02) + username (0x80) + password (0x40)
        assert fake.sent[9] == 0xC2
        assert b"admin" in fake.sent
        assert b"secret" in fake.sent

    @patch("notifier.socket.create_connection")
    def test_send_mqtt_no_auth(self, mock_conn):
        fake = _FakeSocket()
        mock_conn.return_value = fake

        _send_mqtt("mqtt://mqtt.local", "p", "t")

        assert fake.sent[9] == 0x02  # clean session only
        assert b"\x00\x04admin" not in fake.sent

    @patch("notifier.socket.create_connection")
    def test_send_mqtt_broker_rejects(self, mock_conn):
        fake = _FakeSocket(connack=b"\x20\x02\x00\x05")  # not authorized
        mock_conn.return_value = fake

        _send_mqtt("mqtt://mqtt.local", "p", "t")

        # CONNECT sent, but no PUBLISH for a rejected connection
        assert fake.sent.startswith(b"\x10")
        assert b"\x30\x04\x00\x01t p" not in fake.sent

    @patch("notifier.socket.create_connection")
    def test_send_mqtt_unexpected_connack(self, mock_conn):
        fake = _FakeSocket(connack=b"\xff\x02\x00\x00")
        mock_conn.return_value = fake

        _send_mqtt("mqtt://mqtt.local", "p", "t")

        assert b"\x30\x04\x00\x01t p" not in fake.sent

    @patch("notifier.socket.create_connection")
    def test_send_mqtt_connection_error(self, mock_conn):
        mock_conn.side_effect = OSError("Connection refused")

        # Should not raise
        _send_mqtt("mqtt://mqtt.local", "p", "t")

    @patch("notifier.socket.create_connection")
    def test_send_mqtt_unsupported_scheme(self, mock_conn):
        _send_mqtt("http://mqtt.local", "p", "t")
        mock_conn.assert_not_called()

    @patch("notifier.ssl.create_default_context")
    @patch("notifier.socket.create_connection")
    def test_send_mqtt_tls(self, mock_conn, mock_ctx):
        fake = _FakeSocket()
        mock_conn.return_value = fake
        ctx = MagicMock()
        ctx.wrap_socket.return_value = fake
        mock_ctx.return_value = ctx

        _send_mqtt("mqtts://mqtt.local:8883", "p", "t")

        ctx.wrap_socket.assert_called_once_with(fake, server_hostname="mqtt.local")
        assert b"\x30" in fake.sent


# ---------------------------------------------------------------------------
# NotifierService — MQTT dispatch
# ---------------------------------------------------------------------------


class TestNotifierServiceMqtt:
    def _make_target(self, **kwargs):
        defaults = {
            "name": "mqtt-test",
            "type": "mqtt",
            "broker_url": "mqtt://mqtt.local:1883",
            "topic_prefix": "remoteid/alerts",
            "events": ["geozone_enter"],
            "username": "",
            "password": "",
        }
        defaults.update(kwargs)
        return NotificationTargetConfig(**defaults)

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_publishes_json(self, mock_send):
        target = self._make_target()
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("geozone_enter", uas_id="drone-001", name='Drone "One"',
                     geozone_name="Zone A", use_metric=True)

        mock_send.assert_called_once()
        broker_url, payload, topic = mock_send.call_args[0]
        assert broker_url == "mqtt://mqtt.local:1883"
        assert topic == "remoteid/alerts/geozone_enter"
        data = json.loads(payload)
        assert data["event"] == "geozone_enter"
        assert data["uas_id"] == "drone-001"
        assert data["name"] == 'Drone "One"'
        assert data["geozone"] == "Zone A"

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_topic_without_prefix(self, mock_send):
        target = self._make_target(topic_prefix="")
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("geozone_enter", uas_id="d", name="Drone", geozone_name="Zone")

        topic = mock_send.call_args[0][2]
        assert topic == "geozone_enter"

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_with_auth(self, mock_send):
        target = self._make_target(username="admin", password="secret")
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("geozone_enter", uas_id="d", name="Drone", geozone_name="Zone")

        assert mock_send.call_args[1]["username"] == "admin"
        assert mock_send.call_args[1]["password"] == "secret"

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_new_session_payload(self, mock_send):
        target = self._make_target(name="mqtt-sessions", events=["new_session"])
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("new_session", uas_id="drone-001", name="Drone-1",
                     session_id="session_a1b2c3d4e5f6", altitude=100.0, height=50.0,
                     height_type="agl", lat=37.7749, lon=-122.4194, use_metric=True)

        payload = json.loads(mock_send.call_args[0][1])
        assert payload["event"] == "new_session"
        assert payload["uas_id"] == "drone-001"
        assert payload["session_id"] == "session_a1b2c3d4e5f6"
        assert payload["altitude"] == 100.0
        assert payload["height"] == 50.0
        assert payload["height_type"] == "agl"
        assert payload["latitude"] == 37.7749
        assert payload["longitude"] == -122.4194

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_new_session_without_position(self, mock_send):
        target = self._make_target(name="mqtt-sessions", events=["new_session"])
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("new_session", uas_id="drone-001", name="Drone-1",
                     session_id="session_a1b2c3d4e5f6", use_metric=True)

        payload = json.loads(mock_send.call_args[0][1])
        assert payload["uas_id"] == "drone-001"
        assert "altitude" not in payload
        assert "latitude" not in payload

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_renders_valid_json_for_all_events(self, mock_send):
        target = self._make_target(
            events=["geozone_enter", "geozone_exit", "new_session",
                    "unrecognized_drone", "drone_proximity"],
        )
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("geozone_enter", uas_id="d", name="Drone", geozone_name="Zone", use_metric=True)
        svc.dispatch("geozone_exit", uas_id="d", name="Drone", geozone_name="Zone", use_metric=True)
        svc.dispatch("new_session", uas_id="d", name="Drone", session_id="session_1", use_metric=True)
        svc.dispatch("unrecognized_drone", uas_id="d", name="d", session_id="session_2", use_metric=True)
        svc.dispatch("drone_proximity", uas_id_a="a", name_a="A", uas_id_b="b",
                     name_b="B", distance_m=50.0, distance_str="50 m", use_metric=True)

        assert mock_send.call_count == 5
        for call in mock_send.call_args_list:
            data = json.loads(call[0][1])
            assert data["event"]

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_skips_disabled(self, mock_send):
        target = self._make_target(enabled=False)
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("geozone_enter", uas_id="d", name="Drone", geozone_name="Zone")

        mock_send.assert_not_called()

    @patch("notifier._send_mqtt")
    def test_dispatch_mqtt_skips_unrelated_events(self, mock_send):
        target = self._make_target(events=["geozone_enter"])
        svc = NotifierService(notifications=[target], server_url="https://example.com")

        svc.dispatch("new_session", uas_id="d", name="Drone", session_id="s",
                     use_metric=True)

        mock_send.assert_not_called()
