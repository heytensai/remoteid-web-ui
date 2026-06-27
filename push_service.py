"""Push notification service for Web Push API (VAPID)."""

import base64
import json
import logging
import os

logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException
    from pywebpush import Vapid01
    from cryptography.hazmat.primitives import serialization
except ImportError as e:
    logger.error("pywebpush import failed: %s", e)
    webpush = None
    WebPushException = Exception
    Vapid01 = None
    serialization = None


def _vapid_keys_path(database_path):
    """Derive VAPID keys file path from the database path.

    Places vapid_keys alongside the database file so it lives in the
    same persistent volume (critical for Docker deployments).
    """
    db_dir = os.path.dirname(os.path.abspath(database_path)) if database_path else "."
    return os.path.join(db_dir, "vapid_keys")


def _vapid_public_b64url(vapid_instance):
    """Extract the base64url-encoded public key from a Vapid01 instance."""
    pub_bytes = vapid_instance.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")


def _validate_vapid_key(private_pem: str) -> bool:
    """Check that a VAPID private key PEM can be loaded by ``cryptography``."""
    if serialization is None:
        return True
    try:
        key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
        key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("VAPID private key validation failed", exc_info=True)
        return False


def _ensure_vapid_keys(database_path=None):
    """Load VAPID keys from file or generate new ones.

    Keys are stored alongside the database (or current directory if
    *database_path* is ``None``) so they survive container rebuilds
    when the database directory is a mounted volume.

    Returns (private_key_pem, public_key_b64url) tuple,
    or (None, None) if pywebpush is not installed.
    """
    if Vapid01 is None:
        logger.warning("pywebpush not available — push notifications disabled")
        return None, None

    keys_file = _vapid_keys_path(database_path)
    logger.info("VAPID keys file path: %s (database_path=%s)", keys_file, database_path)

    if os.path.exists(keys_file):
        logger.info("VAPID keys file exists, loading")
        try:
            with open(keys_file, encoding="utf-8") as f:
                data = json.load(f)
            private = data.get("private_key")
            public = data.get("public_key")
            if private and public and _validate_vapid_key(private):
                logger.info("Loaded existing VAPID keys from %s", keys_file)
                return private, public
            logger.warning("VAPID keys file %s is invalid, regenerating", keys_file)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read VAPID keys file %s: %s", keys_file, e)

    logger.info("Generating new VAPID keys")
    v = Vapid01()
    v.generate_keys()
    private_pem = v.private_pem().decode()
    public_b64url = _vapid_public_b64url(v)
    logger.info("VAPID keys generated (private=%d chars, public=%d chars)",
                len(private_pem), len(public_b64url))
    if not _validate_vapid_key(private_pem):
        logger.warning("Freshly generated VAPID key failed validation — push will be broken")
    try:
        with open(keys_file, "w", encoding="utf-8") as f:
            json.dump({"private_key": private_pem, "public_key": public_b64url}, f)
        os.chmod(keys_file, 0o600)
        logger.info("VAPID keys written to %s", keys_file)
    except OSError as e:
        logger.warning("Failed to write VAPID keys file %s: %s", keys_file, e)

    return private_pem, public_b64url


class PushService:
    """Manages push notification subscriptions and sends Web Push messages."""

    def __init__(self, db, vapid_private_key, vapid_public_key, email="admin@drone-tracker.local"):
        self._db = db
        self._vapid_public_key = vapid_public_key
        self._claims = {"sub": f"mailto:{email}"}
        # Pre-convert PEM to DER so the installed py_vapid's from_string →
        # from_der path works correctly (some versions don't decode PEM properly).
        self._vapid_private_key = vapid_private_key
        if vapid_private_key and serialization is not None:
            try:
                key = serialization.load_pem_private_key(
                    vapid_private_key.encode("utf-8"), password=None
                )
                self._vapid_private_key = key.private_bytes(
                    encoding=serialization.Encoding.DER,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to pre-parse VAPID private key, falling back to raw PEM")

    def subscribe(self, endpoint, p256dh_key, auth_key, user_agent=None):
        """Store a push subscription."""
        self._db.save_push_subscription(endpoint, p256dh_key, auth_key, user_agent)
        logger.info("Push subscription saved (endpoint=%.20s...)", endpoint)

    def unsubscribe(self, endpoint):
        """Remove a push subscription."""
        self._db.remove_push_subscription(endpoint)
        logger.info("Push subscription removed")

    def notify_all(self, title, body, data=None):
        """Send a push notification to all subscribers."""
        if webpush is None:
            logger.debug("pywebpush not available, skipping push")
            return

        payload = json.dumps({
            "title": title,
            "body": body,
            "data": data or {},
        })

        subs = self._db.get_all_push_subscriptions()
        if not subs:
            return

        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {
                            "p256dh": sub["p256dh_key"],
                            "auth": sub["auth_key"],
                        },
                    },
                    data=payload,
                    vapid_private_key=self._vapid_private_key,
                    vapid_claims=self._claims,
                )
            except WebPushException as e:  # pylint: disable=broad-exception-caught
                # When pywebpush is absent, WebPushException = Exception but
                # the except branch is unreachable (guard at top of method).
                if getattr(e, 'response', None) and e.response.status_code == 410:
                    self._db.remove_push_subscription(sub["endpoint"])
                    logger.info("Removed expired push subscription (410 Gone)")
                else:
                    logger.warning("Push send failed: %s", e)
