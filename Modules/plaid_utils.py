"""
Plaid integration for Fintoit.

Handles Link token creation, public-token exchange, transaction sync, item
(bank connection) management, and signed-webhook verification/handling.

Access tokens are live bank credentials, so they are encrypted at rest with
Fernet (PLAID_ENCRYPTION_KEY) — a materially higher sensitivity bar than the
other secrets stored by this app.

DB access goes through Modules.database.Database, which uses a RealDictCursor,
so every row is a dict keyed by column name (never a positional tuple):
    Database.execute(sql, params)              -> list[dict]  (SELECT; fetch=True default)
    Database.execute(sql, params, fetch=False) -> None        (INSERT/UPDATE/DELETE/DDL)
    Database.execute_one(sql, params)          -> dict | None  (single row / INSERT ... RETURNING)

company_id is a UUID string throughout this app (see database.DEMO_COMPANY_ID),
so it is stored as TEXT.

Tables are created lazily via _ensure_tables() (a no-op after the first run),
matching the convention used by the other integration modules here.

Public API (all app-facing functions return JSON-serialisable dicts and never
raise to the caller):
    create_link_token(company_id)                 -> {"link_token": ...} | {"error": ...}
    exchange_public_token(company_id, public_token, institution_name=None)
                                                  -> {"item_id": ..., "institution_name": ...} | {"error": ...}
    get_items_for_company(company_id)             -> [{"item_id", "institution_name", "status", "needs_reconnect"}]
    disconnect_item(company_id, item_id)          -> {"status": "disconnected"} | {"error": ...}
    sync_transactions(company_id)                 -> {"added", "modified", "removed", "items"}
    verify_webhook(raw_body: bytes, header: str)  -> bool
    handle_webhook(payload: dict)                 -> dict
"""

import os
import json
import time
import hmac
import hashlib
import logging

from cryptography.fernet import Fernet

import plaid
from plaid.api import plaid_api
from plaid.exceptions import ApiException
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.webhook_verification_key_get_request import WebhookVerificationKeyGetRequest

import jwt
from jwt.algorithms import ECAlgorithm

from Modules.database import Database

logger = logging.getLogger(__name__)

PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID")
PLAID_SECRET = os.environ.get("PLAID_SECRET")
PLAID_ENV = os.environ.get("PLAID_ENV", "sandbox")  # sandbox | production
PLAID_ENCRYPTION_KEY = os.environ.get("PLAID_ENCRYPTION_KEY")
PLAID_WEBHOOK_URL = os.environ.get("PLAID_WEBHOOK_URL")  # e.g. https://app.fintoit.com/plaid/webhook

# Plaid retired the legacy "development" environment; only sandbox and
# production remain. Unknown values fall back to sandbox (fail safe).
_ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

_WEBHOOK_MAX_AGE_SECONDS = 5 * 60  # replay-protection window per Plaid's docs


# ---------------------------------------------------------------------------
# Clients / crypto helpers
# ---------------------------------------------------------------------------

def _get_client():
    if not PLAID_CLIENT_ID or not PLAID_SECRET:
        raise RuntimeError("PLAID_CLIENT_ID / PLAID_SECRET not set in environment")
    host = _ENV_MAP.get(PLAID_ENV)
    if host is None:
        logger.warning("Unknown PLAID_ENV=%r; defaulting to sandbox", PLAID_ENV)
        host = plaid.Environment.Sandbox
    configuration = plaid.Configuration(
        host=host,
        api_key={"clientId": PLAID_CLIENT_ID, "secret": PLAID_SECRET},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def _get_fernet():
    if not PLAID_ENCRYPTION_KEY:
        raise RuntimeError(
            "PLAID_ENCRYPTION_KEY not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    key = PLAID_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    return _get_fernet().decrypt(value.encode()).decode()


def _plaid_error_message(exc: ApiException) -> str:
    """Extract a user-safe message from a Plaid ApiException without leaking internals."""
    try:
        body = json.loads(exc.body)
        return body.get("display_message") or body.get("error_message") or "Plaid request failed."
    except Exception:
        return "Plaid request failed."


# ---------------------------------------------------------------------------
# Schema (lazy, migration-free — matches the rest of this codebase)
# ---------------------------------------------------------------------------

_tables_ready = False


def _ensure_tables():
    global _tables_ready
    if _tables_ready:
        return

    # company_id is a UUID string in this app, so TEXT (not INTEGER).
    Database.execute(
        """
        CREATE TABLE IF NOT EXISTS plaid_items (
            id SERIAL PRIMARY KEY,
            company_id TEXT NOT NULL,
            item_id TEXT UNIQUE NOT NULL,
            access_token TEXT NOT NULL,
            institution_name TEXT,
            cursor TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """,
        fetch=False,
    )

    # Additive columns on the existing transactions table (same idiom used
    # elsewhere for evolving shared tables without a migration framework).
    for column, coltype in [
        ("plaid_transaction_id", "TEXT"),
        ("plaid_account_id", "TEXT"),
        ("plaid_item_id", "TEXT"),
        ("source", "TEXT DEFAULT 'manual'"),
    ]:
        try:
            Database.execute(
                f"ALTER TABLE transactions ADD COLUMN IF NOT EXISTS {column} {coltype}",
                fetch=False,
            )
        except Exception as e:
            logger.warning("Could not add column %s to transactions: %s", column, e)

    # Unique index guards against duplicate Plaid transactions. (The upsert in
    # _upsert_transaction checks by plaid_transaction_id before inserting rather
    # than relying on ON CONFLICT, which cannot infer this partial index.)
    try:
        Database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_transactions_plaid_txn "
            "ON transactions (plaid_transaction_id) WHERE plaid_transaction_id IS NOT NULL",
            fetch=False,
        )
    except Exception as e:
        logger.warning("Could not create unique index on transactions.plaid_transaction_id: %s", e)

    _tables_ready = True


# ---------------------------------------------------------------------------
# Link flow
# ---------------------------------------------------------------------------

def create_link_token(company_id, user_email: str = None) -> dict:
    """Create a Plaid Link token so the frontend can open Link for this company."""
    try:
        client = _get_client()
        request = LinkTokenCreateRequest(
            products=[Products("transactions")],
            client_name="Fintoit",
            country_codes=[CountryCode("US")],
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id=str(company_id)),
        )
        # `webhook` is a plain optional string field on the request itself.
        if PLAID_WEBHOOK_URL:
            request.webhook = PLAID_WEBHOOK_URL
        response = client.link_token_create(request)
        return {"link_token": response.to_dict()["link_token"]}
    except ApiException as e:
        logger.error("Plaid link_token_create failed: %s", e)
        return {"error": _plaid_error_message(e)}
    except Exception:
        logger.exception("create_link_token failed")
        return {"error": "Could not start Plaid Link. Please try again."}


def exchange_public_token(company_id, public_token: str, institution_name: str = None) -> dict:
    """Exchange a public_token (from Link) for a permanent access_token, encrypt
    it, store it against this company, and kick off an initial sync."""
    _ensure_tables()
    try:
        client = _get_client()
        exchange = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token)
        ).to_dict()
        access_token = exchange["access_token"]
        item_id = exchange["item_id"]
    except ApiException as e:
        logger.error("Plaid public_token exchange failed: %s", e)
        return {"error": _plaid_error_message(e)}
    except Exception:
        logger.exception("exchange_public_token failed")
        return {"error": "Could not connect your bank. Please try again."}

    encrypted_token = _encrypt(access_token)
    Database.execute(
        """
        INSERT INTO plaid_items (company_id, item_id, access_token, institution_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (item_id) DO UPDATE SET
            access_token = EXCLUDED.access_token,
            institution_name = COALESCE(EXCLUDED.institution_name, plaid_items.institution_name),
            company_id = EXCLUDED.company_id,
            status = 'active',
            updated_at = NOW()
        """,
        (str(company_id), item_id, encrypted_token, institution_name),
        fetch=False,
    )

    # Initial sync so the founder sees data immediately. Never let a sync hiccup
    # fail the whole connection — the item is stored, and webhooks / manual sync
    # will catch up.
    try:
        sync_transactions_for_item(item_id)
    except Exception:
        logger.exception("Initial sync failed for item %s; webhook/manual sync will retry", item_id)

    return {"item_id": item_id, "institution_name": institution_name}


# ---------------------------------------------------------------------------
# Transaction sync
# ---------------------------------------------------------------------------

def _normalize_amount(plaid_amount: float):
    """Plaid convention: positive = money OUT (expense), negative = money IN (income).
    Fintoit stores amount as always-positive with a separate 'income'/'expense'
    type (what the burn-rate calculator expects), so we flip the sign once here.
    """
    if plaid_amount > 0:
        return abs(plaid_amount), "expense"
    return abs(plaid_amount), "income"


def _extract_category(txn: dict):
    pfc = txn.get("personal_finance_category")
    if pfc:
        return pfc.get("primary")
    cats = txn.get("category")
    if cats:
        return cats[0]
    return None


def _upsert_transaction(company_id, item_id: str, txn: dict):
    amount, txn_type = _normalize_amount(txn["amount"])
    category = _extract_category(txn)
    txn_id = txn["transaction_id"]
    # Manual upsert (SELECT then INSERT/UPDATE). NOTE: do NOT use
    # `ON CONFLICT (plaid_transaction_id)` here — the unique index is *partial*
    # (WHERE plaid_transaction_id IS NOT NULL), and Postgres can't infer a partial
    # index for ON CONFLICT unless the same predicate is given, so it raised 42P10
    # on every insert and silently synced zero transactions.
    existing = Database.execute_one(
        "SELECT id FROM transactions WHERE plaid_transaction_id = %s AND company_id = %s",
        (txn_id, str(company_id)),
    )
    if existing:
        Database.execute(
            """
            UPDATE transactions
               SET amount = %s, type = %s, category = %s, description = %s,
                   date = %s, plaid_account_id = %s, source = 'plaid'
             WHERE plaid_transaction_id = %s AND company_id = %s
            """,
            (amount, txn_type, category, txn.get("name"), txn.get("date"),
             txn.get("account_id"), txn_id, str(company_id)),
            fetch=False,
        )
    else:
        Database.execute(
            """
            INSERT INTO transactions
                (company_id, amount, type, category, description, date, recurring,
                 plaid_transaction_id, plaid_account_id, plaid_item_id, source)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s, 'plaid')
            """,
            (str(company_id), amount, txn_type, category, txn.get("name"),
             txn.get("date"), txn_id, txn.get("account_id"), item_id),
            fetch=False,
        )


def sync_transactions_for_item(item_id: str) -> dict:
    """Pull new/updated/removed transactions for one item via the cursor-based
    /transactions/sync endpoint and upsert them. Safe to call repeatedly."""
    _ensure_tables()
    client = _get_client()

    row = Database.execute_one(
        "SELECT company_id, access_token, cursor FROM plaid_items WHERE item_id = %s",
        (item_id,),
    )
    if not row:
        logger.warning("sync_transactions_for_item: no plaid_items row for item_id=%s", item_id)
        return {"added": 0, "modified": 0, "removed": 0}

    company_id = row["company_id"]
    access_token = _decrypt(row["access_token"])
    cursor = row["cursor"]

    added = modified = removed = 0
    has_more = True

    while has_more:
        # First sync: cursor is NULL. plaid-python rejects cursor=None (must be
        # str), and Plaid treats an omitted cursor as "from the beginning" — so
        # only pass cursor once we actually have one.
        kwargs = {"access_token": access_token}
        if cursor:
            kwargs["cursor"] = cursor
        response = client.transactions_sync(TransactionsSyncRequest(**kwargs)).to_dict()

        for txn in response["added"]:
            _upsert_transaction(company_id, item_id, txn)
            added += 1
        for txn in response["modified"]:
            _upsert_transaction(company_id, item_id, txn)
            modified += 1
        for txn in response["removed"]:
            Database.execute(
                "DELETE FROM transactions WHERE plaid_transaction_id = %s AND company_id = %s",
                (txn["transaction_id"], str(company_id)),
                fetch=False,
            )
            removed += 1

        cursor = response["next_cursor"]
        has_more = response["has_more"]

    Database.execute(
        "UPDATE plaid_items SET cursor = %s, status = 'active', updated_at = NOW() WHERE item_id = %s",
        (cursor, item_id),
        fetch=False,
    )

    logger.info(
        "Plaid sync item=%s added=%s modified=%s removed=%s", item_id, added, modified, removed
    )
    return {"added": added, "modified": modified, "removed": removed}


def sync_transactions(company_id) -> dict:
    """Sync every active item for a company. App-facing; never raises."""
    _ensure_tables()
    rows = Database.execute(
        "SELECT item_id FROM plaid_items WHERE company_id = %s AND status != 'removed'",
        (str(company_id),),
    ) or []

    totals = {"added": 0, "modified": 0, "removed": 0, "items": 0}
    for r in rows:
        try:
            res = sync_transactions_for_item(r["item_id"])
            for k in ("added", "modified", "removed"):
                totals[k] += res.get(k, 0)
            totals["items"] += 1
        except ApiException as e:
            logger.error("sync failed for item %s: %s", r["item_id"], e)
        except Exception:
            logger.exception("sync failed for item %s", r["item_id"])
    return totals


# ---------------------------------------------------------------------------
# Item management
# ---------------------------------------------------------------------------

def get_items_for_company(company_id) -> list:
    """List a company's connected bank items for the connect UI."""
    _ensure_tables()
    rows = Database.execute(
        """
        SELECT item_id, institution_name, status
        FROM plaid_items
        WHERE company_id = %s AND status != 'removed'
        ORDER BY created_at DESC
        """,
        (str(company_id),),
    ) or []
    return [
        {
            "item_id": r["item_id"],
            "institution_name": r.get("institution_name"),
            "status": r["status"],
            "needs_reconnect": r["status"] in ("error", "login_required"),
        }
        for r in rows
    ]


def get_item_status(item_id: str, company_id=None):
    """Fetch live item status from Plaid (e.g. to detect ITEM_LOGIN_REQUIRED).
    If company_id is given, the lookup is scoped to that company."""
    query = "SELECT access_token FROM plaid_items WHERE item_id = %s"
    params = [item_id]
    if company_id is not None:
        query += " AND company_id = %s"
        params.append(str(company_id))
    row = Database.execute_one(query, tuple(params))
    if not row:
        return None
    access_token = _decrypt(row["access_token"])
    try:
        return _get_client().item_get(ItemGetRequest(access_token=access_token)).to_dict()
    except ApiException as e:
        logger.error("Plaid item_get failed for %s: %s", item_id, e)
        return {"error": _plaid_error_message(e)}


def remove_item(item_id: str) -> bool:
    """Revoke an item's access_token with Plaid and mark it removed locally.
    Historical transactions are left in place. Internal — callers should use
    disconnect_item(), which enforces company scoping."""
    row = Database.execute_one(
        "SELECT access_token FROM plaid_items WHERE item_id = %s", (item_id,)
    )
    if not row:
        return False
    access_token = _decrypt(row["access_token"])
    try:
        _get_client().item_remove(ItemRemoveRequest(access_token=access_token))
    except ApiException as e:
        # Still mark it removed locally so it leaves the UI; Plaid will expire
        # the token regardless.
        logger.error("Plaid item_remove failed for %s (marking removed locally): %s", item_id, e)
    Database.execute(
        "UPDATE plaid_items SET status = 'removed', updated_at = NOW() WHERE item_id = %s",
        (item_id,),
        fetch=False,
    )
    return True


def disconnect_item(company_id, item_id: str) -> dict:
    """Company-scoped disconnect. Enforces that the item belongs to this company
    (prevents cross-tenant/IDOR disconnects)."""
    _ensure_tables()
    row = Database.execute_one(
        "SELECT item_id FROM plaid_items WHERE item_id = %s AND company_id = %s",
        (item_id, str(company_id)),
    )
    if not row:
        return {"error": "Item not found."}
    remove_item(item_id)
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

_JWK_CACHE = {}


def _get_verification_key(key_id: str) -> dict:
    """Fetch (and cache) the JWK Plaid used to sign a webhook, by key id."""
    if key_id in _JWK_CACHE:
        return _JWK_CACHE[key_id]
    resp = _get_client().webhook_verification_key_get(
        WebhookVerificationKeyGetRequest(key_id=key_id)
    ).to_dict()
    key = resp["key"]
    _JWK_CACHE[key_id] = key
    return key


def verify_webhook(raw_body: bytes, verification_header: str) -> bool:
    """Verify a Plaid webhook per
    https://plaid.com/docs/api/webhooks/webhook-verification/

    Confirms the ES256 JWT signature (key fetched by kid from Plaid), that the
    token is fresh (<5 min), and that sha256(raw_body) matches the signed
    request_body_sha256. Returns True only if everything checks out.
    """
    if not verification_header:
        return False
    try:
        header = jwt.get_unverified_header(verification_header)
        if header.get("alg") != "ES256":
            logger.warning("Plaid webhook: unexpected alg %r", header.get("alg"))
            return False
        key_id = header.get("kid")
        if not key_id:
            return False

        jwk = _get_verification_key(key_id)
        public_key = ECAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(verification_header, public_key, algorithms=["ES256"])

        iat = claims.get("iat", 0)
        if (time.time() - iat) > _WEBHOOK_MAX_AGE_SECONDS:
            logger.warning("Plaid webhook: stale token (iat too old)")
            return False

        expected = claims.get("request_body_sha256")
        actual = hashlib.sha256(raw_body).hexdigest()
        if not expected or not hmac.compare_digest(str(expected), actual):
            logger.warning("Plaid webhook: body hash mismatch")
            return False

        return True
    except Exception:
        logger.exception("Plaid webhook verification error")
        return False


def handle_webhook(payload: dict) -> dict:
    """Dispatch a *verified* Plaid webhook payload. Callers MUST verify the
    signature (verify_webhook) before invoking this."""
    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")

    logger.info("Plaid webhook: type=%s code=%s item_id=%s", webhook_type, webhook_code, item_id)

    if webhook_type == "TRANSACTIONS" and webhook_code in (
        "SYNC_UPDATES_AVAILABLE",
        "INITIAL_UPDATE",
        "HISTORICAL_UPDATE",
        "DEFAULT_UPDATE",
    ):
        try:
            return sync_transactions_for_item(item_id)
        except Exception:
            logger.exception("webhook-triggered sync failed for item %s", item_id)
            return {"status": "error"}

    if webhook_type == "ITEM":
        if webhook_code == "ERROR":
            error_code = (payload.get("error") or {}).get("error_code")
            new_status = "login_required" if error_code == "ITEM_LOGIN_REQUIRED" else "error"
            Database.execute(
                "UPDATE plaid_items SET status = %s, updated_at = NOW() WHERE item_id = %s",
                (new_status, item_id),
                fetch=False,
            )
        elif webhook_code in ("PENDING_EXPIRATION", "USER_PERMISSION_REVOKED"):
            Database.execute(
                "UPDATE plaid_items SET status = 'error', updated_at = NOW() WHERE item_id = %s",
                (item_id,),
                fetch=False,
            )
        elif webhook_code == "LOGIN_REPAIRED":
            Database.execute(
                "UPDATE plaid_items SET status = 'active', updated_at = NOW() WHERE item_id = %s",
                (item_id,),
                fetch=False,
            )

    return {"status": "ok"}
