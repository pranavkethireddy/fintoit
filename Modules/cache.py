"""
Tiny in-process cache for expensive per-company computations.

Why: the dashboard recomputes burn, runway, KPIs, anomalies and a 6-month
forecast from the company's full transaction list on every page load, even when
nothing changed. This caches the result briefly and invalidates it the moment
any transaction is written.

Correctness model
-----------------
Each company has a version counter. Every write through TransactionDB calls
bump(company_id), which invalidates that company's cached entries immediately.
Entries also carry a short TTL as a backstop.

IMPORTANT (multi-worker): this cache lives in one process. If the app runs with
several Gunicorn workers, a write handled by worker A does not invalidate
worker B's copy — worker B serves its cached value until the TTL expires
(default 30s). That is why the TTL is deliberately short. To disable caching
entirely, set METRICS_CACHE_TTL=0.
"""

import os
import threading
import time

_LOCK = threading.RLock()
_VERSIONS = {}   # company_id -> int
_ENTRIES = {}    # (namespace, company_id) -> (version, expires_at, value)

# Cache lifetime in seconds. 0 disables caching completely.
DEFAULT_TTL = int(os.getenv("METRICS_CACHE_TTL", "30"))


def bump(company_id):
    """Invalidate every cached value for a company. Call after any write."""
    if not company_id:
        return
    cid = str(company_id)
    with _LOCK:
        _VERSIONS[cid] = _VERSIONS.get(cid, 0) + 1
        for key in [k for k in _ENTRIES if k[1] == cid]:
            _ENTRIES.pop(key, None)


def get_or_set(namespace, company_id, producer, ttl=None):
    """Return a cached value for (namespace, company_id), computing it if needed.

    `producer` is a zero-arg callable that does the expensive work. It runs
    outside the lock so slow work never blocks other requests.
    """
    ttl = DEFAULT_TTL if ttl is None else ttl
    if not company_id or ttl <= 0:
        return producer()

    cid = str(company_id)
    key = (namespace, cid)
    now = time.time()

    with _LOCK:
        version = _VERSIONS.get(cid, 0)
        hit = _ENTRIES.get(key)
        if hit and hit[0] == version and hit[1] > now:
            return hit[2]

    value = producer()

    with _LOCK:
        # Only store if no write landed while we were computing, otherwise we'd
        # cache data that is already stale.
        if _VERSIONS.get(cid, 0) == version:
            _ENTRIES[key] = (version, time.time() + ttl, value)
    return value


def clear():
    """Drop everything (used by tests)."""
    with _LOCK:
        _VERSIONS.clear()
        _ENTRIES.clear()
