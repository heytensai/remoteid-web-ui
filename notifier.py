"""Notification dispatcher — routes alert events to configured targets.

Each target type has a directory at ``templates/notifications/<type>/``
containing Jinja2 templates for each event (``alert.j2``, ``new_session.j2``).
The rendered output is dispatched differently per type:

- **push**: the template renders a two-line ``title: ...\\nbody: ...`` string
  that is parsed and sent via ``PushService.notify_all()``.
- **discord**: the template renders a JSON payload that is POSTed to the
  configured ``webhook_url``.
"""

import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from jinja2 import Template, TemplateError

logger = logging.getLogger(__name__)

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates", "notifications")

VALID_EVENTS = ("alert", "new_session")


def _render_push(template: Template, ctx: dict) -> dict:
    """Render a push template and extract title/body.

    The template produces two lines::

        title: Geozone Alert
        body: ...

    Returns a dict ``{"title": ..., "body": ...}``.
    """
    text = template.render(**ctx)
    title = ""
    body = ""
    for line in text.strip().splitlines():
        if line.startswith("title:"):
            title = line[len("title:"):].strip()
        elif line.startswith("body:"):
            body = line[len("body:"):].strip()
    return {"title": title, "body": body}


DISCORD_USER_AGENT = \
    "RemoteID-WebUI-Notifier/1.0 (+https://github.com/zmonkey/remoteid-web-ui)"


def _send_discord(webhook_url: str, payload: str):
    """POST a JSON payload to a Discord webhook URL."""
    data = payload.encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
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


class NotifierService:
    """Loads notification templates and dispatches events to all configured targets."""

    def __init__(
        self,
        notifications: List,
        server_url: str,
        url_prefix: str = "",
        push_service=None,
    ):
        base = server_url.rstrip("/")
        prefix = url_prefix.rstrip("/")
        self._server_url = base + prefix
        self._push_service = push_service
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

        ``event`` is ``"alert"`` or ``"new_session"``.
        Extra keyword arguments are passed to the template context.

        Templates are cached in memory and automatically reloaded from
        disk when their file modification time changes.
        """
        ctx.setdefault("server_url", self._server_url)
        ctx.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

        for nt in self._targets:
            if event not in nt.events:
                continue
            key = f"{nt.type}/{event}"
            self._refresh_template_if_changed(key)
            template = self._templates.get(key)
            if template is None:
                continue

            try:
                if nt.type == "push":
                    rendered = _render_push(template, ctx)
                    if self._push_service:
                        self._push_service.notify_all(
                            rendered["title"],
                            rendered["body"],
                            data=ctx,
                        )
                    else:
                        logger.debug("Push service unavailable, skipping push target %r", nt.name)
                elif nt.type == "discord":
                    payload = template.render(**ctx)
                    logger.debug("Discord payload for %s: %s", nt.name, payload)
                    _send_discord(nt.webhook_url, payload)
                else:
                    logger.warning("Unknown notification type %r for target %r", nt.type, nt.name)
            except TemplateError as e:
                logger.warning("Template rendering failed for %s/%s: %s", nt.type, event, e)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Notification dispatch failed for %s/%s", nt.type, event)
