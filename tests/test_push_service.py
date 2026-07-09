"""Tests for push_service.py - Web Push notification service"""

import importlib
from unittest.mock import MagicMock, patch, call

import pytest

from push_service import PushService, _vapid_keys_path, _validate_vapid_key
from pywebpush import Vapid01


def _valid_pem():
    """Generate a real EC private key PEM for tests that exercise notify_all."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.backends import default_backend
    key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


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

    def test_init_keeps_pem_as_string(self):
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
        # Should remain as the PEM string — pywebpush expects a str
        assert service._vapid_private_key == pem
        assert isinstance(service._vapid_private_key, str)

    def test_init_invalid_pem_stays_as_string(self):
        """Invalid PEM is kept as-is (no DER conversion attempted)."""
        mock_db = MagicMock()
        service = PushService(mock_db, "not-a-valid-pem", "pub")
        assert service._vapid_private_key == "not-a-valid-pem"


class TestVapidHelpersExtended:
    def test_vapid_public_b64url(self):
        """_vapid_public_b64url extracts a base64url public key from a Vapid01 instance."""
        from pywebpush import Vapid01
        v = Vapid01()
        v.generate_keys()
        from push_service import _vapid_public_b64url
        result = _vapid_public_b64url(v)
        assert isinstance(result, str)
        assert len(result) > 20
        # Should be base64url (no padding)
        assert "=" not in result

    def test_validate_vapid_key_valid_pem(self):
        """A valid EC private key PEM is accepted by _validate_vapid_key."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        assert _validate_vapid_key(pem) is True

    def test_validate_vapid_key_corrupt_pem(self):
        """A PEM with wrong inner content causes validation to return False."""
        # EC public key PEM (valid format, wrong key type for private key parsing)
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend

        key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        public_pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        assert _validate_vapid_key(public_pem) is False

    def test_validate_vapid_key_no_serialization(self):
        """When cryptography is unavailable, validation passes trivially."""
        with patch("push_service.serialization", None):
            assert _validate_vapid_key("anything") is True

    def test_ensure_vapid_keys_generates_new(self):
        """_ensure_vapid_keys generates keys when no file exists."""
        import os
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        from push_service import _ensure_vapid_keys
        private, public = _ensure_vapid_keys(db_path)
        assert private is not None
        assert public is not None
        assert len(private) > 50
        assert len(public) > 20
        # Keys file should have been written
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")
        assert os.path.exists(keys_path)
        os.unlink(db_path)
        os.unlink(keys_path)

    def test_ensure_vapid_keys_loads_existing(self):
        """_ensure_vapid_keys loads existing keys from file."""
        import json
        import os
        import tempfile

        from pywebpush import Vapid01
        v = Vapid01()
        v.generate_keys()
        private_pem = v.private_pem().decode()

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")
        with open(keys_path, "w", encoding="utf-8") as f:
            json.dump({"private_key": private_pem, "public_key": "existing-pub"}, f)

        from push_service import _ensure_vapid_keys
        private, public = _ensure_vapid_keys(db_path)
        assert private == private_pem
        assert public == "existing-pub"
        os.unlink(db_path)
        os.unlink(keys_path)

    def test_ensure_vapid_keys_corrupt_file_regenerates(self):
        """Corrupt keys file triggers regeneration."""
        import os
        import tempfile

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")
        with open(keys_path, "w", encoding="utf-8") as f:
            f.write("not-json")

        from push_service import _ensure_vapid_keys
        private, public = _ensure_vapid_keys(db_path)
        assert private is not None
        assert public is not None
        os.unlink(db_path)
        os.unlink(keys_path)

    def test_ensure_vapid_keys_no_vapid(self):
        """When Vapid01 is unavailable, returns None."""
        with patch("push_service.Vapid01", None):
            from push_service import _ensure_vapid_keys
            private, public = _ensure_vapid_keys("/fake/path")
            assert private is None
            assert public is None

    def test_ensure_vapid_keys_invalid_existing(self):
        """Existing keys file with invalid PEM triggers regeneration."""
        import json
        import os
        import tempfile

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")
        with open(keys_path, "w", encoding="utf-8") as f:
            json.dump({"private_key": "not-valid-pem", "public_key": "pub"}, f)

        from push_service import _ensure_vapid_keys
        private, public = _ensure_vapid_keys(db_path)
        assert private is not None
        assert private != "not-valid-pem"
        os.unlink(db_path)
        os.unlink(keys_path)

    def test_ensure_vapid_keys_regenerated_validated_true(self):
        """When freshly generated key passes validation, no warning logged."""
        import os
        import tempfile

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")

        from push_service import _ensure_vapid_keys
        private, public = _ensure_vapid_keys(db_path)
        assert private is not None
        os.unlink(db_path)
        os.unlink(keys_path)

    def test_ensure_vapid_keys_regenerated_validation_fails(self):
        """When freshly generated key fails validation, warning is logged."""
        import os
        import tempfile

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        from push_service import _ensure_vapid_keys, _validate_vapid_key

        with patch("push_service._validate_vapid_key") as mock_validate:
            mock_validate.side_effect = lambda k: False
            private, public = _ensure_vapid_keys(db_path)
            assert private is not None

        os.unlink(db_path)
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")
        if os.path.exists(keys_path):
            os.unlink(keys_path)

    def test_ensure_vapid_keys_write_fails(self):
        """When writing the keys file fails, keys are still returned."""
        import os
        import tempfile

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        from push_service import _ensure_vapid_keys

        with patch("builtins.open") as mock_open:
            mock_open.side_effect = OSError("Permission denied")
            private, public = _ensure_vapid_keys(db_path)
            assert private is not None
            assert public is not None

        os.unlink(db_path)
        keys_path = os.path.join(os.path.dirname(db_path), "vapid_keys")
        if os.path.exists(keys_path):
            os.unlink(keys_path)


class TestPushServiceSubscribeUnsubscribe:
    def test_subscribe(self, caplog):
        mock_db = MagicMock()
        service = PushService(mock_db, "priv", "pub")
        with caplog.at_level("INFO"):
            service.subscribe("https://example.com/ep", "p256dh_val", "auth_val", "TestBrowser")
        mock_db.save_push_subscription.assert_called_once_with(
            "https://example.com/ep", "p256dh_val", "auth_val", "TestBrowser"
        )
        assert "Push subscription saved" in caplog.text

    def test_subscribe_no_user_agent(self, caplog):
        mock_db = MagicMock()
        service = PushService(mock_db, "priv", "pub")
        with caplog.at_level("INFO"):
            service.subscribe("https://example.com/ep", "p256dh_val", "auth_val")
        mock_db.save_push_subscription.assert_called_once_with(
            "https://example.com/ep", "p256dh_val", "auth_val", None
        )
        assert "Push subscription saved" in caplog.text

    def test_unsubscribe(self, caplog):
        mock_db = MagicMock()
        service = PushService(mock_db, "priv", "pub")
        with caplog.at_level("INFO"):
            service.unsubscribe("https://example.com/ep")
        mock_db.remove_push_subscription.assert_called_once_with(
            "https://example.com/ep"
        )
        assert "Push subscription removed" in caplog.text


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
        service = PushService(mock_db, _valid_pem(), "pub")
        service.notify_all("Title", "Body")
        mock_db.get_all_push_subscriptions.assert_called_once()

    def test_notify_all_sends_to_subscribers(self):
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep1", "p256dh_key": "k1", "auth_key": "a1"},
            {"endpoint": "https://ep2", "p256dh_key": "k2", "auth_key": "a2"},
        ]
        pem = _valid_pem()
        service = PushService(mock_db, pem, "pub")

        with patch("push_service.webpush") as mock_webpush:
            service.notify_all("Hello", "World", data={"key": "val"})
            assert mock_webpush.call_count == 2
            first_call = mock_webpush.call_args_list[0]
            assert first_call.kwargs["subscription_info"]["endpoint"] == "https://ep1"
            assert isinstance(first_call.kwargs["vapid_private_key"], Vapid01)

    def test_notify_all_removes_gone_subscription(self):
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep-gone", "p256dh_key": "k1", "auth_key": "a1"},
        ]
        service = PushService(mock_db, _valid_pem(), "pub")

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
        service = PushService(mock_db, _valid_pem(), "pub")

        from pywebpush import WebPushException

        with patch("push_service.webpush") as mock_webpush:
            mock_webpush.side_effect = WebPushException("network error")
            service.notify_all("Title", "Body")
            mock_db.remove_push_subscription.assert_not_called()

    def test_notify_all_webpush_unavailable(self):
        """When pywebpush.webpush is None, notify_all returns early."""
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep", "p256dh_key": "k", "auth_key": "a"},
        ]
        service = PushService(mock_db, _valid_pem(), "pub")
        with patch("push_service.webpush", None):
            service.notify_all("Title", "Body")
        mock_db.get_all_push_subscriptions.assert_not_called()

    def test_notify_all_no_vapid_instance(self):
        """When Vapid01 instance is None (invalid PEM), notify_all returns early."""
        mock_db = MagicMock()
        mock_db.get_all_push_subscriptions.return_value = [
            {"endpoint": "https://ep", "p256dh_key": "k", "auth_key": "a"},
        ]
        service = PushService(mock_db, "not-a-valid-pem", "pub")
        assert service._vapid is None
        service.notify_all("Title", "Body")
        mock_db.get_all_push_subscriptions.assert_not_called()
