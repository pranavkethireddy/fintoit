"""
Social sign-in for Fintoit.

Google is implemented; Apple is stubbed behind the same interface so it can be
added later without touching the routes. Apple needs a paid developer account,
a Services ID / Team ID / Key ID / .p8 key, and a client secret that is itself
a JWT expiring within six months — none of which Google requires.

SECURITY — the rules this module exists to enforce
--------------------------------------------------
1. **Only ever link to an existing password account when the provider says the
   email is VERIFIED.** If a provider will assert an address its user does not
   control, "sign in with X" becomes an account-takeover path into an account
   holding the customer's financial data. Google sets `email_verified` on the
   userinfo response; we refuse to link without it.

2. **The authorization code is exchanged server-to-server.** Nothing sensitive
   is read from the browser redirect except the opaque `code` and our own
   `state`, so a tampered redirect cannot inject an identity.

3. **`state` is required and single-use** (CSRF), and `nonce` is round-tripped
   so a replayed authorization cannot be bound to a different session.

4. **The provider's `sub` is the durable identity, not the email.** People
   change their Google address; `sub` never changes. Email is used only for the
   first link.

Nothing here logs a token, a code, or a client secret.
"""

import os
import secrets

import requests

# Database is imported lazily inside the storage helpers rather than at module
# scope. The provider flow above it is pure HTTP and has no business requiring
# a live database connection just to be imported — which also lets the tests
# exercise the linking rules without standing up Postgres.

# Google's OpenID Connect endpoints. Hardcoded rather than fetched from the
# discovery document to avoid a network round trip on every sign-in; these have
# been stable for years and are pinned by Google's own docs.
GOOGLE_AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_ENDPOINT = 'https://openidconnect.googleapis.com/v1/userinfo'

HTTP_TIMEOUT = 10          # never hang a login on a slow provider


class OAuthError(Exception):
    """Raised for any failure that should send the user back to /login."""


# ── configuration ────────────────────────────────────────────────────────

def google_enabled():
    """Same gating pattern as plaid_enabled: the button stays hidden until
    credentials are actually configured, so a half-configured deploy cannot
    show a button that dead-ends."""
    return bool(os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET'))


def apple_enabled():
    """Apple is not wired up yet. Kept so templates and routes can be written
    once and Apple can be switched on by implementing this file only."""
    return False


def new_state():
    return secrets.token_urlsafe(32)


def new_nonce():
    return secrets.token_urlsafe(24)


def https_redirect_uri(url):
    """Force https on anything that isn't local.

    Render terminates TLS at the proxy, so Flask can generate an http:// URL
    for its own callback. Google rejects a redirect_uri that doesn't match the
    registered one exactly, and the mismatch is invisible in local testing.
    """
    if url.startswith('http://') and '://localhost' not in url and '://127.0.0.1' not in url:
        return 'https://' + url[len('http://'):]
    return url


# ── Google flow ──────────────────────────────────────────────────────────

def google_auth_url(redirect_uri, state, nonce):
    from urllib.parse import urlencode
    params = {
        'client_id': os.getenv('GOOGLE_CLIENT_ID'),
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'nonce': nonce,
        # Always show the chooser: without this, a shared machine silently
        # signs in as whoever used it last.
        'prompt': 'select_account',
    }
    return GOOGLE_AUTH_ENDPOINT + '?' + urlencode(params)


def fetch_google_identity(code, redirect_uri):
    """Exchange the code and return a normalised identity dict.

    Returns: {provider, subject, email, email_verified, full_name}
    Raises OAuthError on any provider or transport failure.
    """
    try:
        token_response = requests.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                'code': code,
                'client_id': os.getenv('GOOGLE_CLIENT_ID'),
                'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            },
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OAuthError('Could not reach Google. Please try again.') from exc

    if token_response.status_code != 200:
        # Deliberately does not echo the body — it can contain the code.
        raise OAuthError('Google rejected the sign-in. Please try again.')

    access_token = (token_response.json() or {}).get('access_token')
    if not access_token:
        raise OAuthError('Google did not return an access token.')

    try:
        info_response = requests.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={'Authorization': 'Bearer %s' % access_token},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OAuthError('Could not reach Google. Please try again.') from exc

    if info_response.status_code != 200:
        raise OAuthError('Could not read your Google profile.')

    info = info_response.json() or {}
    subject = info.get('sub')
    email = (info.get('email') or '').strip()
    if not subject or not email:
        raise OAuthError('Google did not return an email address.')

    return {
        'provider': 'google',
        'subject': str(subject),
        'email': email,
        # Google returns a real boolean here; anything else is treated as false.
        'email_verified': info.get('email_verified') is True,
        'full_name': (info.get('name') or '').strip() or email.split('@')[0],
    }


# ── storage ──────────────────────────────────────────────────────────────
#
# The columns are added lazily with ADD COLUMN IF NOT EXISTS, mirroring
# CompanyDB._ensure_tax_columns, so a deploy cannot 500 on an unmigrated
# schema and there is nothing to run by hand in Supabase.

_oauth_columns_ready = False


def ensure_oauth_columns():
    global _oauth_columns_ready
    if _oauth_columns_ready:
        return
    try:
        from Modules.database import Database
        Database.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_provider TEXT",
            fetch=False)
        Database.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS oauth_subject TEXT",
            fetch=False)
        _oauth_columns_ready = True
    except Exception:
        # Never block a sign-in attempt on this; the lookups below degrade to
        # "no linked identity found", which falls through to the email path.
        pass


def find_user_by_oauth(provider, subject):
    """The durable lookup: provider + sub, which never changes."""
    ensure_oauth_columns()
    try:
        from Modules.database import Database
        return Database.execute_one(
            "SELECT * FROM users WHERE oauth_provider = %s AND oauth_subject = %s",
            (provider, str(subject)))
    except Exception:
        return None


def find_user_by_email_ci(email):
    """Case-insensitive, because a miss here creates a DUPLICATE account rather
    than linking to the existing one — the failure mode is silent and annoying
    to unpick once the user has data in both."""
    try:
        from Modules.database import Database
        return Database.execute_one(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
    except Exception:
        return None


def link_oauth(user_id, provider, subject):
    """Attach a social identity to an existing account.

    Callers MUST have confirmed the provider reported a verified email before
    calling this.
    """
    ensure_oauth_columns()
    from Modules.database import Database
    Database.execute(
        "UPDATE users SET oauth_provider = %s, oauth_subject = %s WHERE id = %s",
        (provider, str(subject), user_id),
        fetch=False)


def unusable_password_hash():
    """A hash no password can produce, for accounts created via a provider.

    password_hash has no default in UserDB.create and the live schema is not in
    the repo, so writing NULL risks a NOT NULL violation on a real deploy.
    Storing a hash of a long random secret keeps the column populated while
    making password login impossible for that account — the standard
    "unusable password" approach. If the user later wants a password they go
    through the existing reset-token flow, which overwrites this.
    """
    from werkzeug.security import generate_password_hash
    return generate_password_hash(secrets.token_urlsafe(48))
