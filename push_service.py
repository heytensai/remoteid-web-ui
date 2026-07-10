"""Push notification service for Web Push API (VAPID)."""

import base64
import json
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from pywebpush import webpush, WebPushException
    from pywebpush import Vapid01, Vapid
    from cryptography.hazmat.primitives import serialization
except ImportError as e:
    logger.error("pywebpush import failed: %s", e)
    webpush = None
    WebPushException = Exception
    Vapid01 = None
    Vapid = None
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
        self._vapid_private_key = vapid_private_key
        # pywebpush's ``webpush()`` calls ``Vapid.from_string()`` which does
        # NOT handle PEM headers (it base64-decodes the raw input).  Pre-parse
        # the PEM into a ``Vapid`` (Vapid02 / RFC8292) instance so
        # ``webpush()`` takes the ``isinstance(vapid_private_key, Vapid01)``
        # fast-path at line 547 and uses RFC8292-compatible signing.
        if vapid_private_key and Vapid is not None:
            try:
                self._vapid = Vapid.from_pem(vapid_private_key.encode("utf-8"))
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to create Vapid instance from PEM", exc_info=True)
                self._vapid = None
        else:
            self._vapid = None

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
        if self._vapid is None:
            logger.debug("VAPID instance not available, skipping push")
            return

        payload = json.dumps({
            "title": title,
            "body": body,
            "data": data or {},
        })

        subs = self._db.get_all_push_subscriptions()
        if not subs:
            logger.debug("No push subscriptions to notify")
            return

        logger.info("Sending push notification to %d subscription(s)", len(subs))
        for sub in subs:
            try:
                if logger.isEnabledFor(logging.DEBUG):
                    url = urlparse(sub["endpoint"])
                    aud = f"{url.scheme}://{url.netloc}"
                    logger.debug(
                        "Sending push to endpoint=%s... aud=%s",
                        sub["endpoint"][:60], aud,
                    )
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {
                            "p256dh": sub["p256dh_key"],
                            "auth": sub["auth_key"],
                        },
                    },
                    data=payload,
                    vapid_private_key=self._vapid,
                    vapid_claims=self._claims,
                )
                logger.info("Push sent to %s...", sub["endpoint"][:60])
            except WebPushException as e:  # pylint: disable=broad-exception-caught
                # When pywebpush is absent, WebPushException = Exception but
                # the except branch is unreachable (guard at top of method).
                status = None
                if hasattr(e, 'response') and e.response is not None:
                    status = getattr(e.response, 'status_code', None)
                    logger.debug("Push failed with status=%s", status)
                if status in (410, 403) or '410' in str(e):
                    self._db.remove_push_subscription(sub["endpoint"])
                    logger.info(
                        "Removed push subscription (%s)", status or "410"
                    )
                else:
                    logger.warning("Push send failed: %s", e)
