from __future__ import annotations

import ssl
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def certifi_ssl_context() -> ssl.SSLContext:
    """Return a reusable TLS context backed by the CA bundle shipped with the app."""

    return ssl.create_default_context(cafile=certifi.where())
