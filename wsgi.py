"""WSGI entrypoint for gunicorn in Docker"""
import os

from app import _init_app

config_path = os.environ.get("APP_CONFIG", "/app/config/web_config.yaml")
application = _init_app(config_path)
