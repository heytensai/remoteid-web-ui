"""Gunicorn configuration for Remote ID Web UI.

Uses ``--preload`` so the app is imported once in the master process.
Background threads (session detector) start in the master
and do **not** survive ``fork()`` into workers — each worker inherits a
dead copy, so only one instance of each DB-bound service runs.

Each worker starts its own config-file watcher via ``post_fork`` — config
watching updates in-process memory, so each worker needs an independent copy.
"""
# pylint: disable=unused-argument

from app import start_background_services, start_config_watcher

bind = "0.0.0.0:5000"
workers = 2
access_logfile = "-"
preload_app = True


def when_ready(server):
    """Start background threads in the master process.

    Called after the preloaded app is ready but before workers fork.
    Threads (session detection, config watcher) run only here —
    they don't survive ``fork()``.
    """
    start_background_services()


def post_fork(server, worker):
    """Start a per-worker config-file watcher.

    Workers inherit dead copies of the master's background threads.
    A fresh config watcher is needed because ``reload_hot_config``
    updates in-process memory only.
    """
    start_config_watcher()
