"""
Zoho CRM OAuth2 Token Manager for Fintoit
==========================================
Handles the full OAuth2 flow:
  1. Initial authorization (one-time browser step)
  2. Code → token exchange
  3. Auto-refresh access tokens before every API call
  4. Persistent storage in the database (table: zoho_tokens)

Setup steps (done once):
  a. Create a Zoho Server-Based OAuth client at
     https://api-console.zoho.com/  → "Server-based Applications"
  b. Add redirect URI: https://www.fintoit.com/admin/zoho/callback
  c. Set env vars:
       ZOHO_CLIENT_ID      = your client id
       ZOHO_CLIENT_SECRET  = your client secret
       ZOHO_REDIRECT_URI   = https://www.fintoit.com/admin/zoho/callback
  d. Visit /admin/zoho/connect as admin — click the link, authorise,
     you'll be redirected back and tokens are saved automatically.
  e. Done. All subsequent syncs use the stored refresh token forever.
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta
from Modules.database import Database


# ── Constants ────────────────────────────────────────────────────────────────

ZOHO_CLIENT_ID     = os.getenv('ZOHO_CLIENT_ID', '')
ZOHO_CLIENT_SECRET = os.getenv('ZOHO_CLIENT_SECRET', '')
ZOHO_REDIRECT_URI  = os.getenv('ZOHO_REDIRECT_URI', 'https://www.fintoit.com/admin/zoho/callback')

# Zoho accounts domain (use .eu / .com.au / .in if your Zoho account is not US)
ZOHO_ACCOUNTS_URL  = 'https://accounts.zoho.com'
ZOHO_API_BASE      = 'https://www.zohoapis.com/crm/v2'

# We request these scopes — add more if you need contacts/deals/etc.
ZOHO_SCOPES = 'ZohoCRM.modules.leads.ALL,ZohoCRM.modules.contacts.ALL'

# DB key used to store the single token row
TOKEN_KEY = 'fintoit_zoho'


# ── Database helpers ─────────────────────────────────────────────────────────

def _ensure_table():
    """Create zoho_tokens table if it doesn't exist."""
    Database.execute("""
        CREATE TABLE IF NOT EXISTS zoho_tokens (
            key         TEXT PRIMARY KEY,
            access_token  TEXT,
            refresh_token TEXT,
            expires_at    TIMESTAMPTZ,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            updated_at    TIMESTAMPTZ DEFAULT NOW()
        )
    """, fetch=False)


def _save_tokens(access_token: str, refresh_token: str, expires_in: int):
    """Upsert token row into the database."""
    _ensure_table()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    Database.execute("""
        INSERT INTO zoho_tokens (key, access_token, refresh_token, expires_at, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (key) DO UPDATE
          SET access_token  = EXCLUDED.access_token,
              refresh_token = COALESCE(EXCLUDED.refresh_token, zoho_tokens.refresh_token),
              expires_at    = EXCLUDED.expires_at,
              updated_at    = NOW()
    """, (TOKEN_KEY, access_token, refresh_token, expires_at), fetch=False)


def _load_tokens():
    """Return the stored token row as a dict, or None."""
    _ensure_table()
    row = Database.execute_one(
        "SELECT * FROM zoho_tokens WHERE key = %s", (TOKEN_KEY,)
    )
    return dict(row) if row else None


# ── OAuth flow ────────────────────────────────────────────────────────────────

def get_authorization_url(state: str = None) -> str:
    """
    Step 1 — Build the URL the admin must visit to grant permission.
    Pass a random `state` (stored server-side) to protect against OAuth CSRF.
    Returns a URL string.
    """
    params = {
        'response_type': 'code',
        'client_id':     ZOHO_CLIENT_ID,
        'scope':         ZOHO_SCOPES,
        'redirect_uri':  ZOHO_REDIRECT_URI,
        'access_type':   'offline',   # ← this is what gives us the refresh token
        'prompt':        'consent',   # ← forces Zoho to always return refresh_token
    }
    if state:
        params['state'] = state
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    return f"{ZOHO_ACCOUNTS_URL}/oauth/v2/auth?{query}"


def exchange_code_for_tokens(code: str) -> dict:
    """
    Step 2 — Exchange the one-time authorization code for access + refresh tokens.
    Called once from the OAuth callback route.
    Returns {'access_token': ..., 'refresh_token': ..., 'expires_in': ...}
    Raises ValueError on failure.
    """
    resp = requests.post(f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token", data={
        'grant_type':    'authorization_code',
        'client_id':     ZOHO_CLIENT_ID,
        'client_secret': ZOHO_CLIENT_SECRET,
        'redirect_uri':  ZOHO_REDIRECT_URI,
        'code':          code,
    }, timeout=15)

    data = resp.json()
    if 'error' in data:
        raise ValueError(f"Zoho token exchange failed: {data['error']}")

    _save_tokens(
        access_token  = data['access_token'],
        refresh_token = data['refresh_token'],
        expires_in    = int(data.get('expires_in', 3600)),
    )
    return data


def _refresh_access_token(refresh_token: str) -> str:
    """
    Step 3 (automatic) — Use the refresh token to get a new access token.
    Returns the new access token string.
    Raises ValueError on failure.
    """
    resp = requests.post(f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token", data={
        'grant_type':    'refresh_token',
        'client_id':     ZOHO_CLIENT_ID,
        'client_secret': ZOHO_CLIENT_SECRET,
        'refresh_token': refresh_token,
    }, timeout=15)

    data = resp.json()
    if 'error' in data:
        raise ValueError(f"Zoho token refresh failed: {data['error']}")

    # Zoho does NOT return a new refresh_token on refresh — pass None so we
    # keep the existing one in the database.
    _save_tokens(
        access_token  = data['access_token'],
        refresh_token = None,
        expires_in    = int(data.get('expires_in', 3600)),
    )
    return data['access_token']


def get_valid_access_token() -> str:
    """
    Main public function — call this before every Zoho API request.
    - Loads stored tokens from DB
    - If the access token is still valid, returns it immediately
    - If it has expired (or expires within 60 s), refreshes automatically
    - Raises RuntimeError if no tokens are stored (need to re-connect)
    """
    tokens = _load_tokens()

    if not tokens:
        raise RuntimeError(
            "Zoho not connected. Visit /admin/zoho/connect to authorise."
        )

    if not tokens.get('refresh_token'):
        raise RuntimeError(
            "Zoho refresh token missing. Re-connect at /admin/zoho/connect."
        )

    # Check expiry (expires_at is stored as UTC)
    expires_at = tokens['expires_at']
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)

    if now < expires_at:
        # Still valid
        return tokens['access_token']

    # Expired — refresh
    print("[Zoho] Access token expired, refreshing...")
    return _refresh_access_token(tokens['refresh_token'])


def is_connected() -> bool:
    """Returns True if Zoho tokens are stored in the DB."""
    try:
        tokens = _load_tokens()
        return bool(tokens and tokens.get('refresh_token'))
    except Exception:
        return False


def disconnect():
    """Remove stored tokens (admin can re-connect later)."""
    _ensure_table()
    Database.execute(
        "DELETE FROM zoho_tokens WHERE key = %s", (TOKEN_KEY,), fetch=False
    )


# ── Zoho API helpers ──────────────────────────────────────────────────────────

def _headers() -> dict:
    """Build auth headers, auto-refreshing the token if needed."""
    token = get_valid_access_token()
    return {
        'Authorization': f'Zoho-oauthtoken {token}',
        'Content-Type':  'application/json',
    }


def _zoho_status_map(status: str) -> str:
    return {
        'not_contacted': 'Not Contacted',
        'contacted':     'Contacted',
        'replied':       'Contacted',
        'meeting':       'Contacted',
        'bounced':       'Lost Lead',
        'passed':        'Lost Lead',
    }.get(status, 'Not Contacted')


def sync_contacts_to_zoho(contacts: list) -> dict:
    """
    Upsert a list of outreach contacts into Zoho CRM as Leads.
    Deduplicates on Email field.
    Returns {'synced': N, 'failed': N, 'errors': [...]}
    """
    synced = 0
    failed = 0
    errors = []

    for c in contacts:
        try:
            name_parts = (c.get('name') or '').strip().split(' ', 1)
            first = name_parts[0] or '-'
            last  = name_parts[1] if len(name_parts) > 1 else '-'

            payload = {
                "data": [{
                    "First_Name":   first,
                    "Last_Name":    last,
                    "Email":        c.get('email', ''),
                    "Company":      c.get('company') or 'Unknown',
                    "Title":        c.get('role') or '',
                    "Lead_Source":  "Fintoit Outreach",
                    "Lead_Status":  _zoho_status_map(c.get('status', 'not_contacted')),
                    "Description":  c.get('notes') or '',
                }],
                "duplicate_check_fields": ["Email"],
                "trigger": [],
            }

            resp = requests.post(
                f"{ZOHO_API_BASE}/Leads/upsert",
                json=payload,
                headers=_headers(),
                timeout=15,
            )

            if resp.status_code in (200, 201):
                synced += 1
            else:
                failed += 1
                errors.append(f"{c.get('email')}: HTTP {resp.status_code} — {resp.text[:120]}")

        except Exception as e:
            failed += 1
            errors.append(f"{c.get('email', '?')}: {str(e)}")

    return {'synced': synced, 'failed': failed, 'errors': errors}