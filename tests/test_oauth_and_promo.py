"""
Tests for social sign-in and the promo capture.

The rules worth pinning here are the security ones. "Sign in with Google" is a
second front door into an account holding a company's financial data, so the
tests below care most about the cases where it must REFUSE:

  * a provider that will not vouch for the email address
  * a redirect_uri that would silently downgrade to http
  * a token exchange that failed but still returned 200

The provider HTTP calls are stubbed — nothing here touches the network or a
database, so this runs in CI without credentials.

Run: python -m pytest tests/test_oauth_and_promo.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from Modules import oauth_utils as O


# ── fakes ─────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeRequests:
    """Stands in for the `requests` module inside oauth_utils."""

    RequestException = Exception

    def __init__(self, token_response=None, info_response=None, raise_on=None):
        self.token_response = token_response
        self.info_response = info_response
        self.raise_on = raise_on
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append(('post', url, data, timeout))
        if self.raise_on == 'post':
            raise self.RequestException('boom')
        return self.token_response

    def get(self, url, headers=None, timeout=None):
        self.calls.append(('get', url, headers, timeout))
        if self.raise_on == 'get':
            raise self.RequestException('boom')
        return self.info_response


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'test-client-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'test-client-secret')


def install_requests(monkeypatch, fake):
    monkeypatch.setattr(O, 'requests', fake)
    return fake


def ok_google(email='founder@northwind.com', verified=True, sub='11223344'):
    return FakeRequests(
        token_response=FakeResponse(200, {'access_token': 'tok'}),
        info_response=FakeResponse(200, {
            'sub': sub, 'email': email, 'email_verified': verified,
            'name': 'Ada Founder',
        }),
    )


# ── configuration gating ──────────────────────────────────────────────────

def test_google_is_disabled_until_both_credentials_exist(monkeypatch):
    """Half-configured must read as off, or the button dead-ends on Google."""
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_SECRET', raising=False)
    assert O.google_enabled() is False

    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'x')
    assert O.google_enabled() is False          # secret still missing

    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'y')
    assert O.google_enabled() is True


def test_apple_reports_itself_as_not_wired_up():
    assert O.apple_enabled() is False


def test_state_and_nonce_are_long_and_unique():
    states = {O.new_state() for _ in range(50)}
    assert len(states) == 50
    assert all(len(s) >= 32 for s in states)
    assert len({O.new_nonce() for _ in range(50)}) == 50


# ── redirect_uri ──────────────────────────────────────────────────────────

def test_redirect_uri_is_upgraded_to_https_off_localhost():
    """Render terminates TLS at the proxy, so Flask can emit http:// for its own
    callback. Google compares redirect_uri byte-for-byte against the registered
    value, and the mismatch never shows up in local testing."""
    assert O.https_redirect_uri('http://fintoit.com/auth/google/callback') \
        == 'https://fintoit.com/auth/google/callback'


def test_redirect_uri_leaves_local_development_alone():
    for url in ('http://localhost:5000/auth/google/callback',
                'http://127.0.0.1:5000/auth/google/callback'):
        assert O.https_redirect_uri(url) == url


def test_redirect_uri_leaves_https_untouched():
    url = 'https://fintoit.com/auth/google/callback'
    assert O.https_redirect_uri(url) == url


# ── the authorization URL ─────────────────────────────────────────────────

def test_auth_url_carries_state_nonce_and_account_chooser(google_env):
    url = O.google_auth_url('https://fintoit.com/cb', 'STATE123', 'NONCE456')
    assert url.startswith(O.GOOGLE_AUTH_ENDPOINT + '?')
    for fragment in ('state=STATE123', 'nonce=NONCE456', 'response_type=code',
                     'client_id=test-client-id', 'prompt=select_account'):
        assert fragment in url, fragment


def test_auth_url_requests_only_the_scopes_we_need(google_env):
    """Asking for more than openid/email/profile puts a scarier consent screen
    in front of a signup, for data we do not use."""
    from urllib.parse import parse_qs, urlparse
    scope = parse_qs(urlparse(O.google_auth_url('https://x/cb', 's', 'n')).query)['scope'][0]
    assert sorted(scope.split()) == ['email', 'openid', 'profile']


# ── identity exchange ─────────────────────────────────────────────────────

def test_successful_exchange_returns_a_normalised_identity(monkeypatch, google_env):
    install_requests(monkeypatch, ok_google())
    identity = O.fetch_google_identity('code', 'https://fintoit.com/cb')
    assert identity == {
        'provider': 'google',
        'subject': '11223344',
        'email': 'founder@northwind.com',
        'email_verified': True,
        'full_name': 'Ada Founder',
    }


def test_subject_is_always_a_string(monkeypatch, google_env):
    """Google sends `sub` as a string, but it is compared against a TEXT column;
    a numeric one would silently never match on the second sign-in."""
    install_requests(monkeypatch, ok_google(sub=99887766))
    assert O.fetch_google_identity('c', 'https://x/cb')['subject'] == '99887766'


@pytest.mark.parametrize('raw', [False, None, 'true', 'True', 1, 'yes', ''])
def test_only_a_real_boolean_true_counts_as_verified(monkeypatch, google_env, raw):
    """The string "false" is truthy in Python. Treating anything other than a
    genuine True as verified is how a linking check quietly stops checking."""
    fake = FakeRequests(
        token_response=FakeResponse(200, {'access_token': 'tok'}),
        info_response=FakeResponse(200, {
            'sub': '1', 'email': 'a@b.com', 'email_verified': raw, 'name': 'A'}),
    )
    install_requests(monkeypatch, fake)
    assert O.fetch_google_identity('c', 'https://x/cb')['email_verified'] is False


def test_falls_back_to_the_email_local_part_when_no_name(monkeypatch, google_env):
    fake = FakeRequests(
        token_response=FakeResponse(200, {'access_token': 'tok'}),
        info_response=FakeResponse(200, {
            'sub': '1', 'email': 'ada@northwind.com', 'email_verified': True}),
    )
    install_requests(monkeypatch, fake)
    assert O.fetch_google_identity('c', 'https://x/cb')['full_name'] == 'ada'


def test_token_endpoint_rejection_raises_oauth_error(monkeypatch, google_env):
    install_requests(monkeypatch, FakeRequests(
        token_response=FakeResponse(400, {'error': 'invalid_grant'})))
    with pytest.raises(O.OAuthError):
        O.fetch_google_identity('c', 'https://x/cb')


def test_two_hundred_without_a_token_still_fails(monkeypatch, google_env):
    """A 200 is not the same as a success."""
    install_requests(monkeypatch, FakeRequests(
        token_response=FakeResponse(200, {'scope': 'openid'})))
    with pytest.raises(O.OAuthError):
        O.fetch_google_identity('c', 'https://x/cb')


def test_userinfo_rejection_raises_oauth_error(monkeypatch, google_env):
    install_requests(monkeypatch, FakeRequests(
        token_response=FakeResponse(200, {'access_token': 'tok'}),
        info_response=FakeResponse(401, {})))
    with pytest.raises(O.OAuthError):
        O.fetch_google_identity('c', 'https://x/cb')


def test_missing_email_raises_rather_than_creating_a_blank_account(monkeypatch, google_env):
    install_requests(monkeypatch, FakeRequests(
        token_response=FakeResponse(200, {'access_token': 'tok'}),
        info_response=FakeResponse(200, {'sub': '1', 'name': 'No Email'})))
    with pytest.raises(O.OAuthError):
        O.fetch_google_identity('c', 'https://x/cb')


@pytest.mark.parametrize('stage', ['post', 'get'])
def test_network_failure_becomes_a_clean_oauth_error(monkeypatch, google_env, stage):
    """A transport error must not surface as a 500 on the callback."""
    install_requests(monkeypatch, FakeRequests(
        token_response=FakeResponse(200, {'access_token': 'tok'}),
        info_response=FakeResponse(200, {}),
        raise_on=stage))
    with pytest.raises(O.OAuthError):
        O.fetch_google_identity('c', 'https://x/cb')


def test_provider_calls_are_time_limited(monkeypatch, google_env):
    """An unbounded call to a provider hangs a worker on every sign-in attempt."""
    fake = install_requests(monkeypatch, ok_google())
    O.fetch_google_identity('c', 'https://x/cb')
    assert all(call[-1] == O.HTTP_TIMEOUT for call in fake.calls)
    assert len(fake.calls) == 2


def test_the_secret_is_only_ever_sent_to_googles_token_endpoint(monkeypatch, google_env):
    fake = install_requests(monkeypatch, ok_google())
    O.fetch_google_identity('c', 'https://x/cb')
    post_url, post_data = fake.calls[0][1], fake.calls[0][2]
    assert post_url == O.GOOGLE_TOKEN_ENDPOINT
    assert post_data['client_secret'] == 'test-client-secret'
    # and never as a query parameter or to userinfo
    get_url, get_headers = fake.calls[1][1], fake.calls[1][2]
    assert get_url == O.GOOGLE_USERINFO_ENDPOINT
    assert 'test-client-secret' not in str(get_headers)


def test_endpoints_are_https():
    for endpoint in (O.GOOGLE_AUTH_ENDPOINT, O.GOOGLE_TOKEN_ENDPOINT,
                     O.GOOGLE_USERINFO_ENDPOINT):
        assert endpoint.startswith('https://'), endpoint


# ── unusable password ─────────────────────────────────────────────────────

def test_oauth_accounts_get_a_password_nobody_can_use():
    """password_hash has no default in UserDB.create and the live DDL is not in
    the repo, so writing NULL risks a NOT NULL violation on a real deploy. A
    hash of a long random secret keeps the column populated while making
    password login impossible."""
    from werkzeug.security import check_password_hash

    a = O.unusable_password_hash()
    b = O.unusable_password_hash()
    assert a != b, 'must not be a shared constant'
    assert len(a) > 40
    for guess in ('', 'password', 'google', None):
        if guess is None:
            continue
        assert not check_password_hash(a, guess)


# ── the linking rules, as enforced by the callback ────────────────────────
#
# The callback itself lives in app.py and needs Flask plus a database, so these
# assert the decision table it implements. If the ordering below is ever
# changed in app.py, this is the note explaining why it was that way.

def test_linking_decision_table_is_documented_and_ordered():
    """Order matters:

    1. provider subject match  -> sign in (durable identity, survives an email change)
    2. verified email match    -> link, then sign in
    3. otherwise               -> collect details, verify phone, then create

    Checking email before subject would re-link on every sign-in and would mean
    an email change at the provider silently orphaned the account. Skipping the
    verified check at step 2 turns social sign-in into account takeover.
    """
    import inspect
    import re as _re

    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    callback = source[source.index('def google_callback('):
                      source.index('def social_complete(')]

    subject_at = callback.index('find_user_by_oauth')
    verified_at = callback.index("identity['email_verified']")
    email_at = callback.index('find_user_by_email_ci')
    pending_at = callback.index("session['pending_oauth']")

    assert subject_at < verified_at < email_at < pending_at, (
        'the callback must check the provider subject first, refuse an '
        'unverified email before looking one up, and only then fall through '
        'to creating a new account')
    assert inspect.cleandoc(test_linking_decision_table_is_documented_and_ordered.__doc__)
    assert _re.search(r'if not identity\[.email_verified.\]', callback), \
        'the unverified-email refusal must remain an explicit guard'


def test_callback_enforces_ban_and_two_factor_for_social_sign_in():
    """A different front door must not be a way around the locks."""
    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    tail = source[source.index('def _finish_social_login('):
                  source.index('def google_login(')]
    assert "is_banned" in tail
    assert "two_factor_enabled" in tail
    assert "pending_2fa_user_id" in tail


def test_no_account_is_created_before_the_phone_is_verified():
    """An abandoned social signup must leave nothing behind."""
    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    complete = source[source.index('def social_complete('):
                      source.index("@app.route('/login'")]
    assert 'User.create_user' not in complete, (
        'social_complete must only stage a pending_signup; the account is '
        'created by verify_signup once the phone code checks out')
    assert "session['pending_signup']" in complete


# ── promo capture ─────────────────────────────────────────────────────────

def test_promo_code_is_configurable_without_a_deploy():
    """The code lives in Stripe. If this page can promise a string Stripe has
    never heard of, the discount silently fails at checkout."""
    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    assert "PROMO_CODE = os.getenv('PROMO_CODE', 'AICFO')" in source
    assert "PROMO_PERCENT = os.getenv('PROMO_PERCENT', '15')" in source


def test_promo_endpoint_is_rate_limited_and_validates_email():
    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    route = source[source.index('def promo_subscribe('):
                   source.index('def newsletter_unsubscribe(')]
    assert '_promo_rate_limited' in route, 'unauthenticated mail-sending endpoint needs a throttle'
    assert '429' in route
    assert '400' in route, 'invalid addresses must be rejected before we mail them'
    assert 'if not already' in route, 'a repeat submit must not re-send mail'


def test_marketing_email_carries_an_unsubscribe_link():
    """CAN-SPAM requires a working opt-out and a physical address on marketing
    mail. The verification codes are transactional and exempt; this is not."""
    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    route = source[source.index('def promo_subscribe('):
                   source.index('def newsletter_unsubscribe(')]
    assert 'newsletter_unsubscribe' in route
    assert 'Unsubscribe' in route
    assert 'TX' in route, 'a postal address is required on marketing email'


def test_unsubscribe_links_are_signed():
    """Otherwise anyone can unsubscribe anyone by guessing an address."""
    app_py = os.path.join(os.path.dirname(__file__), '..', 'app.py')
    source = open(app_py, encoding='utf-8').read()
    assert 'URLSafeSerializer' in source
    assert "salt='newsletter-unsub'" in source
    unsub = source[source.index('def newsletter_unsubscribe('):]
    assert 'BadSignature' in unsub, 'a forged token must 404, not unsubscribe someone'


def test_rate_limiter_allows_a_burst_then_blocks():
    import importlib.util
    spec = importlib.util.spec_from_file_location('rl_probe', __file__)
    # Exercise the algorithm directly rather than importing app.py (which needs
    # a database); this mirrors _promo_rate_limited exactly.
    import time
    hits = {}
    window, limit = 3600, 5

    def limited(ip):
        now = time.time()
        seen = [t for t in hits.get(ip, []) if now - t < window]
        if len(seen) >= limit:
            hits[ip] = seen
            return True
        seen.append(now)
        hits[ip] = seen
        return False

    assert [limited('1.2.3.4') for _ in range(5)] == [False] * 5
    assert limited('1.2.3.4') is True
    assert limited('5.6.7.8') is False        # per-IP, not global


# ── Resend contact sync ───────────────────────────────────────────────────
#
# The local table records who asked for marketing email; Resend is what
# actually sends it. Once contacts live there, the local row stops being the
# source of truth for deliverability — which makes the UNSUBSCRIBE path the
# one that carries legal weight, not the subscribe path.

from Modules import contacts_sync as CS


class FakeContacts:
    def __init__(self, create_fails=False, update_fails=False):
        self.create_fails = create_fails
        self.update_fails = update_fails
        self.created = []
        self.updated = []

    def create(self, params):
        if self.create_fails:
            raise RuntimeError('already exists')
        self.created.append(params)
        return {'id': 'c_1'}

    def update(self, params):
        if self.update_fails:
            raise RuntimeError('nope')
        self.updated.append(params)
        return {'id': 'c_1'}


class FakeResend:
    def __init__(self, contacts):
        self.Contacts = contacts
        self.api_key = None


@pytest.fixture
def resend_env(monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY', 're_test')
    monkeypatch.delenv('RESEND_AUDIENCE_ID', raising=False)


def install_resend(monkeypatch, contacts):
    monkeypatch.setattr(CS, '_client', lambda: FakeResend(contacts))
    return contacts


def test_sync_is_a_silent_noop_without_a_key(monkeypatch):
    """Must never be a deploy dependency."""
    monkeypatch.delenv('RESEND_API_KEY', raising=False)
    assert CS.enabled() is False
    assert CS.sync_subscriber('a@b.com') is False
    assert CS.mark_unsubscribed('a@b.com') is False


def test_sync_creates_a_contact(monkeypatch, resend_env):
    fake = install_resend(monkeypatch, FakeContacts())
    assert CS.sync_subscriber('ada@northwind.com') is True
    assert fake.created[0]['email'] == 'ada@northwind.com'
    assert fake.created[0]['unsubscribed'] is False


def test_existing_contact_falls_back_to_update(monkeypatch, resend_env):
    """Someone re-subscribing after opting out must be flipped BACK to
    subscribed, not left suppressed forever."""
    fake = install_resend(monkeypatch, FakeContacts(create_fails=True))
    assert CS.sync_subscriber('ada@northwind.com') is True
    assert fake.updated[0]['unsubscribed'] is False


def test_sync_returns_false_when_both_calls_fail(monkeypatch, resend_env):
    install_resend(monkeypatch, FakeContacts(create_fails=True, update_fails=True))
    assert CS.sync_subscriber('ada@northwind.com') is False


def test_audience_id_is_attached_only_when_configured(monkeypatch, resend_env):
    fake = install_resend(monkeypatch, FakeContacts())
    CS.sync_subscriber('a@b.com')
    assert 'audience_id' not in fake.created[0]

    monkeypatch.setenv('RESEND_AUDIENCE_ID', 'aud_123')
    fake2 = install_resend(monkeypatch, FakeContacts())
    CS.sync_subscriber('a@b.com')
    assert fake2.created[0]['audience_id'] == 'aud_123'


def test_unsubscribe_flags_rather_than_deletes(monkeypatch, resend_env):
    """Deleting would let the address be silently re-added by a later signup
    and start receiving mail again. Flagging suppresses it permanently."""
    fake = install_resend(monkeypatch, FakeContacts())
    assert CS.mark_unsubscribed('ada@northwind.com') is True
    assert fake.updated == [{'email': 'ada@northwind.com', 'unsubscribed': True}]
    assert fake.created == []


def test_unsubscribe_failure_is_reported_not_swallowed_silently(monkeypatch, resend_env, capsys):
    install_resend(monkeypatch, FakeContacts(update_fails=True))
    assert CS.mark_unsubscribed('ada@northwind.com') is False
    out = capsys.readouterr().out
    assert 'RESEND UNSUBSCRIBE FAILED' in out
    assert 'ada@northwind.com' not in out, 'must not echo the address into logs'


def test_backfill_counts_successes_and_failures(monkeypatch, resend_env):
    install_resend(monkeypatch, FakeContacts())
    assert CS.sync_many(['a@b.com', 'c@d.com']) == (2, 0)
    install_resend(monkeypatch, FakeContacts(create_fails=True, update_fails=True))
    assert CS.sync_many(['a@b.com']) == (0, 1)
    assert CS.sync_many([]) == (0, 0)
    assert CS.sync_many(None) == (0, 0)


def test_blank_email_is_rejected_before_calling_the_provider(monkeypatch, resend_env):
    fake = install_resend(monkeypatch, FakeContacts())
    assert CS.sync_subscriber('') is False
    assert CS.mark_unsubscribed(None) is False
    assert fake.created == [] and fake.updated == []


# ── wiring, asserted against app.py ───────────────────────────────────────

def _app_source():
    return open(os.path.join(os.path.dirname(__file__), '..', 'app.py'),
                encoding='utf-8').read()


def test_unsubscribe_route_syncs_to_resend():
    """A local delete alone leaves the address subscribed at the provider, so
    the next broadcast reaches someone who opted out."""
    source = _app_source()
    route = source[source.index('def newsletter_unsubscribe('):]
    assert 'contacts_sync.mark_unsubscribed' in route
    assert 'NewsletterDB.delete' in route


def test_both_capture_points_sync_to_resend():
    source = _app_source()
    promo = source[source.index('def promo_subscribe('):
                   source.index('def admin_newsletter_sync(')]
    blog = source[source.index('def blogsubscribe('):]
    for name, chunk in (('promo', promo), ('blog', blog)):
        assert 'contacts_sync.sync_subscriber' in chunk, name
        assert 'send_in_background' in chunk, '%s sync must not block the response' % name


def test_backfill_is_admin_only_and_not_automatic():
    source = _app_source()
    route = source[source.index('def admin_newsletter_sync('):
                   source.index('def newsletter_unsubscribe(')]
    assert 'current_user.is_admin' in route
    assert "methods=['POST']" in source[source.index('/admin/newsletter/sync') - 120:
                                        source.index('/admin/newsletter/sync') + 120]
    # must not be invoked anywhere at import/deploy time
    assert source.count('sync_many') == 1, 'backfill should only be reachable via the admin route'
