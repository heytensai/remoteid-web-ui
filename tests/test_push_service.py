"""Tests for push_service.py - Web Push notification service"""

from unittest.mock import MagicMock, patch, call

import pytest

from push_service import PushService, _vapid_keys_path, _validate_vapid_key


class TestVapidHelpers:
    def test_vapid_keys_path_with_path(self):
        path = _vapid_keys_path("/data/web.db")
        assert path.endswith("vapid_keys")
        assert "/data/" in path
        assert "web.db" not in path

    def test_vapid_keys_path_none(self):
        path = _vapid_keys_path(None)
        assert path.endswith("vapid_keys")
        assert "vapid_keys" in path

    def test_vapid_keys_path_relative(self):
        path = _vapid_keys_path("./data/web.db")
        assert path.endswith("vapid_keys")

    def test_validate_vapid_key_invalid(self):
        assert _validate_vapid_key("not-a-pem-key") is False

    def test_validate_vapid_key_empty(self):
        assert _validate_vapid_key("") is False


class TestPushServiceInit:
    def test_init_basic(self):
        mock_db = MagicMock()
        service = PushService(mock_db, "private-key", "public-key")
        assert service is not None
        assert service._vapid_public_key == "public-key"

    def test_init_with_none_keys(self):
        mock_db = MagicMock()
        service = PushService(mock_db, None, None)
        assert service._vapid_private_key is None
        assert service._vapid_public_key is None

    def test_init_converts_pem_to_der(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend

        private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

        mock_db = MagicMock()
        service = PushService(mock_db, pem, "public-key")
        # Should have been converted to DER
        assert service._vapid_private_key != pem
        assert isinstance(service._vapid_private_key, bytes)


class TestPushServiceSubscribeUnsubscribe:
    def test_subscribe(self):
        mock_db = MagicMock()
        service = PushService(mock_db, "priv", "pub")
        service.subscribe("https://example.com/ep", "p256dh_val", "auth_val", "TestBrowser")
        mock_db.save_push_subscription.assert_called_once_with(
            "https://example.com/ep", "p256dh_val", "auth_val", "TestBrowser"
        )

    def test_subscribe_no_user_agent(self):
        mock_db = MagicMock()
        service = PushService(mock_db, "priv", "pub")
        service.subscribe("https://example.com/ep", "p256dh_val", "auth_val")
        mock_db.save_push_subscription.assert_called_once_with(
            "https://example.com/ep", "p256dh_val", "auth_val", None
        )

    def test_unsubscribe(self):
        mock_db = MagicMock()
        service = PushService(mock_db, "priv", "pub")
        service.unsubscribe("https://example.com/ep")
        mock_db.remove_push_subscription.assert_called_once_with(
            "https://example.com/ep"
        )


class TestPushServiceNotifyAll:
    def test_notify_all_no_subscribers(self):
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = []
        service = PushService(mock_db, "priv", "pub")
        service.notify_all("Title", "Body")
        mock_db.get_all_push_subscriptions.assert_called_once()

    def test_notify_all_sends_to_subscribers(self):
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep1", "p256dh_key": "k1", "auth_key": "a1"},
            {"endpoint": "https://ep2", "p256dh_key": "k2", "auth_key": "a2"},
        ]
        service = PushService(mock_db, "priv", "pub")

        with patch("push_service.webpush") as mock_webpush:
            service.notify_all("Hello", "World", data={"key": "val"})
            assert mock_webpush.call_count == 2
            first_call = mock_webpush.call_args_list[0]
            assert first_call.kwargs["subscription_info"]["endpoint"] == "https://ep1"
            assert first_call.kwargs["vapid_private_key"] == "priv"

    def test_notify_all_removes_gone_subscription(self):
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep-gone", "p256dh_key": "k1", "auth_key": "a1"},
        ]
        service = PushService(mock_db, "priv", "pub")

        with patch("push_service.webpush") as mock_webpush:
            from pywebpush import WebPushException
            exc = WebPushException("gone")
            exc.response = MagicMock()
            exc.response.status_code = 410
            mock_webpush.side_effect = exc

            service.notify_all("Title", "Body")
            mock_db.remove_push_subscription.assert_called_once_with("https://ep-gone")

    def test_notify_all_logs_other_errors(self):
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep", "p256dh_key": "k", "auth_key": "a"},
        ]
        service = PushService(mock_db, "priv", "pub")

        from pywebpush import WebPushException

        with patch("push_service.webpush") as mock_webpush:
            mock_webpush.side_effect = WebPushException("network error")
            service.notify_all("Title", "Body")
            mock_db.remove_push_subscription.assert_not_called()
