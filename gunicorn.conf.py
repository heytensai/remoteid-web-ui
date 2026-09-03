"""Gunicorn configuration for Remote ID Web UI.

Uses ``--preload`` so the app is imported once in the master process.
Background threads (session detector) start in the master
and do **not** survive ``fork()`` into workers — each worker inherits a
dead copy, so only one instance of each DB-bound service runs.

Each worker starts its own config-file watcher via ``post_fork`` — config
watching updates in-process memory, so each worker needs an independent copy.
"""
# pylint: disable=unused-argument

import app
from app import start_background_services, start_config_watcher

bind = "0.0.0.0:5000"  # pylint: disable=invalid-name
workers = 2  # pylint: disable=invalid-name
max_requests = 10000  # pylint: disable=invalid-name
max_requests_jitter = 2000  # pylint: disable=invalid-name
access_logfile = "-"  # pylint: disable=invalid-name
preload_app = True  # pylint: disable=invalid-name


def when_ready(server):
    """Start background threads in the master process.

    Called after the preloaded app is ready but before workers fork.
    Threads (session detection, config watcher) run only here —
    they don't survive ``fork()``.
    """
    start_background_services()


def post_fork(server, worker):
    """Give the worker a fresh DB pool and start a per-worker config-file watcher.

    ``reset_pool()`` replaces the inherited pool with a brand-new one so this
    worker never reuses the master's shared sockets or their inherited locks
    (which may be held by a forked background thread and deadlock the worker).

    ``DATABASE`` is resolved here (not at import time) because gunicorn
    imports this config before ``wsgi:application`` runs ``_init_app``,
    so the module-level ``DATABASE`` is still ``None`` during ``import``.

    A fresh config watcher is also needed because ``reload_hot_config``
    updates in-process memory only.
    """
    if app.DATABASE is not None:
        app.DATABASE.reset_pool()
    start_config_watcher()
