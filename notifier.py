"""Notification dispatcher — routes events to configured targets.

Each target type has a directory at ``templates/notifications/<type>/``
containing Jinja2 templates for each event (``geozone_enter.j2``, ``new_session.j2``).
The rendered output is dispatched differently per type:

- **discord**: the template renders a JSON payload that is POSTed to the
  configured ``webhook_url``.
- **ntfy**: the template renders the plain-text message body. Metadata
  (title, priority, tags, click URL) is sent as ntfy HTTP headers.
- **teams**: the template renders an Adaptive Card JSON payload that is
  POSTed to the configured Incoming Webhook URL.
"""

import base64
import http.client
import logging
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urlparse

from jinja2 import Template, TemplateError

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates", "notifications")

VALID_EVENTS = ("geozone_enter", "new_session")

_NTFY_EVENT_HEADERS = {
    "geozone_enter": {
        "title": "Geozone Alert",
        "priority": 4,
        "tags": "warning,drone",
    },
    "new_session": {
        "title": "New Flight Detected",
        "priority": 3,
        "tags": "drone",
    },
}


NOTIFIER_USER_AGENT = \
    "RemoteID-WebUI-Notifier/1.0 (+https://github.com/zmonkey/remoteid-web-ui)"


def _send_discord(webhook_url: str, payload: str):
    """POST a JSON payload to a Discord webhook URL."""
    data = payload.encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": NOTIFIER_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            logger.debug("Discord webhook response %s: %s", resp.status, resp_body)
            if not 200 <= resp.status < 300:
                logger.warning(
                    "Discord webhook returned %s: %s", resp.status, resp_body
                )
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        logger.warning(
            "Discord webhook HTTP %s (headers=%s): body=%s",
            e.code, dict(e.headers), resp_body,
        )
    except urllib.error.URLError as e:
        logger.warning("Discord webhook connection failed: %s", e.reason)


def _send_teams(webhook_url: str, payload: str, token: str = ""):
    """POST a JSON payload to a Microsoft Teams Incoming Webhook URL.

    Optionally sends a Bearer token for tenant-restricted webhooks.
    """
    data = payload.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": NOTIFIER_USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            logger.debug("Teams webhook response %s: %s", resp.status, resp_body)
            if not 200 <= resp.status < 300:
                logger.warning(
                    "Teams webhook returned %s: %s", resp.status, resp_body,
                )
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        logger.warning(
            "Teams webhook HTTP %s (headers=%s): body=%s",
            e.code, dict(e.headers), resp_body,
        )
    except urllib.error.URLError as e:
        logger.warning("Teams webhook connection failed: %s", e.reason)


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that forces IPv4 to avoid Docker IPv6 issues."""

    def connect(self):
        host = self.host
        port = self.port or 443
        # Resolve to IPv4 only, bypassing IPv6
        addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if not addrs:
            raise OSError(f"DNS lookup for {host} returned no IPv4 addresses")
        err = None
        for family, socktype, proto, _, sockaddr in addrs:
            try:
                raw = socket.socket(family, socktype, proto)
                raw.settimeout(self.timeout)
                raw.connect(sockaddr)
                if self._tunnel_host:
                    self._tunnel()
                self.sock = self._context.wrap_socket(raw, server_hostname=host)
                return
            except OSError as exc:
                err = exc
                if raw:
                    raw.close()
        raise err or OSError(f"Could not connect to {host}:{port}")


class _IPv4HTTPHandler(urllib.request.HTTPSHandler):
    """HTTPS handler that forces IPv4 connections."""

    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req)


def _send_ntfy(webhook_url: str, payload: str, token: str = "",  # pylint: disable=too-many-arguments,too-many-positional-arguments
               username: str = "", password: str = "",
               title: str = "", priority: int = 0,
               tags: str = "", click_url: str = ""):
    """POST a plain-text message to an ntfy publish URL.

    ``payload`` is the message body (plain text). Metadata is sent as
    ntfy HTTP headers: ``Title``, ``Priority``, ``Tags``, ``Click``.

    Supports two auth methods (mutually exclusive):
    - Bearer token: set ``token``
    - Basic auth: set ``username`` and ``password``
    """
    parsed = urlparse(webhook_url)
    logger.debug("ntfy target: %s://%s (scheme=%s, port=%s)",
                 parsed.scheme, parsed.hostname, parsed.scheme,
                 parsed.port or (443 if parsed.scheme == "https" else 80))

    # DNS resolution debug
    try:
        addrinfos = socket.getaddrinfo(
            parsed.hostname, parsed.port or 443,
            socket.AF_UNSPEC, socket.SOCK_STREAM,
        )
        family_names = {socket.AF_INET: "IPv4", socket.AF_INET6: "IPv6"}
        resolved = ", ".join(
            f"{family_names.get(ai[0], ai[0])}: {ai[4][0]}"
            for ai in addrinfos
        )
        logger.debug("ntfy DNS resolved %s -> %s", parsed.hostname, resolved)
    except socket.gaierror as e:
        logger.warning("ntfy DNS resolution failed for %s: %s", parsed.hostname, e)

    data = payload.encode("utf-8")
    headers = {
        "User-Agent": NOTIFIER_USER_AGENT,
    }
    if username:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    if title:
        headers["Title"] = title
    if priority:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = tags
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_IPv4HTTPHandler)
        with opener.open(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            logger.debug("ntfy response %s: %s", resp.status, resp_body)
            if not 200 <= resp.status < 300:
                logger.warning(
                    "ntfy returned %s: %s", resp.status, resp_body,
                )
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        logger.warning(
            "ntfy HTTP %s (headers=%s): body=%s",
            e.code, dict(e.headers), resp_body,
        )
    except urllib.error.URLError as e:
        logger.warning("ntfy connection failed: %s", e.reason)
        _log_connection_debug(parsed)
    except OSError as e:
        logger.warning("ntfy socket error: %s (errno=%s)", e, e.errno)
        _log_connection_debug(parsed)


def _log_connection_debug(parsed):
    """Log diagnostics when ntfy connection fails."""
    logger.debug("ntfy diagnostics: Python %s, SSL %s",
                 sys.version.split()[0], ssl.OPENSSL_VERSION)
    logger.debug("ntfy diagnostics: URL=%s:%s, host=%s",
                 parsed.scheme, parsed.port or "default", parsed.hostname)
    # Try connecting manually to isolate the failure
    for family_name, family in [("IPv4", socket.AF_INET),
                                 ("IPv6", socket.AF_INET6)]:
        try:
            infos = socket.getaddrinfo(
                parsed.hostname, parsed.port or 443,
                family, socket.SOCK_STREAM,
            )
            for info in infos:
                sockaddr = info[4]
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(5)
                try:
                    sock.connect(sockaddr)
                    logger.debug("ntfy diagnostics: %s %s connect OK",
                                 family_name, sockaddr[0])
                except OSError as e:
                    logger.debug("ntfy diagnostics: %s %s connect failed: %s",
                                 family_name, sockaddr[0], e)
                finally:
                    sock.close()
        except socket.gaierror:
            logger.debug("ntfy diagnostics: %s DNS failed", family_name)


class NotifierService:
    """Loads notification templates and dispatches events to all configured targets."""

    def __init__(
        self,
        notifications: List,
        server_url: str,
        url_prefix: str = "",
    ):
        base = server_url.rstrip("/")
        prefix = url_prefix.rstrip("/")
        self._server_url = base + prefix
        self._targets: List[Dict] = []
        self._templates: Dict[str, Template] = {}
        self._template_mtimes: Dict[str, float] = {}
        self._template_paths: Dict[str, str] = {}
        self._load_templates()

        for nt in notifications:
            target_type = nt.type
            for event in nt.events:
                key = f"{target_type}/{event}"
                if key not in self._templates:
                    logger.warning(
                        "No template for %s (target %r), skipping",
                        key, nt.name,
                    )
                    continue
            self._targets.append(nt)
            logger.info(
                "Registered notifier %r (type=%s, events=%s)",
                nt.name, nt.type, nt.events,
            )

        if not self._targets:
            logger.info("No notification targets configured — notifications disabled")

    def has_targets(self) -> bool:
        """Return True if any notification targets are configured."""
        return bool(self._targets)

    def _load_templates(self):
        """Walk ``templates/notifications/<type>/<event>.j2`` and load each."""
        if not os.path.isdir(TEMPLATE_DIR):
            logger.warning("Notification template directory not found: %s", TEMPLATE_DIR)
            return
        for type_name in os.listdir(TEMPLATE_DIR):
            type_dir = os.path.join(TEMPLATE_DIR, type_name)
            if not os.path.isdir(type_dir):
                continue
            for fname in os.listdir(type_dir):
                if not fname.endswith(".j2"):
                    continue
                event_name = fname[:-3]  # strip ".j2"
                if event_name not in VALID_EVENTS:
                    logger.warning("Unknown event template %s/%s, skipping", type_name, fname)
                    continue
                fpath = os.path.join(type_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        src = fh.read()
                    self._templates[f"{type_name}/{event_name}"] = Template(src)
                    self._template_paths[f"{type_name}/{event_name}"] = fpath
                    self._template_mtimes[f"{type_name}/{event_name}"] = os.path.getmtime(fpath)
                    logger.debug("Loaded template %s", fpath)
                except OSError as e:
                    logger.warning("Failed to read template %s: %s", fpath, e)
                except TemplateError as e:
                    logger.warning("Invalid template %s: %s", fpath, e)

    def _refresh_template_if_changed(self, key: str):
        """Reload template from disk if the file's mtime has changed."""
        fpath = self._template_paths.get(key)
        if not fpath:
            return
        try:
            mtime = os.path.getmtime(fpath)
        except OSError:
            return
        cached = self._template_mtimes.get(key)
        if cached is not None and mtime <= cached:
            return
        try:
            with open(fpath, encoding="utf-8") as fh:
                src = fh.read()
            self._templates[key] = Template(src)
            self._template_mtimes[key] = mtime
            logger.info("Reloaded template %s (mtime changed)", fpath)
        except (OSError, TemplateError) as e:
            logger.warning("Failed to reload template %s: %s", fpath, e)

    def dispatch(self, event: str, **ctx):
        """Dispatch an event to all configured targets that listen for it.

        ``event`` is ``"geozone_enter"`` or ``"new_session"``.
        Extra keyword arguments are passed to the template context.

        Templates are cached in memory and automatically reloaded from
        disk when their file modification time changes.
        """
        ctx.setdefault("server_url", self._server_url)
        ctx.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

        for nt in self._targets:
            if not nt.enabled:
                continue
            if event not in nt.events:
                continue
            key = f"{nt.type}/{event}"
            self._refresh_template_if_changed(key)
            template = self._templates.get(key)
            if template is None:
                continue

            try:
                if nt.type == "discord":
                    payload = template.render(**ctx)
                    logger.debug("Discord payload for %s: %s", nt.name, payload)
                    _send_discord(nt.webhook_url, payload)
                    logger.info("Sent %s notification via %s (%s)", event, nt.type, nt.name)
                elif nt.type == "ntfy":
                    payload = template.render(**ctx)
                    ntfy_headers = dict(_NTFY_EVENT_HEADERS.get(event, {}))
                    if ctx.get("server_url"):
                        ntfy_headers["click_url"] = ctx["server_url"]
                    logger.debug("ntfy payload for %s: %s", nt.name, payload)
                    _send_ntfy(nt.webhook_url, payload, token=nt.token,
                               username=nt.username, password=nt.password,
                               **ntfy_headers)
                    logger.info("Sent %s notification via %s (%s)", event, nt.type, nt.name)
                elif nt.type == "teams":
                    payload = template.render(**ctx)
                    logger.debug("Teams payload for %s: %s", nt.name, payload)
                    _send_teams(nt.webhook_url, payload, token=nt.token)
                    logger.info("Sent %s notification via %s (%s)", event, nt.type, nt.name)
                else:
                    logger.warning("Unknown notification type %r for target %r", nt.type, nt.name)
            except TemplateError as e:
                logger.warning("Template rendering failed for %s/%s: %s", nt.type, event, e)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Notification dispatch failed for %s/%s", nt.type, event)
