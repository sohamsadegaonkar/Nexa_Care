"""Run one process behind an HTTPS reverse proxy, with a persistent journal."""

import os

from aiohttp import web

from .api import create_app

if __name__ == "__main__":
    web.run_app(
        create_app(),
        host=os.environ.get("GATEWAY_BIND", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        access_log=None,
        print=None,
    )
