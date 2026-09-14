from flask import Flask, abort, jsonify, render_template, request, redirect, url_for, flash, session, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_login import user_loaded_from_cookie, user_loaded_from_request
from flask_mail import Mail, Message
from functools import wraps
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from decimal import Decimal
from dotenv import load_dotenv
from groq import Groq
from jinja2 import TemplateNotFound
import resend
import os
import time
import io
import secrets
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode
import base64
from io import BytesIO
from Modules.zoho_utils import (
    get_authorization_url, exchange_code_for_tokens,
    get_valid_access_token, sync_contacts_to_zoho,
    is_connected, disconnect as zoho_disconnect
)
from Modules.plaid_utils import (
    create_link_token, exchange_public_token, get_items_for_company,
    disconnect_item, sync_transactions, verify_webhook, handle_webhook
)

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

PLANS = {
    "free": {
        "name": "Free",
        "display": "$0",
        "period": "/month",
        "description": "Core features, no credit card",
        "features": [
            'Dashboard & burn rate',
            'Runway tracking',
            'Transaction tracking',
        ],
        "price_id": "price_1TIVTsAsD0D6h74v5VhvCVS5"
    },
    "starter": {
        "name": "Starter",
        "display": "$49",
        "period": "/month",
        "description": "Best for individuals",
        "features": ["Everything in Free", "Unlimited AI chat", "Unlimited reports", "Cash flow forecasting", "Scenario planning", "Budget alerts", "Anomaly detection"],
        "price_id": "price_1TIZ4BAsD0D6h74vZtimvxaH"
    },
    "pro": {
        "name": "Pro",
        "display": "$149",
        "period": "/month",
        "description": "For power users",
        "features": ["Everything in Starter", "Cap table management", "Payroll tracking", "Vendor management", "KPI dashboard", "Board report PDF", "Priority support", 'Basic Accounting & Tax Docs'],
        "price_id": "price_1TIZ5kAsD0D6h74vgcbwNMwi"
    }
}
PLANS1 = {
    "free": {
        "name": "Free",
        "display": "$0",
        "period": "/month",
        "description": "Core features, no credit card",
        "features": [
            'Dashboard & burn rate',
            'Runway tracking',
            'Transaction tracking',
        ],
        "price_id": "price_1TIVTsAsD0D6h74v5VhvCVS5"
    },
    "starter": {
        "name": "Starter",
        "display": "$49",
        "period": "/month",
        "description": "Best for individuals",
        "features": ["Everything in Free", "Unlimited AI chat", "Unlimited reports", "Cash flow forecasting", "Scenario planning", "Budget alerts", "Anomaly detection"],
        "price_id": "price_1TIZ4BAsD0D6h74vZtimvxaH"
    },
    "pro": {
        "name": "Pro",
        "display": "$149",
        "period": "/month",
        "description": "Full CFO suite",
        "features": ["Everything in Starter", "Cap table management", "Payroll tracking", "Vendor management", "KPI dashboard", "Board report PDF", "Priority support", 'Basic Accounting & Tax Docs'],
        "price_id": "price_1TIZ5kAsD0D6h74vgcbwNMwi"
    }
}

# Import custom modules
from Modules.financial_calculator import (FinancialCalculator, create_sample_transactions,
                                          AnomalyDetector, DEMO_STARTING_CASH)
from Modules.auth import User
from Modules import cache as metrics_cache
from Modules.export_utils import generate_csv, parse_csv, generate_pdf_report, generate_investor_report
from Modules.ai_insights import AIFinancialAdvisor, format_insights_for_display
from Modules.accounting_service import FinancialStatements, ChartOfAccountsDB, InvoiceDB, BillDB, TaxDocumentDB
from Modules import tax_estimator
from Modules import metrics as canonical_metrics
from Modules.database import TransactionDB, CompanyDB, UserDB, BudgetDB, GoalDB, VendorDB, EmployeeDB, PayRunDB, CapTableDB, Database, FeedbackDB, ContactDB, JobApplicationDB, OutreachDB, HeadcountDB, ChatHistoryDB, NewsletterDB
from Modules.email_utils import send_email, send_html_email, send_password_reset, send_invoice_email, send_contract_signing_request, send_in_background
from Modules.stripe_utils import PLANS, FREE_PLAN, create_checkout_session, create_portal_session, handle_webhook
from posts import POSTS, CATEGORIES, search_posts, get_post, get_related
from Modules.sms_utils import send_sms_verification
# Create Flask application
app = Flask(__name__)
# Change port to 5001 to avoid macOS AirPlay conflict
PORT = int(os.environ.get("PORT", 5001)) 
#Flask Mail configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = ('Fintoit', os.getenv('MAIL_DEFAULT_SENDER'))
mail = Mail(app)
# Configuration
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is not set. Refusing to start with an "
        "insecure fallback — a known secret key makes all session cookies forgeable. "
        "Set SECRET_KEY in the environment (see .env.example)."
    )
app.secret_key = SECRET_KEY

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'warning'

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

@app.context_processor
def inject_plaid_enabled():
    # Controls whether Plaid UI (e.g. the "Connect Bank" nav link) is shown.
    # Stays hidden until Plaid credentials are configured (e.g. after production approval).
    return {'plaid_enabled': bool(os.getenv('PLAID_CLIENT_ID') and os.getenv('PLAID_SECRET'))}


@app.context_processor
def inject_social_login():
    # Same gating idea as plaid_enabled: the "Continue with Google" button stays
    # hidden until credentials exist, so a half-configured deploy can never show
    # a button that dead-ends on Google's error page.
    from Modules import oauth_utils
    return {
        'google_login_enabled': oauth_utils.google_enabled(),
        'apple_login_enabled': oauth_utils.apple_enabled(),
    }

# How often (seconds) to re-check the ban flag for a signed-in user. This
# previously ran a database query on EVERY request, including static assets,
# adding a round trip to every page load. A short interval keeps enforcement
# effectively immediate while removing that per-request query.
BAN_CHECK_INTERVAL = int(os.getenv('BAN_CHECK_INTERVAL', '60'))


@app.before_request
def check_banned():
    # Static files never need an auth/ban check.
    if request.endpoint == 'static':
        return
    if not current_user.is_authenticated:
        return

    now = time.time()
    if now - session.get('_ban_checked_at', 0) < BAN_CHECK_INTERVAL:
        return

    user_data = UserDB.get_by_id(current_user.id)
    if user_data and user_data.get('is_banned'):
        session.pop('_ban_checked_at', None)
        logout_user()
        flash('Your account has been suspended.', 'error')
        return redirect(url_for('login'))
    session['_ban_checked_at'] = now


# Cache headers for static assets. Previously every navigation re-fetched
# /static/css/tailwind.css, the logo and other images.
#
# Filenames here are not content-hashed, so cache lifetimes are chosen to be
# safe without a cache-busting query string: CSS/JS get a short TTL so a deploy
# propagates quickly, while images (which change rarely) get a long one.
STATIC_MAX_AGE_CODE  = int(os.getenv('STATIC_MAX_AGE_CODE', '3600'))     # 1 hour: css/js
STATIC_MAX_AGE_MEDIA = int(os.getenv('STATIC_MAX_AGE_MEDIA', '604800'))  # 7 days: images/fonts


@app.after_request
def add_static_cache_headers(response):
    if request.endpoint != 'static':
        return response
    path = (request.path or '').lower()
    if path.endswith(('.css', '.js')):
        max_age = STATIC_MAX_AGE_CODE
    elif path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
                        '.webp', '.woff', '.woff2', '.ttf')):
        max_age = STATIC_MAX_AGE_MEDIA
    else:
        max_age = STATIC_MAX_AGE_CODE
    response.headers['Cache-Control'] = 'public, max-age=%d' % max_age
    return response


# ============================================
# DEMO / READ-ONLY MODE
# ============================================
DEMO_EMAIL = 'demo@fintoit.com'
 
def is_demo_user():
    """Return True if the currently logged-in user is the demo account."""
    return (
        current_user.is_authenticated
        and current_user.email.lower() == DEMO_EMAIL
    )
 
def demo_readonly(f):
    """
    Blocks all write actions (POST/DELETE) for the demo account.
    GET requests pass through normally so the demo user can browse freely.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if is_demo_user() and request.method != 'GET':
            flash('👀 This is a read-only demo account.', 'warning')
            return redirect(request.referrer or url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def is_demo_sandbox():
    """True when the current session is a per-visit demo sandbox: a private,
    throwaway company that is fully writable and wiped on logout. Distinct from the
    legacy shared read-only demo@fintoit.com account (see is_demo_user)."""
    return bool(session.get('demo_sandbox'))

# ============================================
# PUBLIC ROUTES
# ============================================
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('home.html')

# Public, ungated lead magnet (FIN-6/FIN-16). No login_required — must be
# indexable and usable without an account. All runway math runs client-side.
@app.route('/runway-calculator')
def runway_calculator():
    return render_template('runway_calculator.html')

# SEO content cluster: two guides that support (and link into) the runway
# calculator hub. Public and ungated so search engines can index them.
@app.route('/burn-rate-guide')
def burn_rate_guide():
    return render_template('burn_rate_guide.html')


@app.route('/investor-update-template')
def investor_update_template():
    return render_template('investor_update_template.html')


@app.route('/guides')
def guides():
    """Index page for the free guides + tools cluster (linked from the nav)."""
    return render_template('guides.html')


@app.route('/sitemap.xml')
def sitemap():
    # Public, indexable pages only (no app/admin/auth/action routes).
    # /team was removed — it is not a route and returned 404.
    urls = [
        ('/', '1.0'),
        ('/runway-calculator', '0.9'),
        ('/guides', '0.8'),
        ('/burn-rate-guide', '0.8'),
        ('/investor-update-template', '0.8'),
        ('/features', '0.8'),
        ('/pricing', '0.8'),
        ('/about', '0.6'),
        ('/contact', '0.6'),
        ('/demo', '0.6'),
        ('/media-kit', '0.5'),
        ('/privacy', '0.3'),
        ('/terms', '0.3'),
    ]
    items = "".join(
        "<url><loc>https://fintoit.com{p}</loc><changefreq>monthly</changefreq>"
        "<priority>{pr}</priority></url>".format(p=p, pr=pr)
        for p, pr in urls
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + items + '</urlset>')
    return app.response_class(xml, mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    body = ("User-agent: *\n"
            "Allow: /\n"
            "Sitemap: https://fintoit.com/sitemap.xml\n")
    return app.response_class(body, mimetype='text/plain')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/media-kit')
def media_kit():
    return render_template('media_kit.html')

@app.route('/referrals')
@login_required
def referrals():
    code = str(current_user.id)[:8]
    return render_template(
        'referrals.html',
        referral_code=code,
        my_referrals=UserDB.get_referral_count(code),
        leaderboard=UserDB.get_referral_leaderboard(10),
        goal=10,
    )

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    error = None
    ref_code = request.args.get('ref', '')

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        company_name = request.form.get('company_name', '').strip()
        starting_cash = float(request.form.get('starting_cash', 0) or 0)
        phone_code = request.form.get('phone_code', '+1')
        phone_number = request.form.get('phone_number', '').strip()
        phone = f"{phone_code.replace('-CA','')} {phone_number}".strip() if phone_number else ''
        referral = request.form.get('referral', '').strip()
        referral_code = referral if referral else ''

        # Validate phone is provided (now required)
        if not phone_number:
            error = 'Phone number is required.'
            return render_template('signup.html', error=error, ref_code=ref_code)

        # Check if email already registered
        existing_user = UserDB.get_by_email(email)
        if existing_user:
            error = 'An account with this email already exists.'
            return render_template('signup.html', error=error, ref_code=ref_code)

        if len(password) < 6:
            error = 'Password must be at least 6 characters.'
            return render_template('signup.html', error=error, ref_code=ref_code)

        # Generate a single 6-digit code used for both email and phone
        import random
        code = str(random.randint(100000, 999999))
        expiry = datetime.now(timezone.utc) + timedelta(minutes=10)

        # Store pending signup in session (don't create account yet)
        session['pending_signup'] = {
            'email': email,
            'password_hash': generate_password_hash(password),
            'full_name': full_name,
            'company_name': company_name,
            'starting_cash': starting_cash,
            'phone': phone,
            'referral_code': referral_code,
            'code': code,
            'expiry': expiry.isoformat(),
            'email_verified': False,
            'phone_verified': False
        }

        # Send email verification code
        from Modules.email_utils import send_html_email
        send_html_email(
            from_name="Fintoit",
            to=email,
            subject="Verify your Fintoit account",
            html_body=f"""
            <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:32px;background:#f7f6f2;border-radius:12px;">
                <h2 style="color:#0a0a0f;">Verify your email</h2>
                <p style="color:#6b6b7a;">Enter this code to verify your email address:</p>
                <div style="font-size:2rem;font-weight:800;letter-spacing:0.2em;color:#0a0a0f;margin:24px 0;">{code}</div>
                <p style="color:#6b6b7a;font-size:0.85rem;">Expires in 10 minutes. If you didn't sign up for Fintoit, ignore this email.</p>
            </div>"""
        )

        # Send SMS verification code
        from Modules.sms_utils import send_sms_verification
        send_sms_verification(phone, code)

        return redirect(url_for('verify_signup'))

    return render_template('signup.html', error=error, ref_code=ref_code)


@app.route('/verify-signup', methods=['GET', 'POST'])
def verify_signup():
    pending = session.get('pending_signup')
    if not pending:
        return redirect(url_for('signup'))

    error = None

    if request.method == 'POST':
        email_code = request.form.get('email_code', '').strip()
        phone_code = request.form.get('phone_code', '').strip()

        from datetime import datetime, timezone
        expiry = datetime.fromisoformat(pending['expiry'])
        if datetime.now(timezone.utc) > expiry:
            session.pop('pending_signup', None)
            flash('Verification codes expired. Please sign up again.', 'error')
            return redirect(url_for('signup'))

        # A social sign-in already proved the address (Google reported
        # email_verified), so mailing a second code and asking for it back
        # would be friction that proves nothing. The phone is still unproven
        # either way, so that code is always required.
        email_prverified = bool(pending.get('email_prverified'))

        if not email_prverified and email_code != pending['code']:
            error = 'Invalid email verification code.'
            return render_template('verify_signup.html', error=error,
                                   email=pending['email'], phone=pending['phone'],
                                   email_prverified=email_prverified)

        if phone_code != pending['code']:
            error = 'Invalid phone verification code.'
            return render_template('verify_signup.html', error=error,
                                   email=pending['email'], phone=pending['phone'],
                                   email_prverified=email_prverified)

        # Verified — create the account
        user = User.create_user(
            pending['email'],
            None,
            pending['full_name'],
            pending['company_name'],
            pending['starting_cash'],
            pending['phone'],
            pending['referral_code'],
            password_hash=pending.get('password_hash'),
        )

        # Attach the social identity, if this signup came in through one, so
        # the next sign-in matches on the provider subject rather than falling
        # back to the email path.
        if pending.get('oauth_provider') and pending.get('oauth_subject'):
            try:
                from Modules import oauth_utils
                oauth_utils.link_oauth(user.id, pending['oauth_provider'],
                                       pending['oauth_subject'])
            except Exception as exc:                      # noqa: BLE001
                # The account is already created and usable; a failed link just
                # means the next Google sign-in re-links via the verified email.
                print(f"[OAUTH LINK FAILED] {type(exc).__name__}")

        session.pop('pending_signup', None)
        login_user(user, remember=True)
        flash('Welcome to Fintoit! 🎉', 'success')
        return redirect(url_for('pricing', welcome=1))

    return render_template('verify_signup.html', error=error,
                           email=pending['email'], phone=pending['phone'],
                           email_prverified=bool(pending.get('email_prverified')))


@app.route('/verify-signup/resend', methods=['POST'])
def resend_verification():
    pending = session.get('pending_signup')
    if not pending:
        return redirect(url_for('signup'))

    import random
    code = str(random.randint(100000, 999999))
    expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
    pending['code'] = code
    pending['expiry'] = expiry.isoformat()
    session['pending_signup'] = pending

    # A social signup has no email code to re-send — the address was already
    # proven by the provider and the form doesn't ask for one.
    if not pending.get('email_prverified'):
        from Modules.email_utils import send_html_email
        send_html_email(
            from_name="Fintoit",
            to=pending['email'],
            subject="Your new Fintoit verification code",
            html_body=f"""
            <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:32px;background:#f7f6f2;border-radius:12px;">
                <h2 style="color:#0a0a0f;">New verification code</h2>
                <div style="font-size:2rem;font-weight:800;letter-spacing:0.2em;color:#0a0a0f;margin:24px 0;">{code}</div>
                <p style="color:#6b6b7a;font-size:0.85rem;">Expires in 10 minutes.</p>
            </div>"""
        )

    from Modules.sms_utils import send_sms_verification
    send_sms_verification(pending['phone'], code)

    flash('New verification code sent!', 'success')
    return redirect(url_for('verify_signup'))


# ============================================
# SOCIAL SIGN-IN  (Google today; Apple stubbed in Modules/oauth_utils.py)
# ============================================

def _google_redirect_uri():
    from Modules.oauth_utils import https_redirect_uri
    return https_redirect_uri(url_for('google_callback', _external=True))


def _finish_social_login(user_row, greeting):
    """Shared tail for a social sign-in that resolved to an existing account.

    Social sign-in is a different front door, not a different set of rules: a
    suspended account stays suspended, and 2FA still has to be satisfied. Both
    checks mirror the password login path exactly.
    """
    if user_row.get('is_banned'):
        flash('Your account has been suspended. Contact support.', 'error')
        return redirect(url_for('login'))

    if user_row.get('two_factor_enabled'):
        session['pending_2fa_user_id'] = str(user_row['id'])
        return redirect(url_for('two_fa_challenge'))

    user = User.get(user_row['id'])
    if not user:
        flash('Could not load your account. Please try again.', 'error')
        return redirect(url_for('login'))

    login_user(user, remember=True)
    flash(greeting, 'success')
    if user.is_admin:
        return redirect(url_for('admin_panel'))
    return redirect(url_for('dashboard'))


@app.route('/auth/google')
def google_login():
    from Modules import oauth_utils
    if not oauth_utils.google_enabled():
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    state = oauth_utils.new_state()
    nonce = oauth_utils.new_nonce()
    session['google_oauth_state'] = state
    session['google_oauth_nonce'] = nonce
    return redirect(oauth_utils.google_auth_url(_google_redirect_uri(), state, nonce))


@app.route('/auth/google/callback')
def google_callback():
    from Modules import oauth_utils
    if not oauth_utils.google_enabled():
        abort(404)

    # state is single-use: popped before anything else so a replayed callback
    # cannot be processed twice.
    expected_state = session.pop('google_oauth_state', None)
    session.pop('google_oauth_nonce', None)

    if request.args.get('error'):
        # User pressed Cancel on Google's consent screen — not an error worth
        # shouting about.
        return redirect(url_for('login'))

    if not expected_state or request.args.get('state') != expected_state:
        flash('That sign-in link expired or did not match. Please try again.', 'error')
        return redirect(url_for('login'))

    code = request.args.get('code')
    if not code:
        return redirect(url_for('login'))

    try:
        identity = oauth_utils.fetch_google_identity(code, _google_redirect_uri())
    except oauth_utils.OAuthError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('login'))
    except Exception as exc:                       # noqa: BLE001
        print(f"[GOOGLE OAUTH ERROR] {type(exc).__name__}")
        flash('Google sign-in failed. Please try again.', 'error')
        return redirect(url_for('login'))

    # ── 1. Already linked? The provider's subject is the durable identity. ──
    linked = oauth_utils.find_user_by_oauth(identity['provider'], identity['subject'])
    if linked:
        return _finish_social_login(linked, 'Welcome back!')

    # ── 2. Existing password account with the same address? ────────────────
    # This is the account-takeover surface, so it is gated on the provider
    # asserting the address is verified. Without that, anyone able to make
    # Google emit an address they do not own could walk into a stranger's
    # financial data.
    if not identity['email_verified']:
        flash('Google has not verified that email address, so it cannot be '
              'used to sign in. Please verify it with Google first, or sign '
              'in with your password.', 'error')
        return redirect(url_for('login'))

    existing = oauth_utils.find_user_by_email_ci(identity['email'])
    if existing:
        oauth_utils.link_oauth(existing['id'], identity['provider'], identity['subject'])
        return _finish_social_login(
            existing, 'Signed in with Google. Linked to your existing account.')

    # ── 3. Brand new person. Collect what Google cannot give us. ───────────
    # Google supplies a verified email and a name; it has no idea about the
    # company, its opening cash, or a phone number, all of which normal signup
    # requires. Nothing is written to the database until those are supplied and
    # the phone is verified, so an abandoned flow leaves no orphan account.
    session['pending_oauth'] = {
        'provider': identity['provider'],
        'subject': identity['subject'],
        'email': identity['email'],
        'full_name': identity['full_name'],
    }
    return redirect(url_for('social_complete'))


@app.route('/auth/complete', methods=['GET', 'POST'])
def social_complete():
    """Collect the details a provider cannot supply, then hand off to the
    existing verification flow rather than duplicating it."""
    pending = session.get('pending_oauth')
    if not pending:
        return redirect(url_for('login'))
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    error = None

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip() or pending['full_name']
        company_name = request.form.get('company_name', '').strip()
        starting_cash = float(request.form.get('starting_cash', 0) or 0)
        phone_code_prefix = request.form.get('phone_code', '+1')
        phone_number = request.form.get('phone_number', '').strip()
        referral = request.form.get('referral', '').strip()

        if not company_name:
            error = 'Company name is required.'
        elif not phone_number:
            error = 'Phone number is required.'

        if error:
            return render_template('social_complete.html', error=error,
                                   pending=pending, form=request.form)

        phone = f"{phone_code_prefix.replace('-CA','')} {phone_number}".strip()

        # Guard against someone completing the form twice in two tabs, or the
        # address being claimed between step 2 and here.
        from Modules import oauth_utils as _oauth
        if _oauth.find_user_by_email_ci(pending['email']):
            session.pop('pending_oauth', None)
            flash('An account with that email already exists. Please sign in.', 'error')
            return redirect(url_for('login'))

        import random
        code = str(random.randint(100000, 999999))
        expiry = datetime.now(timezone.utc) + timedelta(minutes=10)

        from Modules.oauth_utils import unusable_password_hash

        # Reuse the existing pending_signup contract verbatim so /verify-signup
        # needs no parallel implementation. email_prverified tells it the
        # address is already proven and only the phone needs a code.
        session['pending_signup'] = {
            'email': pending['email'],
            'password_hash': unusable_password_hash(),
            'full_name': full_name,
            'company_name': company_name,
            'starting_cash': starting_cash,
            'phone': phone,
            'referral_code': referral,
            'code': code,
            'expiry': expiry.isoformat(),
            'email_verified': True,
            'phone_verified': False,
            'email_prverified': True,
            'oauth_provider': pending['provider'],
            'oauth_subject': pending['subject'],
        }
        session.pop('pending_oauth', None)

        from Modules.sms_utils import send_sms_verification
        send_sms_verification(phone, code)

        return redirect(url_for('verify_signup'))

    return render_template('social_complete.html', error=error,
                           pending=pending, form={})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin_panel'))
        return redirect(url_for('dashboard'))

    error = None

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        user = User.authenticate(email, password)
        if user:
            # Check if banned
            user_data = UserDB.get_by_id(user.id)
            if user_data.get('is_banned'):
                error = 'Your account has been suspended. Contact support.'
                return render_template('login.html', error=error)

            # Check 2FA
            if user_data.get('two_factor_enabled'):
                session['pending_2fa_user_id'] = str(user.id)
                return redirect(url_for('two_fa_challenge'))

            login_user(user, remember=True)
            flash('Logged in successfully!', 'success')
            if user.is_admin:
                return redirect(url_for('admin_panel'))
            return redirect(url_for('dashboard'))
        else:
            error = 'Invalid email or password.'

    return render_template('login.html', error=error)

@app.route('/logout')
@login_required
def logout():
    # If this was a disposable demo sandbox, wipe it so nothing persists.
    if session.get('demo_sandbox'):
        try:
            UserDB.delete_demo_sandbox(
                session.get('demo_user_id'), session.get('demo_company_id'))
        except Exception as e:
            print(f"❌ demo sandbox teardown failed: {e}")
        for k in ('demo_sandbox', 'demo_company_id', 'demo_user_id'):
            session.pop(k, None)
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('home'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user_data = UserDB.get_by_email(email)

        if user_data:
            token = secrets.token_urlsafe(32)
            expiry = datetime.now(timezone.utc) + timedelta(hours=1)
            UserDB.set_reset_token(email, token, expiry)
            reset_url = f"https://www.fintoit.com/reset-password/{token}"
            send_in_background(send_password_reset, email, reset_url)

        flash('If that email exists, a reset link has been sent.', 'success')
        return redirect(url_for('forgot_password'))
    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user_data = UserDB.get_by_reset_token(token)
    if not user_data:
        flash('This reset link is invalid or has expired.', 'error')
        return redirect(url_for('forgot_password'))

    expiry = user_data.get('reset_token_expiry')
    if expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expiry:
            flash('This reset link has expired.', 'error')
            return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset_password.html', token=token)

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token)

        password_hash = generate_password_hash(password)
        UserDB.update_password(user_data['id'], password_hash)
        UserDB.clear_reset_token(user_data['id'])

        flash('Password reset successfully! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.route('/status')
def status():
    import time
    checks = []

    # Database
    try:
        start = time.time()
        Database.execute("SELECT 1")
        ms = round((time.time() - start) * 1000)
        checks.append({'name': 'Database', 'icon': '🗄️', 'status': 'ok'})
    except:
        checks.append({'name': 'Database', 'icon': '🗄️', 'status': 'error'})

    # Groq AI
    try:
        if os.getenv('GROQ_API_KEY'):
            checks.append({'name': 'AI Features', 'icon': '🤖', 'status': 'ok'})
        else:
            checks.append({'name': 'AI Features', 'icon': '🤖', 'status': 'warning'})
    except:
        checks.append({'name': 'AI Features', 'icon': '🤖', 'status': 'error'})

    # Email (Resend)
    try:
        resend_key = os.getenv('RESEND_API_KEY')
        if resend_key:
            checks.append({'name': 'Email Delivery', 'icon': '✉️', 'status': 'ok'})
        else:
            checks.append({'name': 'Email Delivery', 'icon': '✉️', 'status': 'warning', 'message': 'RESEND_API_KEY not set'})
    except:
        checks.append({'name': 'Email Delivery', 'icon': '✉️', 'status': 'error'})
    # PDF Export
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        checks.append({'name': 'PDF Export', 'icon': '📄', 'status': 'ok'})
    except:
        checks.append({'name': 'PDF Export', 'icon': '📄', 'status': 'error'})

    # File Storage
    checks.append({'name': 'File Storage', 'icon': '📁', 'status': 'ok'})

    # API
    checks.append({'name': 'API & Web App', 'icon': '🌐', 'status': 'ok'})

    overall = 'ok' if all(c['status'] == 'ok' for c in checks) else \
              'error' if any(c['status'] == 'error' for c in checks) else 'warning'
    
    checked_at = datetime.now(timezone.utc).isoformat()

    return render_template('status.html', checks=checks, overall=overall,
                           checked_at=checked_at)

@app.route('/features')
def features():
    return render_template('features.html')

def _provision_demo_sandbox():
    """Create a fresh, isolated demo company + throwaway user, seed it with sample
    data, and return a User ready to be logged in. Everything is namespaced under the
    reserved @sandbox.fintoit.com domain so abandoned sessions can be swept later."""
    token = secrets.token_hex(8)
    email = f"demo+{token}@sandbox.fintoit.com"
    pw_hash = generate_password_hash(secrets.token_urlsafe(24))
    # Opening cash comes from the demo fixture so the sandbox lands on the exact
    # documented figures ($173,000 cash, $23,316.67 net burn, 7.42 months).
    company = CompanyDB.create("Northwind Robotics (Demo)", DEMO_STARTING_CASH)
    try:
        user_data = UserDB.create(email, pw_hash, "Demo Explorer", company['id'], '', '')
    except Exception:
        # Never leave an orphan company behind if user creation fails.
        try:
            UserDB.delete_demo_sandbox(None, company['id'])
        except Exception:
            pass
        raise
    # Put the demo on the Pro plan so the sandbox showcases the full product —
    # every paid feature unlocked and no upgrade prompts.
    try:
        UserDB.update_plan(user_data['id'], 'pro')
    except Exception:
        pass
    # Seed six months of realistic sample data so the dashboard looks alive.
    try:
        TransactionDB.bulk_create(create_sample_transactions(), company['id'])
    except Exception:
        pass
    return User(
        user_data['id'], user_data['email'],
        user_data.get('full_name'), user_data.get('company_id'), False,
    )

@app.route('/demo/launch', methods=['POST'])
def demo_launch():
    """Auto-login into a private, disposable sandbox. POST-only so crawlers and link
    prefetchers can't spin up throwaway accounts."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    try:
        user = _provision_demo_sandbox()
    except Exception as e:
        print(f"❌ demo sandbox provisioning failed: {e}")
        flash('Sorry, we could not start the demo just now. Please try again.', 'error')
        return redirect(url_for('home'))
    login_user(user)  # ephemeral: no remember cookie for a throwaway session
    session['demo_sandbox'] = True
    session['demo_company_id'] = str(user.company_id)
    session['demo_user_id'] = str(user.id)
    flash("You're in a live demo sandbox: edit anything you like. It resets when you log out. 🧪", 'success')
    return redirect(url_for('dashboard'))

# ============================================
# SUBSCRIPTION GUARD
# ============================================
 
def subscription_required(plans=None):
    """
    Decorator that requires the user to have an active paid plan.
    plans: list of allowed plan names e.g. ['starter','pro']
           None means any paid plan (starter or pro).
    Admins and demo accounts bypass the check.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if current_user.is_admin or is_demo_user() or is_demo_sandbox():
                return f(*args, **kwargs)
            user_data = UserDB.get_by_id(current_user.id)
            user_plan = (user_data or {}).get('plan', 'free')
            allowed = plans if plans else ['starter', 'pro']
            if user_plan not in allowed:
                flash('This feature requires a paid plan. Upgrade to continue.', 'warning')
                return redirect(url_for('pricing'))
            return f(*args, **kwargs)
        return decorated
    return decorator
 
 
# ============================================
# PRICING & STRIPE ROUTES
# ============================================
 
@app.route('/pricing')
def pricing():
    user_plan = ''
    if current_user.is_authenticated:
        user_data = UserDB.get_by_id(current_user.id)
        user_plan = (user_data or {}).get('plan', 'free')
    # `welcome=1` is set on the post-signup redirect so the page can greet new
    # users and give the Free plan a "continue into the app" action.
    welcome = request.args.get('welcome')
    return render_template('pricing.html', plans=PLANS1,
                           user_plan=user_plan, welcome=welcome)

 
@app.route('/billing')
@login_required
def billing():
    user_data = UserDB.get_by_id(current_user.id)
    user_plan  = (user_data or {}).get('plan', 'free')
    upgraded   = request.args.get('upgraded') == '1'
    if upgraded:
        flash(f'Welcome to {user_plan.title()}! Your plan is now active. 🎉', 'success')
    return render_template('billing.html', user=user_data, plans=PLANS1,
                           user_plan=user_plan)

@app.route('/billing/portal')
@login_required
def billing_portal():
    user_data = UserDB.get_by_id(current_user.id)
    customer_id = (user_data or {}).get('stripe_customer_id')
    if not customer_id:
        flash('No billing account found. Subscribe to a plan first.', 'warning')
        return redirect(url_for('pricing'))
    try:
        return_url = request.host_url.rstrip('/') + url_for('billing')
        portal_url = create_portal_session(customer_id, return_url)
        return redirect(portal_url)
    except Exception as e:
        flash(f'Could not open billing portal: {e}', 'error')
        return redirect(url_for('billing'))

 # ── SUBSCRIBE ROUTE ──
@app.route('/subscribe/<plan_key>')
@login_required
def subscribe(plan_key):
    user_data = UserDB.get_by_id(current_user.id)
    current_plan = (user_data or {}).get('plan', 'free')
    sub_id = (user_data or {}).get('stripe_subscription_id')

    # Downgrade to free
    if plan_key == 'free':
        if sub_id:
            try:
                s = _client()
                s.Subscription.modify(sub_id, cancel_at_period_end=True)
            except Exception as e:
                print("Cancel error:", e)
        UserDB.update_plan(current_user.id, 'free')
        UserDB.set_subscription(current_user.id, None, 'free')
        flash('Switched to Free plan. Access continues until end of billing period.', 'success')
        return redirect(url_for('billing'))

    if plan_key not in PLANS:
        flash('Invalid plan.', 'error')
        return redirect(url_for('pricing'))

    if current_plan == plan_key:
        flash('Already on this plan.', 'info')
        return redirect(url_for('billing'))

    # If user already has an active subscription, modify it (proration)
    if sub_id and current_plan != 'free':
        try:
            s = _client()

            # Get current subscription to find the item ID
            subscription = s.Subscription.retrieve(sub_id)
            item_id = subscription['items']['data'][0]['id']
            new_price_id = PLANS[plan_key]['price_id']

            if not new_price_id:
                flash(f'Checkout error: Stripe price_id not set for plan \'{plan_key}\'', 'error')
                return redirect(url_for('pricing'))

            # Modify subscription with proration
            updated_sub = s.Subscription.modify(
                sub_id,
                items=[{
                    'id': item_id,
                    'price': new_price_id,
                }],
                proration_behavior='always_invoice',
                metadata={'plan': plan_key}
            )

            # Only update DB if Stripe confirms success
            if updated_sub and updated_sub.get('status') in ('active', 'trialing'):
                UserDB.update_plan(current_user.id, plan_key)
                UserDB.set_subscription(current_user.id, sub_id, plan_key)
                flash(f'Upgraded to {plan_key.title()}! You\'ve been charged the prorated amount for the remainder of your billing cycle.', 'success')
            else:
                flash(f'Upgrade failed. Your plan has not been changed. Stripe status: {updated_sub.get("status")}', 'error')

            return redirect(url_for('billing'))

        except stripe.error.CardError as e:
            flash(f'Payment failed: {e.user_message}', 'error')
            return redirect(url_for('billing'))

        except stripe.error.StripeError as e:
            flash(f'Upgrade failed: {e.user_message}', 'error')
            return redirect(url_for('billing'))

        except Exception as e:
            flash(f'Something went wrong: {str(e)}', 'error')
            return redirect(url_for('billing'))

    # No existing subscription — create new checkout session (first time paying)
    try:
        success_url = request.host_url.rstrip('/') + url_for('billing') + '?upgraded=1'
        cancel_url = request.host_url.rstrip('/') + url_for('billing')

        checkout_url = create_checkout_session(
            plan_key=plan_key,
            user_id=str(current_user.id),
            user_email=current_user.email,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return redirect(checkout_url)

    except Exception as e:
        flash(f'Checkout error: {e}', 'error')
        return redirect(url_for('pricing'))

import os
import stripe

def _client():
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    return stripe

@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        event = handle_webhook(payload, sig_header)
    except Exception as e:
        print("❌ Webhook signature verification failed:", e)
        return jsonify({'error': str(e)}), 400

    # # Idempotency: skip if event already processed
    # if WebhookLog.exists(event.id):
    #     print(f"⚡ Event {event.id} already processed, skipping.")
    #     return jsonify({'status': 'skipped'}), 200
    # WebhookLog.create(event.id, event.type, event.data.object)

    etype = event.type
    obj = event.data.object
    obj_dict = obj.to_dict()
    s = _client()

    print(f"🔥 EVENT: {etype}")
    print(obj_dict)

    # -------------------------------
    # Checkout completed
    # -------------------------------
    if etype == 'checkout.session.completed':
        customer_id = obj_dict["customer"]
        print(f"Customer {customer_id} completed checkout.")
        email = obj_dict["customer_email"]
        print(f"Customer email from checkout session: {email}")
        subscription_id= obj_dict["subscription"]
        print(f"Subscription ID from checkout session: {subscription_id}")
        # cust_id = getattr(obj_dict, 'customer', None)
        # sub_id = getattr(obj_dict, 'subscription', None)
        # metadata = getattr(obj_dict, 'metadata', {})

        plan_key = obj_dict["metadata"]["plan"]

        user = UserDB.get_by_email(email)
        if user:
            UserDB.set_stripe_customer(user['id'], customer_id)
            UserDB.set_subscription(user['id'], subscription_id, plan_key)
            UserDB.update_plan(user['id'], plan_key)
        else:
            print(f"❌ checkout.session.completed: no user found for email {email}")
        print(f"✅ Checkout completed for user {email}, plan {plan_key}, customer {customer_id}, subscription {subscription_id}")

        # if not user_id or not cust_id:
        #     print("❌ Missing user_id or customer")
        # else:
        #     user = UserDB.get_by_id(user_id)
        #     if not user:
        #         print(f"❌ User not found: {user_id}")
        #     else:
        #         # Save customer and subscription info
        #         UserDB.set_stripe_customer(user_id, cust_id)
        #         UserDB.set_subscription(user_id, sub_id, plan_key)
        #         UserDB.update_plan(user_id, plan_key)
        #         print(f"✅ Checkout completed for user {user_id}, plan {plan_key}")

    # -------------------------------
    # Subscription events
    # -------------------------------
    elif etype.startswith('customer.subscription.created'):
        cust_id = obj_dict.get('customer')
        sub_id = obj_dict.get('id')
        status = obj_dict.get('status', '')
        metadata = obj_dict.get('metadata', {})
        # Try to link to user via customer ID
        user = UserDB.get_by_stripe_customer(cust_id)
        user_id = metadata.get('user_id') if user is None else user['id']

        if user_id:
            # Always fetch latest subscription to handle out-of-order events
            latest_sub = s.Subscription.retrieve(sub_id)
            plan_key = latest_sub.metadata.get('plan', 'starter') if latest_sub.metadata else 'starter'
            new_plan = plan_key if status in ('active', 'trialing') else 'free'

            UserDB.set_subscription(user_id, sub_id, new_plan)
            UserDB.update_plan(user_id, new_plan)

            print(f"✅ Updated user {user_id} subscription to {new_plan}")
        else:
            print(f"⚠️ User not linked yet (normal): {cust_id}")

    # -------------------------------
    # Canceled
    # -------------------------------
    elif etype.startswith('customer.subscription.deleted'):
        customer_id = obj_dict["customer"]
        print(f"Customer {customer_id} completed checkout.")
        UserDB.cancel(customer_id)
        print(f"✅ Subscription canceled for customer {customer_id}, user set to free")

    # -------------------------------
    # Invoice payment failed
    # -------------------------------
    elif etype == 'invoice.payment_failed':
        cust_id = obj_dict.get('customer')
        user = UserDB.get_by_stripe_customer(cust_id)
        if user:
            UserDB.set_plan(user['id'], 'free')
            UserDB.set_subscription(user['id'], None, 'free')
            print(f"💳 Payment failed: user {user['id']} downgraded to free")

    return jsonify({'received': True}), 200

def sync_user_subscription(cust_id):
    """
    Always derive the truth from Stripe.
    This makes your system immune to event order issues.
    """

    user = UserDB.get_by_stripe_customer(cust_id)

    if not user:
        print(f"⚠️ User not linked yet (normal): {cust_id}")
        return

    try:
        # ✅ Get all subscriptions from Stripe
        subs = stripe.Subscription.list(customer=cust_id, limit=1)

        if not subs.data:
            # no active subscription → free
            UserDB.set_plan(user['id'], 'free')
            UserDB.set_subscription(user['id'], None, 'free')
            print(f"⬇️ No subscription → user {user['id']} set to free")
            return

        sub = subs.data[0]

        status = sub.status

        # ✅ derive plan from price
        try:
            price_id = sub['items']['data'][0]['price']['id']
            plan_key = next(
                (k for k, v in PLANS.items() if v.get('price_id') == price_id),
                'starter'
            )
        except Exception as e:
            print("⚠️ Plan detection failed:", e)
            plan_key = 'starter'

        if status in ('active', 'trialing'):
            UserDB.set_subscription(user['id'], sub.id, plan_key)
            UserDB.update_plan(user['id'], plan_key)

            print(f"✅ Synced user {user['id']} → {plan_key}")

        else:
            UserDB.set_subscription(user['id'], sub.id, 'free')
            UserDB.update_plan(user['id'], 'free')

            print(f"⬇️ Subscription inactive → user {user['id']} downgraded")

    except Exception as e:
        print("❌ Sync error:", e)
# ============================================
# 2FA ROUTES
# ============================================

@app.route('/2fa/setup', methods=['GET', 'POST'])
@login_required
@demo_readonly

def setup_2fa():
    user_data = UserDB.get_by_id(current_user.id)

    if request.method == 'POST':
        method = request.form.get('method', 'email')

        if method == 'authenticator':
            # Generate TOTP secret
            secret = pyotp.random_base32()
            totp = pyotp.TOTP(secret)
            otp_uri = totp.provisioning_uri(
                name=current_user.email,
                issuer_name='Fintoit'
            )
            # Generate QR code
            qr = qrcode.make(otp_uri)
            buffer = BytesIO()
            qr.save(buffer, format='PNG')
            qr_b64 = base64.b64encode(buffer.getvalue()).decode()

            session['pending_2fa_secret'] = secret
            session['pending_2fa_method'] = 'authenticator'

            return render_template('2fa_setup.html',
                                   method='authenticator',
                                   qr_code=qr_b64,
                                   secret=secret,
                                   user=user_data)

        elif method == 'email':
            session['pending_2fa_method'] = 'email'
            return render_template('2fa_setup.html',
                                   method='email',
                                   user=user_data)

    return render_template('2fa_setup.html', method=None, user=user_data)


@app.route('/2fa/verify-setup', methods=['POST'])
@login_required
@demo_readonly

def verify_2fa_setup():
    method = session.get('pending_2fa_method')
    code = request.form.get('code', '').strip()

    if method == 'authenticator':
        secret = session.get('pending_2fa_secret')
        totp = pyotp.TOTP(secret)
        if totp.verify(code):
            UserDB.enable_2fa(current_user.id, 'authenticator', secret)
            session.pop('pending_2fa_secret', None)
            session.pop('pending_2fa_method', None)
            flash('Authenticator app 2FA enabled!', 'success')
            return redirect(url_for('profile'))
        else:
            flash('Invalid code. Please try again.', 'error')
            return redirect(url_for('setup_2fa'))

    elif method == 'email':
        # Send verification code
        import random
        code = str(random.randint(100000, 999999))
        from datetime import datetime, timedelta, timezone
        expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
        UserDB.save_2fa_code(current_user.id, code, expiry)

        from Modules.email_utils import send_email
        send_email(
            current_user.email,
            'Your Fintoit 2FA Setup Code',
            f"""Your Fintoit two-factor authentication setup code is:

{code}

This code expires in 10 minutes.

If you didn't request this, ignore this email.

— Fintoit"""
        )
        session['pending_2fa_method'] = 'email'
        flash('Verification code sent to your email!', 'success')
        return render_template('2fa_verify_setup.html')


@app.route('/2fa/confirm-email-setup', methods=['POST'])
@login_required
@demo_readonly

def confirm_email_2fa_setup():
    code = request.form.get('code', '').strip()
    user_data = UserDB.get_by_id(current_user.id)

    stored_code = user_data.get('two_factor_code')
    expiry = user_data.get('two_factor_code_expiry')

    from datetime import datetime, timezone
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    if stored_code == code and expiry and datetime.now(timezone.utc) < expiry:
        UserDB.enable_2fa(current_user.id, 'email')
        UserDB.clear_2fa_code(current_user.id)
        session.pop('pending_2fa_method', None)
        flash('Email 2FA enabled!', 'success')
        return redirect(url_for('profile'))
    else:
        flash('Invalid or expired code. Please try again.', 'error')
        return redirect(url_for('setup_2fa'))


@app.route('/2fa/disable', methods=['POST'])
@login_required
@demo_readonly

def disable_2fa():
    UserDB.disable_2fa(current_user.id)
    flash('Two-factor authentication disabled.', 'success')
    return redirect(url_for('profile'))


@app.route('/2fa/challenge', methods=['GET', 'POST'])
def two_fa_challenge():
    """2FA verification during login"""
    if 'pending_2fa_user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['pending_2fa_user_id']
    user_data = UserDB.get_by_id(user_id)
    method = user_data.get('two_factor_method', 'email')

    if request.method == 'GET' and method == 'email':
        # Auto-send code
        import random
        from datetime import datetime, timedelta, timezone
        code = str(random.randint(100000, 999999))
        expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
        UserDB.save_2fa_code(user_id, code, expiry)
        from Modules.email_utils import send_email
        send_email(
            user_data['email'],
            'Your Fintoit login code',
            f"""Your Fintoit login verification code is:

{code}

This code expires in 10 minutes.
If you didn't try to log in, please secure your account.

— Fintoit"""
        )

    if request.method == 'POST':
        code = request.form.get('code', '').strip()

        if method == 'authenticator':
            secret = user_data.get('totp_secret')
            totp = pyotp.TOTP(secret)
            valid = totp.verify(code)
        else:
            stored = user_data.get('two_factor_code')
            expiry = user_data.get('two_factor_code_expiry')
            from datetime import datetime, timezone
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            valid = stored == code and expiry and datetime.now(timezone.utc) < expiry

        if valid:
            from Modules.auth import User
            user = User.get(user_id)
            login_user(user, remember=True)
            UserDB.clear_2fa_code(user_id)
            session.pop('pending_2fa_user_id', None)
            flash('Logged in successfully!', 'success')
            if user.is_admin:
                return redirect(url_for('admin_panel'))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid or expired code. Please try again.', 'error')

    return render_template('2fa_challenge.html', method=method, email=user_data.get('email'))

# ============================================
# DASHBOARD & AI ROUTES
# ============================================

@app.route('/dashboard')
@login_required
@demo_readonly
def dashboard():
    company_id = current_user.company_id

    # One user lookup for the whole request. This previously ran twice here:
    # once into an unused variable, and again as a separate SELECT just to read
    # `plan` (on top of the lookup already done in check_banned).
    user_data = UserDB.get_by_id(current_user.id) or {}
    user_plan = user_data.get('plan', 'free')

    # Everything below derives purely from the company's transactions + starting
    # cash, so it is cached per company and invalidated the instant any
    # transaction is written (see Modules/cache.py).
    def _compute_dashboard():
        transactions = TransactionDB.get_all(company_id)
        starting_cash = CompanyDB.get_starting_cash(company_id)
        metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)
        # Canonical burn/runway/MRR. The dashboard and the AI must both read
        # these from Modules.metrics so they can never disagree again.
        canon = canonical_metrics.compute_metrics(transactions, starting_cash)
        # KPIs take the canonical object so burn multiple is derived from the
        # same net burn the tiles show, rather than a second, gross figure.
        kpis = FinancialCalculator.calculate_kpis(transactions, starting_cash, canon=canon)

        cat_map = {}
        for t in transactions:
            if t['type'] == 'expense':
                cat = t['category']
                amt = float(t['amount']) if isinstance(t['amount'], Decimal) else t['amount']
                cat_map[cat] = cat_map.get(cat, 0) + amt
        sorted_cats = sorted(cat_map.items(), key=lambda x: x[1], reverse=True)[:5]

        anomalies = AnomalyDetector.detect(transactions, metrics['monthly_breakdown'])
        forecast_data = FinancialCalculator.forecast_cash_flow(
            metrics['monthly_breakdown'],
            metrics['current_cash'],
            months_ahead=6
        )
        return {
            'metrics': metrics,
            'canon': canon,
            'kpis': kpis,
            'sorted_cats': sorted_cats,
            'anomalies': anomalies,
            'forecast_data': forecast_data,
        }

    computed = metrics_cache.get_or_set('dashboard', company_id, _compute_dashboard)
    metrics = computed['metrics']
    canon = computed['canon']
    kpis = computed['kpis']
    sorted_cats = computed['sorted_cats']
    anomalies = computed['anomalies']
    forecast_data = computed['forecast_data']

    budget_alerts = BudgetDB.get_alerts(company_id, threshold=0.8)

    dashboard_data = {
        'cash': canon['cash_balance'],
        # Gross burn is what the tile has always shown; net burn drives runway.
        'burn_rate': canon['gross_burn_monthly'],
        'net_burn': canon['net_burn_monthly'],
        # Published so the two burn tiles visibly reconcile on screen:
        # gross burn - revenue = net burn, and cash / net burn = runway.
        'revenue_monthly': canon['revenue_monthly'],
        'runway': canon['runway_months'],          # None when profitable — no 999 sentinel
        'runway_display': canon['runway_display'],
        'is_profitable': canon['is_profitable'],
        'window_months': canon['window_months'],
        'mrr': canon['mrr'],
        'revenue_growth': metrics['revenue_growth'],
        'monthly_data': metrics['monthly_breakdown'],
        'categories': sorted_cats,
        'forecast': forecast_data
    }

    return render_template('dashboard.html', metrics=dashboard_data,
                           anomalies=anomalies, kpis=kpis, user_plan=user_plan, budget_alerts=budget_alerts)

@app.route('/ai-insights')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def ai_insights():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)

    if len(transactions) < 5:
        flash('Add more transactions (at least 5) for AI analysis.', 'warning')
        return redirect(url_for('dashboard'))

    starting_cash = CompanyDB.get_starting_cash(company_id)
    metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)

    try:
        # Generate Insights via Groq
        raw_insights = AIFinancialAdvisor.generate_insights(
            metrics, metrics['monthly_breakdown'], transactions
        )
        formatted = format_insights_for_display(raw_insights)

        if formatted.get('error'):
            flash(f"AI Error: {formatted.get('message')}", "error")
            return redirect(url_for('dashboard'))

        return render_template('ai_insights.html', insights=formatted)

    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f"Error: {str(e)}", "error")
        return redirect(url_for('dashboard'))

# -----------------------------
# AI Chat
# -----------------------------
@app.route('/ai-chat-page')
@login_required
@subscription_required(['starter', 'pro'])
def ai_chat_page():
    return render_template('ai_chat.html')

@app.route('/ai-chat', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def ai_chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400

        company_id = current_user.company_id

        # -----------------------------
        # 💰 Financial Aggregates
        # -----------------------------
        totals = Database.execute_one("""
            SELECT 
                COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) as income,
                COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) as expense
            FROM transactions
            WHERE company_id = %s
        """, (company_id,))

        starting_cash_row = Database.execute_one(
            "SELECT starting_cash FROM companies WHERE id = %s",
            (company_id,)
        )

        starting_cash = Decimal(starting_cash_row['starting_cash'] or 0)

        total_income = Decimal(totals['income'] or 0)
        total_expense = Decimal(totals['expense'] or 0)

        current_cash = starting_cash + total_income - total_expense

        # -----------------------------
        # 🔥 Canonical metrics (single source of truth)
        # -----------------------------
        # This previously summed expenses WHERE created_at >= NOW() - 30 days.
        # created_at is the row's insert time, not the transaction's business
        # date, so a demo seeded in one shot -- or any CSV import of historical
        # data -- counted every imported month as "last 30 days" and reported it
        # as one month's burn ($91,800 instead of $15,300). We now read the same
        # computed object the dashboard renders.
        _ai_txs = TransactionDB.get_all(company_id)
        canon = canonical_metrics.compute_metrics(_ai_txs, starting_cash)
        monthly_burn = Decimal(str(canon['gross_burn_monthly']))

        # -----------------------------
        # 📈 Revenue (last 30d)
        # -----------------------------
        # Same created_at defect as burn: use the canonical MRR (revenue of the
        # most recent COMPLETE month) so the AI and the dashboard tile agree.
        mrr = Decimal(str(canon['mrr']))

        # -----------------------------
        # 🧮 Runway
        # -----------------------------
        # Runway is NOT recomputed here. It is None whenever the company is
        # profitable or at breakeven; dividing cash by burn unconditionally is
        # what produced a confident "3.58 months" for a company whose cash was
        # growing every month.
        runway_months = canon['runway_months']

        # -----------------------------
        # 🧾 Top Categories
        # -----------------------------
        top_cats_rows = Database.execute("""
            SELECT category, SUM(amount) as total
            FROM transactions
            WHERE company_id = %s AND type = 'expense'
            GROUP BY category
            ORDER BY total DESC
            LIMIT 5
        """, (company_id,))

        top_cats = [
            (row['category'], Decimal(row['total']))
            for row in top_cats_rows
        ]

        # -----------------------------
        # 📊 Revenue Trend
        # -----------------------------
        trend_rows = Database.execute("""
            SELECT DATE_TRUNC('month', date) as month,
                   SUM(amount) as revenue
            FROM transactions
            WHERE company_id = %s AND type = 'income'
            GROUP BY month
            ORDER BY month DESC
            LIMIT 3
        """, (company_id,))

        revenue_trend = [
            f"{row['month'].strftime('%b %Y')}: ${Decimal(row['revenue']):,.0f}"
            for row in trend_rows
        ]

        # -----------------------------
        # 🧠 Chat History
        # -----------------------------
        since = datetime.utcnow() - timedelta(days=7)

        history_rows = Database.execute("""
            SELECT role, content
            FROM chat_history
            WHERE user_id = %s
              AND created_at >= %s
            ORDER BY created_at ASC
            LIMIT 20
        """, (current_user.id, since))

        history = [
            {"role": row["role"], "content": row["content"]}
            for row in history_rows
        ]

        # -----------------------------
        # 🧠 Intent Boost
        # -----------------------------
        if "how am i doing" in user_message.lower():
            user_message = "Give me a full financial health summary with risks and recommendations."

        # -----------------------------
        # 🔭 Scenario ("what if") routing
        # -----------------------------
        # A scenario question genuinely needs new computation, so it cannot be
        # answered by quoting the snapshot -- but the model must not do the
        # projection in prose either. Parse the request, compute it
        # deterministically, and hand the model a finished answer to read out.
        #
        # The previous prompt told the model to answer "what if" questions
        # qualitatively and point the user at the Scenario Planning page. That
        # turned the hero example on the marketing site -- "what's my runway if
        # I hire two engineers?" -- into a refusal, which is the first thing
        # every visitor tries.
        scenario_block = ''
        try:
            scenario_params = canonical_metrics.parse_scenario_request(user_message)
            if scenario_params and canon['has_data']:
                scenario = canonical_metrics.compute_scenario(canon, **scenario_params)
                ladder = None
                if scenario_params.get('hires'):
                    ladder = canonical_metrics.hire_ladder(
                        canon,
                        salary_per_hire=scenario_params.get('salary_per_hire'),
                        max_hires=max(3, scenario_params['hires']))
                scenario_block = canonical_metrics.scenario_context(scenario, ladder)
        except Exception as scenario_error:
            # Never let a parsing failure take the chat down. Without the block
            # the rules tell the model to answer directionally and ask for the
            # monthly cost, which is still a usable answer.
            print(f"[AI CHAT SCENARIO SKIPPED] {scenario_error}")
            scenario_block = ''

        # -----------------------------
        # 🤖 AI Context
        # -----------------------------
        context = f"""You are an expert startup CFO assistant.

Give concise, actionable insights using the real numbers below.

{canonical_metrics.ai_context(canon)}

Top expense categories (totals across all history, NOT monthly):
{', '.join([f"{c}: ${a:,.0f}" for c, a in top_cats]) or "None"}

Revenue by month:
{', '.join(revenue_trend) or "No data"}

{scenario_block}

{canonical_metrics.AI_RULES}
- Highlight growth or risk. Be specific, not generic.
"""

        messages = [{"role": "system", "content": context}]
        messages += history[-4:]
        messages.append({"role": "user", "content": user_message})

        # -----------------------------
        # 🚀 Groq Call
        # -----------------------------
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.6,
            max_tokens=400
        )

        reply = (
            response.choices[0].message.content.strip()
            if response and response.choices
            else "No response generated."
        )

        # -----------------------------
        # 💾 Save Chat
        # -----------------------------
        Database.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (%s,%s,%s)",
            (current_user.id, 'user', user_message),
            fetch=False
        )

        Database.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (%s,%s,%s)",
            (current_user.id, 'assistant', reply),
            fetch=False
        )

        return jsonify({'reply': reply})

    except Exception as e:
        print(f"[AI CHAT ERROR] {e}")
        return jsonify({'reply': "Connection error. Please try again."}), 500

@app.route('/ai-chat/history', methods=['GET'])
@login_required
@subscription_required(['starter', 'pro'])
def ai_chat_history():
    try:
        since = datetime.utcnow() - timedelta(days=7)

        rows = Database.execute("""
            SELECT role, content, created_at
            FROM chat_history
            WHERE user_id = %s AND created_at >= %s
            ORDER BY created_at ASC
            LIMIT 200
        """, (current_user.id, since))

        history = [
            {
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"].isoformat()
            }
            for row in rows
        ]

        return jsonify({"history": history})

    except Exception as e:
        print(f"[CHAT HISTORY ERROR] {e}")
        return jsonify({"error": "Failed to load chat history"}), 500

@app.route('/ai-chat-clear', methods=['POST'])
@login_required
def clear_ai_chat():
    try:
        Database.execute(
            "DELETE FROM chat_history WHERE user_id = %s",
            (current_user.id,),
            fetch=False
        )
        return jsonify({"success": True})
    except Exception as e:
        print(f"[CLEAR CHAT ERROR] {e}")
        return jsonify({"error": "Failed to clear chat"}), 500

@app.route('/scenario-planning', methods=['GET', 'POST'])
@login_required
@subscription_required(['starter', 'pro'])
def scenario_planning():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)

    # Baseline comes from the canonical object so this page, the dashboard and
    # the AI chat all start from the same burn and cash figures.
    canon = canonical_metrics.compute_metrics(transactions, starting_cash)
    metrics['current_cash'] = canon['cash_balance']
    metrics['monthly_burn'] = canon['gross_burn_monthly']
    metrics['mrr'] = canon['mrr']
    metrics['runway_months'] = canon['runway_months']
    metrics['runway_display'] = canon['runway_display']

    scenario_result = None

    if request.method == 'POST':
        # Get current baseline
        current_cash = canon['cash_balance']
        monthly_burn = canon['gross_burn_monthly']
        mrr = canon['mrr']

        # Get scenario inputs
        revenue_change_pct = float(request.form.get('revenue_change', 0))
        expense_change = float(request.form.get('expense_change', 0))
        one_time_cost = float(request.form.get('one_time_cost', 0))
        months = int(request.form.get('months', 12))
        scenario_name = request.form.get('scenario_name', 'Custom Scenario')

        # Runway for both the baseline and the scenario comes from the shared
        # engine, so a profitable company gets None here exactly as it does on
        # the dashboard instead of a 999 sentinel that renders as a real number.
        modelled = canonical_metrics.compute_scenario(
            canon,
            monthly_expense_delta=expense_change,
            monthly_revenue_delta=canon['revenue_monthly'] * (revenue_change_pct / 100.0),
            cash_delta=-one_time_cost,
            label=scenario_name)

        # Monthly projection uses the same deltas the engine was given.
        new_mrr = mrr * (1 + revenue_change_pct / 100)
        new_burn = modelled['gross_burn_monthly']
        new_net_burn = modelled['net_burn_monthly']
        baseline_net_burn = canon['net_burn_monthly']

        # Project month by month
        baseline_cash = current_cash
        scenario_cash = current_cash - one_time_cost
        baseline_months = []
        scenario_months = []

        for i in range(1, months + 1):
            baseline_cash -= baseline_net_burn
            scenario_cash -= new_net_burn
            from datetime import date
            today = date.today()
            month_num = today.month + i
            year = today.year + (month_num - 1) // 12
            month = ((month_num - 1) % 12) + 1
            label = f"{year}-{month:02d}"
            baseline_months.append({'month': label, 'cash': round(baseline_cash, 2)})
            scenario_months.append({'month': label, 'cash': round(scenario_cash, 2)})

        # Runway, from the shared engine. None means profitable — the template
        # renders that as ∞ rather than treating 999 as a month count.
        new_runway = modelled['runway_months']
        baseline_runway = canon['runway_months']
        runway_diff = modelled['runway_delta_months']

        scenario_result = {
            'name': scenario_name,
            'revenue_change_pct': revenue_change_pct,
            'expense_change': expense_change,
            'one_time_cost': one_time_cost,
            'new_mrr': round(new_mrr, 2),
            'new_burn': round(new_burn, 2),
            'new_net_burn': round(new_net_burn, 2),
            'baseline_net_burn': round(baseline_net_burn, 2),
            'new_runway': new_runway,
            'new_runway_display': modelled['runway_display'],
            'baseline_runway': baseline_runway,
            'baseline_runway_display': canon['runway_display'],
            'runway_diff': runway_diff,
            'final_cash': scenario_months[-1]['cash'],
            'baseline_months': baseline_months,
            'scenario_months': scenario_months,
            'months': months
        }

    return render_template('scenario_planning.html',
                           metrics=metrics,
                           scenario=scenario_result)

@app.route('/pl-comparison')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def pl_comparison():
    company_id = current_user.company_id
    current_year = datetime.now().year
    year = int(request.args.get('year', current_year))
    data = FinancialStatements.get_pl_comparison(company_id, year)
    years = list(range(current_year, current_year - 5, -1))
    return render_template('pl_comparison.html', data=data, years=years, current_year=current_year)
# ============================================
# DATA MANAGEMENT ROUTES
# ============================================

@app.route('/transactions')
@login_required
@demo_readonly
def transactions():
    data = TransactionDB.get_all(current_user.company_id)
    return render_template('transactions.html', transactions=data)

@app.route('/add-transaction', methods=['GET', 'POST'])
@login_required
@demo_readonly
def add_transaction():
    if request.method == 'POST':
        try:
            recurring = request.form.get('recurring') == 'on'
            tx = {
                'date': request.form['date'],
                'amount': float(request.form['amount']),
                'type': request.form['type'],
                'category': request.form['category'],
                'description': request.form.get('description', ''),
                'recurring': recurring,
                'recurring_frequency': request.form.get('recurring_frequency', 'monthly') if recurring else None
            }
            TransactionDB.create(tx, current_user.company_id)
            flash('Transaction added!', 'success')
            return redirect(url_for('transactions'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
    return render_template('add_transaction.html')

@app.route('/transactions/delete/<transaction_id>', methods=['POST'])
@login_required
@demo_readonly
def delete_transaction(transaction_id):
    TransactionDB.delete(transaction_id, current_user.company_id)
    flash('Transaction deleted.', 'success')
    return redirect(url_for('transactions'))

@app.route('/transactions/edit/<transaction_id>', methods=['POST'])
@login_required
@demo_readonly
def edit_transaction(transaction_id):
    try:
        recurring = request.form.get('recurring') == 'on'
        tx = {
            'date': request.form['date'],
            'amount': float(request.form['amount']),
            'type': request.form['type'],
            'category': request.form['category'],
            'description': request.form.get('description', ''),
            'recurring': recurring,
            'recurring_frequency': request.form.get('recurring_frequency', 'monthly') if recurring else None
        }
        TransactionDB.update(transaction_id, tx, current_user.company_id)
        flash('Transaction updated!', 'success')
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
    return redirect(url_for('transactions'))


@app.route('/import-csv', methods=['GET', 'POST'])
@login_required
@demo_readonly
def import_csv():
    if request.method == 'POST':
        file = request.files.get('csv_file')
        if file and file.filename.endswith('.csv'):
            content = file.read()
            txs = parse_csv(content)
            if txs:
                TransactionDB.bulk_create(txs, current_user.company_id)
                flash(f'Imported {len(txs)} transactions!', 'success')
                return redirect(url_for('transactions'))
        flash('Invalid CSV file.', 'error')
    return render_template('import_csv.html')

# ============================================
# PROFILE ROUTES
# ============================================

@app.route('/profile', methods=['GET', 'POST'])
@login_required
@demo_readonly
def profile():
    user_data = UserDB.get_by_id(current_user.id)
    company = CompanyDB.get(current_user.company_id)

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_profile':
            full_name = request.form.get('full_name', '').strip()
            email = request.form.get('email', '').strip().lower()
            phone_code = request.form.get('phone_code', '')
            phone_number = request.form.get('phone_number', '').strip()
            phone = f"{phone_code.replace('-CA','')} {phone_number}".strip() if phone_number else ''
            company_name = request.form.get('company_name', '').strip()

            existing = UserDB.get_by_email(email)
            if existing and str(existing['id']) != str(current_user.id):
                flash('That email is already in use.', 'error')
                return redirect(url_for('profile'))

            UserDB.update_profile(current_user.id, full_name, email, phone, company_name)
            if company_name:
                Database.execute(
                    "UPDATE companies SET name = %s WHERE id = %s",
                    (company_name, current_user.company_id), fetch=False
                )
            flash('Profile updated!', 'success')
            return redirect(url_for('profile'))

        elif action == 'change_password':
            current_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')

            if not check_password_hash(user_data['password_hash'], current_pw):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('profile'))

            if len(new_pw) < 8:
                flash('New password must be at least 8 characters.', 'error')
                return redirect(url_for('profile'))

            if new_pw != confirm_pw:
                flash('Passwords do not match.', 'error')
                return redirect(url_for('profile'))

            UserDB.update_password(current_user.id, generate_password_hash(new_pw))
            flash('Password changed successfully!', 'success')
            return redirect(url_for('profile'))

        elif action == 'delete_account':
            confirm = request.form.get('confirm_delete', '')
            if confirm != user_data['email']:
                flash('Email confirmation did not match.', 'error')
                return redirect(url_for('profile'))

            # Grab IDs BEFORE logging out
            user_id = str(current_user.id)
            company_id = str(current_user.company_id)

            logout_user()
            UserDB.delete_account(user_id, company_id)
            flash('Your account has been deleted.', 'success')
            return redirect(url_for('home'))

    return render_template('profile.html', user=user_data, company=company)

@app.route('/profile/upload-avatar', methods=['POST'])
@login_required
@demo_readonly
def upload_avatar():
    file = request.files.get('avatar')
    if file and file.filename:
        if not file.mimetype.startswith('image/'):
            flash('Please upload an image file.', 'error')
            return redirect(url_for('profile'))
        data = file.read()
        if len(data) > 5 * 1024 * 1024:  # 5MB limit
            flash('Image must be under 5MB.', 'error')
            return redirect(url_for('profile'))
        UserDB.save_avatar(current_user.id, data, file.mimetype)
        flash('Profile picture updated!', 'success')
    return redirect(url_for('profile'))

@app.route('/profile/delete-avatar', methods=['POST'])
@login_required
@demo_readonly
def delete_avatar():
    UserDB.delete_avatar(current_user.id)
    flash('Profile picture removed.', 'success')
    return redirect(url_for('profile'))

@app.route('/profile/avatar/<user_id>')
@login_required
@demo_readonly
def get_avatar(user_id):
    user_data = UserDB.get_by_id(user_id)
    if not user_data:
        return redirect('https://ui-avatars.com/api/?name=User'
                        '&background=3b82f6&color=ffffff&bold=true')
    # Prevent cross-tenant avatar/name enumeration (IDOR): only self, same company, or admin.
    if (str(user_data.get('id')) != str(current_user.id)
            and user_data.get('company_id') != current_user.company_id
            and not current_user.is_admin):
        abort(403)
    if not user_data.get('avatar_data'):
        return redirect('https://ui-avatars.com/api/?name=' +
                       (user_data.get('full_name') or 'User').replace(' ', '+') +
                       '&background=3b82f6&color=ffffff&bold=true')
    return send_file(
        io.BytesIO(bytes(user_data['avatar_data'])),
        mimetype=user_data['avatar_type']
    )

@app.route('/load-demo-data')
@login_required
@demo_readonly
def load_demo_data():
    TransactionDB.delete_all(current_user.company_id)
    demo_txs = create_sample_transactions()
    TransactionDB.bulk_create(demo_txs, current_user.company_id)
    flash('Demo data loaded!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/clear-demo-data')
@login_required
@demo_readonly
def clear_demo_data():
    TransactionDB.delete_all(current_user.company_id)
    flash('Demo data cleared!', 'success')
    return redirect(url_for('dashboard'))

# ============================================
# EXPORT ROUTES
# ============================================

@app.route('/export-csv')
@login_required
@demo_readonly

def export_csv():
    txs = TransactionDB.get_all(current_user.company_id)
    csv_body = generate_csv(txs)
    return send_file(
        io.BytesIO(csv_body.encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='transactions.csv'
    )

@app.route('/export-pdf')
@login_required
@demo_readonly

def export_pdf():
    company_id = current_user.company_id
    txs = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    company = CompanyDB.get(company_id)
    metrics = FinancialCalculator.get_all_metrics(txs, starting_cash)
    
    pdf_body = generate_pdf_report(company['name'], metrics, metrics['monthly_breakdown'], txs)
    return send_file(
        io.BytesIO(pdf_body),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='financial_report.pdf'
    )

@app.route('/download-template')
def download_template():
    template = "Date,Type,Category,Description,Amount,Recurring\n2024-01-01,income,Sales,Demo,5000.00,No"
    return send_file(
        io.BytesIO(template.encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='template.csv'
    )

@app.route('/investor-report', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def investor_report():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)
    company = CompanyDB.get(company_id)

    # Overlay the canonical figures. Investors are the last audience that should
    # see a number this app contradicts elsewhere.
    canon = canonical_metrics.compute_metrics(transactions, starting_cash)
    metrics['current_cash'] = canon['cash_balance']
    metrics['monthly_burn'] = canon['gross_burn_monthly']
    metrics['net_burn'] = canon['net_burn_monthly']
    metrics['mrr'] = canon['mrr']
    metrics['runway_months'] = canon['runway_months']
    metrics['runway_display'] = canon['runway_display']

    if request.method == 'POST':
        from groq import Groq

        company_info = {
            'tagline': request.form.get('tagline', ''),
            'stage': request.form.get('stage', ''),
            'use_of_funds': request.form.get('use_of_funds', '')
        }

        # Build financial summary for AI
        categories = {}
        for t in transactions:
            if t['type'] == 'expense':
                cat = t['category']
                categories[cat] = categories.get(cat, 0) + float(t['amount'])
        top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]

        prompt = f"""You are writing a professional financial narrative for a startup investor report.

Company: {company['name']}
Stage: {company_info.get('stage', 'Early stage')}
Tagline: {company_info.get('tagline', '')}

Financial Data:
- Current Cash: ${metrics['current_cash']:,.0f}
- Monthly Burn Rate: ${metrics['monthly_burn']:,.0f}
- Runway: {metrics['runway_months'] or 'N/A'} months
- MRR: ${metrics['mrr']:,.0f}
- Revenue Growth: {metrics['revenue_growth']}%
- Top Expenses: {', '.join([f"{c}: ${a:,.0f}" for c, a in top_cats])}

Use of Funds: {company_info.get('use_of_funds', 'General operations')}

Write a 3-paragraph professional financial narrative for investors covering:
1. Current financial position and key metrics
2. Revenue trends and growth trajectory  
3. Use of funds and path to next milestone

Be concise, factual, and professional. Use specific numbers. Do not use bullet points."""

        try:
            groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))
            response = groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a professional CFO writing investor reports."},
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.5,
                max_tokens=600
            )
            ai_narrative = response.choices[0].message.content
        except Exception as e:
            ai_narrative = f"{company['name']} is an early-stage startup with ${metrics['current_cash']:,.0f} in current cash, a monthly burn rate of ${metrics['monthly_burn']:,.0f}, and {metrics['runway_months'] or 'N/A'} months of runway. The company is focused on achieving key milestones with disciplined financial management."

        pdf = generate_investor_report(
            company['name'], metrics,
            metrics['monthly_breakdown'],
            transactions, ai_narrative, company_info
        )

        return send_file(
            io.BytesIO(pdf),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{company['name'].replace(' ', '_')}_Investor_Report_{datetime.now().strftime('%Y%m')}.pdf"
        )

    return render_template('investor_report.html', metrics=metrics, company=company)

@app.route('/ai-categorize', methods=['POST'])
@login_required
@demo_readonly
def ai_categorize():
    from groq import Groq
    description = request.json.get('description', '').strip()
    tx_type = request.json.get('type', 'expense')

    if not description or len(description) < 3:
        return jsonify({'category': ''})

    try:
        groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))
        response = groq_client.chat.completions.create(
            messages=[{
                "role": "user",
                "content": f"""Categorize this {tx_type} transaction into exactly ONE category.

Transaction description: "{description}"

For expenses, choose from: Salaries, Software, Marketing, Office, Travel, Equipment, Professional Services, Utilities, Insurance, Taxes, Other
For income, choose from: Sales, Services, Investment, Grant, Refund, Other

Reply with ONLY the category name, nothing else."""
            }],
            model="llama-3.3-70b-versatile",
            temperature=0,
            max_tokens=10
        )
        category = response.choices[0].message.content.strip()
        return jsonify({'category': category})
    except Exception:
        return jsonify({'category': ''})

@app.route('/ar-aging')
@login_required
@demo_readonly

def ar_aging():
    company_id = current_user.company_id
    aging = InvoiceDB.get_aging(company_id)
    return render_template('ar_aging.html', aging=aging)

# ============================================
# ACCOUNTING ROUTES
# ============================================
@app.route('/accounting')
@login_required
@demo_readonly
@subscription_required(['pro'])
def accounting():
    """Accounting hub — a live snapshot of the books (P&L, cash, receivables) plus a
    directory of every statement, ledger and report in the suite."""
    company_id = current_user.company_id

    # Ensure the standard chart of accounts exists
    ChartOfAccountsDB.create_standard_accounts(company_id)

    today = datetime.now()
    year = today.year
    year_start = f"{year}-01-01"
    today_str = today.strftime('%Y-%m-%d')

    # YTD Profit & Loss — single source of truth for revenue / expense / net income,
    # including the category-level breakdown used for the on-page P&L snapshot.
    pl = FinancialStatements.get_income_statement(company_id, year_start, today_str)

    # Cash position + trailing-6-month P&L, computed from transactions already in memory.
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    total_income = sum(float(t['amount']) for t in transactions if t['type'] == 'income')
    total_expense = sum(float(t['amount']) for t in transactions if t['type'] == 'expense')
    cash_on_hand = starting_cash + total_income - total_expense

    # Trailing six calendar months, most recent last.
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    seq, y, mo = [], today.year, today.month
    for _ in range(6):
        seq.append((y, mo))
        mo -= 1
        if mo == 0:
            mo, y = 12, y - 1
    seq.reverse()
    monthly = []
    for (yy, mm) in seq:
        pref = f"{yy}-{mm:02d}"
        inc = sum(float(t['amount']) for t in transactions
                  if t['type'] == 'income' and str(t['date'])[:7] == pref)
        exp = sum(float(t['amount']) for t in transactions
                  if t['type'] == 'expense' and str(t['date'])[:7] == pref)
        monthly.append({'label': month_names[mm - 1], 'income': round(inc, 2),
                        'expense': round(exp, 2), 'net': round(inc - exp, 2)})
    monthly_max = max([max(m['income'], m['expense']) for m in monthly] + [1.0])

    # Receivables (with aging) + payables
    invoice_summary = InvoiceDB.get_summary(company_id)
    bill_summary = BillDB.get_summary(company_id)
    aging = InvoiceDB.get_aging(company_id)

    # Chart of accounts counts by type
    accounts = ChartOfAccountsDB.get_all(company_id)
    account_counts = {}
    for acc in accounts:
        acc_type = acc['account_type']
        account_counts[acc_type] = account_counts.get(acc_type, 0) + 1

    stats = {
        'ytd_revenue': pl['total_revenue'],
        'ytd_expenses': pl['total_expenses'],
        'net_income': pl['net_income'],
        'profit_margin': pl['profit_margin'],
        'cash_on_hand': cash_on_hand,
        'outstanding_invoices': invoice_summary['outstanding'],
        'outstanding_bills': bill_summary['outstanding'],
        'total_invoices': invoice_summary['total_invoices'],
        'total_bills': bill_summary['total_bills'],
        'account_counts': account_counts,
        'fiscal_year': year,
        'as_of': today.strftime('%b %d, %Y'),
    }

    return render_template('accounting.html', stats=stats, pl=pl,
                           aging=aging, monthly=monthly, monthly_max=monthly_max)


@app.route('/balance-sheet')
@login_required
@demo_readonly
@subscription_required(['pro'])
def balance_sheet():
    """Balance Sheet"""
    company_id = current_user.company_id
    as_of_date = request.args.get('as_of_date', datetime.now().strftime('%Y-%m-%d'))
    starting_cash = CompanyDB.get_starting_cash(company_id)
    
    bs = FinancialStatements.get_balance_sheet(company_id, starting_cash, as_of_date)
    return render_template('balance_sheet.html', balance_sheet=bs)


@app.route('/income-statement')
@login_required
@demo_readonly
@subscription_required(['pro'])
def income_statement():
    """Income Statement (P&L)"""
    company_id = current_user.company_id
    today = datetime.now()
    
    start_date = request.args.get('start_date', f"{today.year}-01-01")
    end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
    
    stmt = FinancialStatements.get_income_statement(company_id, start_date, end_date)
    return render_template('income_statement.html', income_stmt=stmt, current_year=today.year)


@app.route('/cash-flow')
@login_required
@demo_readonly
@subscription_required(['pro'])
def cash_flow():
    """Cash Flow Statement"""
    company_id = current_user.company_id
    today = datetime.now()
    starting_cash = CompanyDB.get_starting_cash(company_id)
    
    start_date = request.args.get('start_date', f"{today.year}-01-01")
    end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
    
    cf = FinancialStatements.get_cash_flow(company_id, start_date, end_date, starting_cash)
    return render_template('cash_flow.html', cash_flow=cf, current_year=today.year)


@app.route('/tax-summary')
@login_required
@demo_readonly
@subscription_required(['pro'])
def tax_summary():
    """Tax Summary"""
    company_id = current_user.company_id
    current_year = datetime.now().year
    tax_year = int(request.args.get('year', current_year))
    starting_cash = CompanyDB.get_starting_cash(company_id)

    profile = CompanyDB.get_tax_profile(company_id)
    tax = FinancialStatements.get_tax_summary(
        company_id, tax_year, starting_cash,
        entity_type=profile.get('entity_type'), state=profile.get('state'))
    years = list(range(current_year, current_year - 5, -1))

    return render_template(
        'tax_summary.html', tax=tax, years=years,
        est=tax['estimate'],
        entity_types=tax_estimator.ENTITY_TYPES,
        state_names=sorted(tax_estimator.STATE_NAMES.items()))


@app.route('/tax-summary/profile', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def tax_summary_profile():
    """Save the company's entity type + state, which drive the tax estimate."""
    entity_type = (request.form.get('entity_type') or '').strip().lower()
    state = (request.form.get('state') or '').strip().upper()
    if entity_type and entity_type not in tax_estimator.ENTITY_TYPES:
        entity_type = ''
    if state and state not in tax_estimator.STATE_NAMES:
        state = ''
    if CompanyDB.set_tax_profile(current_user.company_id, entity_type, state):
        flash('Tax profile updated.', 'success')
    else:
        flash('Could not save your tax profile. Please try again.', 'error')
    year = request.form.get('year') or datetime.now().year
    return redirect(url_for('tax_summary', year=year))


@app.route('/tax-documents/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_tax_document():
    company_id = current_user.company_id
    if request.method == 'POST':
        file_data = file_name = file_type = None
        uploaded = request.files.get('document_file')
        if uploaded and uploaded.filename:
            file_data = uploaded.read()
            file_name = uploaded.filename
            file_type = uploaded.mimetype
        doc = {
            'document_type': request.form['document_type'],
            'tax_year': int(request.form['tax_year']),
            'vendor_name': request.form.get('vendor_name', ''),
            'amount': float(request.form.get('amount') or 0),
            'category': request.form.get('category', ''),
            'notes': request.form.get('notes', '')
        }
        TaxDocumentDB.create(doc, company_id, file_data, file_name, file_type)
        flash('Tax document saved!', 'success')
        return redirect(url_for('tax_summary', year=doc['tax_year']))
    current_year = datetime.now().year
    years = list(range(current_year, current_year - 5, -1))
    return render_template('add_tax_document.html', years=years)


@app.route('/tax-documents/download/<doc_id>')
@login_required
@demo_readonly
@subscription_required(['pro'])
def download_tax_document(doc_id):
    doc = TaxDocumentDB.get_by_id(doc_id, current_user.company_id)
    if not doc or not doc.get('file_data'):
        flash('No file attached to this document.', 'error')
        return redirect(url_for('tax_summary'))
    return send_file(
        io.BytesIO(bytes(doc['file_data'])),
        mimetype=doc['file_type'],
        as_attachment=True,
        download_name=doc['file_name']
    )


@app.route('/tax-documents/delete/<doc_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_tax_document(doc_id):
    TaxDocumentDB.delete(doc_id, current_user.company_id)
    flash('Tax document deleted.', 'success')
    return redirect(url_for('tax_summary'))


@app.route('/invoices')
@login_required
@demo_readonly
@subscription_required(['pro'])
def invoices():
    """List all invoices"""
    company_id = current_user.company_id
    all_invoices = InvoiceDB.get_all(company_id)
    summary = InvoiceDB.get_summary(company_id)
    return render_template('invoices.html', invoices=all_invoices, summary=summary)


@app.route('/invoices/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_invoice():
    company_id = current_user.company_id
    if request.method == 'POST':
        invoice = {
            'invoice_number': request.form['invoice_number'],
            'customer_name': request.form['customer_name'],
            'customer_email': request.form.get('customer_email', '').strip(),
            'invoice_date': request.form['invoice_date'],
            'due_date': request.form['due_date'],
            'total_amount': float(request.form['total_amount']),
            'notes': request.form.get('notes', '')
        }
        InvoiceDB.create(invoice, company_id)

        if invoice['customer_email']:
            company = CompanyDB.get(company_id)
            send_in_background(
                send_invoice_email,
                invoice['customer_email'],
                invoice['customer_name'],
                invoice['invoice_number'],
                invoice['total_amount'],
                invoice['invoice_date'],
                invoice['due_date'],
                invoice.get('notes', ''),
                company['name'],
                current_user.email
            )
            flash(f"Invoice created and emailed to {invoice['customer_email']}!", 'success')
        else:
            flash('Invoice created!', 'success')

        return redirect(url_for('invoices'))

    today = datetime.now().strftime('%Y-%m-%d')
    invoice_number = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    return render_template('add_invoice.html', today=today, invoice_number=invoice_number)

@app.route('/invoices/mark-paid/<invoice_id>')
@login_required
@demo_readonly
@subscription_required(['pro'])
def mark_invoice_paid(invoice_id):
    """Mark invoice as paid"""
    InvoiceDB.mark_paid(invoice_id, current_user.company_id)
    flash('Invoice marked as paid!', 'success')
    return redirect(url_for('invoices'))

@app.route('/invoices/delete/<invoice_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_invoice(invoice_id):
    InvoiceDB.delete(invoice_id, current_user.company_id)
    flash('Invoice deleted.', 'success')
    return redirect(url_for('invoices'))


@app.route('/bills')
@login_required
@demo_readonly
@subscription_required(['pro'])
def bills():
    """List all bills"""
    company_id = current_user.company_id
    all_bills = BillDB.get_all(company_id)
    summary = BillDB.get_summary(company_id)
    return render_template('bills.html', bills=all_bills, summary=summary)


@app.route('/bills/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_bill():
    """Add new bill"""
    company_id = current_user.company_id
    
    if request.method == 'POST':
        bill = {
            'bill_number': request.form.get('bill_number', ''),
            'vendor_name': request.form['vendor_name'],
            'bill_date': request.form['bill_date'],
            'due_date': request.form['due_date'],
            'total_amount': float(request.form['total_amount']),
            'category': request.form.get('category', ''),
            'notes': request.form.get('notes', '')
        }
        BillDB.create(bill, company_id)
        flash('Bill added!', 'success')
        return redirect(url_for('bills'))
    
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('add_bill.html', today=today)

@app.route('/bills/delete/<bill_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_bill(bill_id):
    BillDB.delete(bill_id, current_user.company_id)
    flash('Bill deleted.', 'success')
    return redirect(url_for('bills'))

@app.route('/bills/mark-paid/<bill_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def mark_bill_paid(bill_id):
    BillDB.mark_paid(bill_id, current_user.company_id)
    flash('Bill marked as paid!', 'success')
    return redirect(url_for('bills'))

@app.route('/chart-of-accounts')
@login_required
@demo_readonly
@subscription_required(['pro'])
def chart_of_accounts():
    """View chart of accounts"""
    company_id = current_user.company_id
    ChartOfAccountsDB.create_standard_accounts(company_id)
    accounts = ChartOfAccountsDB.get_all(company_id)
    
    organized = {}
    for acc in accounts:
        acc_type = acc['account_type']
        if acc_type not in organized:
            organized[acc_type] = []
        organized[acc_type].append(acc)
    
    return render_template('chart_of_accounts.html', accounts=organized)

# ── Admin decorator ──────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ── Admin: user list ─────────────────────────────────────
@app.route('/admin')
@login_required
@demo_readonly
def admin_panel():
    if not current_user.is_admin:
        return render_template('404.html')
    users = UserDB.get_all()
    return render_template('admin.html', users=users)


@app.route('/admin/add-user', methods=['GET', 'POST'])
@login_required
@demo_readonly
def admin_add_user():
    if not current_user.is_admin:
        return render_template('404.html')
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        company_name = request.form.get('company_name', '').strip()
        starting_cash = float(request.form.get('starting_cash', 0) or 0)
        existing_user = UserDB.get_by_email(email)
        if existing_user:
            error = 'An account with this email already exists.'
            return render_template('admin_add_user.html', error=error)

        if len(password) < 6:
            error = 'Password must be at least 6 characters.'
            return render_template('admin_add_user.html', error=error)
        user = User.create_user(email, password, full_name, company_name, starting_cash)
        flash('User created! 🎉', 'success')
        return render_template('admin.html', success=True)
    return render_template('admin_add_user.html')

@app.route('/admin/ban/<user_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_ban_user(user_id):
    if not current_user.is_admin:
        return render_template('404.html')
    UserDB.ban(user_id)
    flash('User banned.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/unban/<user_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_unban_user(user_id):
    if not current_user.is_admin:
        return render_template('404.html')
    UserDB.unban(user_id)
    flash('User unbanned.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/delete-user/<user_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_delete_user(user_id):
    if not current_user.is_admin:
        return render_template('404.html')
    if user_id == str(current_user.id):
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_panel'))
    UserDB.delete(user_id)
    Database.execute("DELETE FROM transactions WHERE company_id = (SELECT company_id FROM users WHERE id = %s)", (user_id,), fetch=False)   
    #Database.execute("""DELETE FROM companies 
#WHERE id NOT IN (SELECT company_id FROM users WHERE company_id IS NOT NULL);""")
    flash('User deleted.', 'success')
    return redirect(url_for('admin_panel'))



@app.route('/admin/impersonate/<user_id>')
@login_required
@demo_readonly

def admin_impersonate(user_id):
    if not current_user.is_admin:
        return render_template('404.html')
    target = User.get(user_id)
    if not target:
        flash('User not found.', 'error')
        return redirect(url_for('admin_panel'))
    session['impersonating'] = str(current_user.id)  # remember real admin
    login_user(target)
    flash(f'Now impersonating {target.email}. Click "Stop Impersonating" to return.', 'warning')
    return redirect(url_for('dashboard'))

@app.route('/admin/stop-impersonating')
@login_required
@demo_readonly

def stop_impersonating():
    admin_id = session.pop('impersonating', None)
    if admin_id:
        admin = User.get(admin_id)
        if admin:
            login_user(admin)
            flash('Returned to your admin account.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/stats')
@login_required
@demo_readonly

def admin_stats():
    if not current_user.is_admin:
        return render_template('404.html')
    stats = UserDB.get_stats()
    signups = UserDB.get_signups_by_day()
    return render_template('admin_stats.html', stats=stats, signups=signups)

@app.route('/admin/make-admin/<user_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_make_admin(user_id):
    if not current_user.is_admin:
        return render_template('404.html')
    if user_id == str(current_user.id):
        flash('You are already an admin.', 'error')
        return redirect(url_for('admin_panel'))
    UserDB.set_admin(user_id, True)
    flash('User promoted to admin.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/remove-admin/<user_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_remove_admin(user_id):
    if not current_user.is_admin:
        return render_template('404.html')
    if user_id == str(current_user.id):
        flash('You cannot remove your own admin access.', 'error')
        return redirect(url_for('admin_panel'))
    UserDB.set_admin(user_id, False)
    flash('Admin access removed.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/board-report', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])

def board_report():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    raw_metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)
    company = CompanyDB.get(company_id)

    # Read the canonical figures rather than recomputing. A board pack that
    # disagrees with the dashboard is worse than one that is merely late.
    canon = canonical_metrics.compute_metrics(transactions, starting_cash)
    kpis = FinancialCalculator.calculate_kpis(transactions, starting_cash, canon=canon)

    # Format for template
    metrics = {
        'cash': canon['cash_balance'],
        'burn_rate': canon['gross_burn_monthly'],
        'net_burn': canon['net_burn_monthly'],
        'runway': canon['runway_months'],        # None when profitable
        'runway_display': canon['runway_display'],
        'is_profitable': canon['is_profitable'],
        'mrr': canon['mrr'],
        'monthly_breakdown': raw_metrics['monthly_breakdown']
    }

    if request.method == 'POST':
        from groq import Groq

        period = request.form.get('period', 'Q1 2026')
        highlights = request.form.get('highlights', '')
        challenges = request.form.get('challenges', '')
        next_quarter = request.form.get('next_quarter', '')
        ask = request.form.get('ask', '')

        try:
            groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))
            prompt = f"""Write a professional board report financial summary for {company['name']}.

Period: {period}
Cash: ${raw_metrics['current_cash']:,.0f}
Monthly Burn: ${raw_metrics['monthly_burn']:,.0f}
Runway: {raw_metrics['runway_months'] or 'N/A'} months
MRR: ${raw_metrics['mrr']:,.0f}
ARR: ${kpis['arr']:,.0f}
MoM Growth: {kpis['mom_growth']}%
Gross Margin: {kpis['gross_margin']}%
Burn Multiple: {kpis['burn_multiple']}x

Highlights: {highlights}
Challenges: {challenges}
Next Quarter Plan: {next_quarter}
The Ask: {ask}

Write 2 concise paragraphs suitable for a board of directors. Be direct and data-driven."""

            response = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                max_tokens=400
            )
            narrative = response.choices[0].message.content
        except Exception as e:
            narrative = f"{company['name']} reports ${raw_metrics['current_cash']:,.0f} in cash with {raw_metrics['runway_months'] or 'N/A'} months runway and ${raw_metrics['mrr']:,.0f} MRR for {period}."

        from Modules.export_utils import generate_board_report
        pdf = generate_board_report(
            company['name'], raw_metrics, kpis,
            raw_metrics['monthly_breakdown'], narrative,
            {'period': period, 'highlights': highlights,
             'challenges': challenges, 'next_quarter': next_quarter, 'ask': ask}
        )

        return send_file(
            io.BytesIO(pdf),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{company['name'].replace(' ', '_')}_Board_Report_{period.replace(' ', '_')}.pdf"
        )

    return render_template('board_report.html', metrics=metrics, kpis=kpis, company=company)

@app.route('/custom-report', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])

def custom_report():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)

    report_data = None

    if request.method == 'POST':
        date_from = request.form.get('date_from')
        date_to = request.form.get('date_to')
        tx_types = request.form.getlist('tx_type')
        categories = request.form.getlist('categories')
        group_by = request.form.get('group_by', 'month')
        show_charts = request.form.get('show_charts') == 'on'

        # Filter transactions
        filtered = []
        for t in transactions:
            date_raw = t['date']
            if isinstance(date_raw, str):
                from datetime import datetime as dt
                date_obj = dt.strptime(date_raw, '%Y-%m-%d').date()
            else:
                date_obj = date_raw

            if date_from and str(date_obj) < date_from:
                continue
            if date_to and str(date_obj) > date_to:
                continue
            if tx_types and t['type'] not in tx_types:
                continue
            if categories and t.get('category') not in categories:
                continue
            filtered.append(t)

        # Group
        groups = {}
        for t in filtered:
            date_raw = t['date']
            if isinstance(date_raw, str):
                from datetime import datetime as dt
                date_obj = dt.strptime(date_raw, '%Y-%m-%d')
            else:
                from datetime import datetime as dt
                date_obj = dt.combine(date_raw, dt.min.time())

            if group_by == 'month':
                key = date_obj.strftime('%Y-%m')
            elif group_by == 'category':
                key = t.get('category', 'Uncategorized')
            elif group_by == 'type':
                key = t['type'].title()
            else:
                key = date_obj.strftime('%Y')

            if key not in groups:
                groups[key] = {'income': 0.0, 'expense': 0.0, 'count': 0}
            groups[key][t['type']] += float(t['amount'])
            groups[key]['count'] += 1

        report_data = {
            'filtered': filtered,
            'groups': dict(sorted(groups.items())),
            'total_income': sum(t['amount'] for t in filtered if t['type'] == 'income'),
            'total_expense': sum(t['amount'] for t in filtered if t['type'] == 'expense'),
            'count': len(filtered),
            'group_by': group_by,
            'show_charts': show_charts,
            'date_from': date_from,
            'date_to': date_to
        }

    # Get unique categories for filter
    all_categories = sorted(set(t.get('category', '') for t in transactions if t.get('category')))

    return render_template('custom_report.html',
                           report=report_data,
                           categories=all_categories,
                           metrics=metrics)

@app.route('/cohort-analysis')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def cohort_analysis():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)

    # Build monthly cohorts from income transactions
    from datetime import datetime as dt
    cohorts = {}

    for t in transactions:
        if t['type'] != 'income':
            continue
        date_raw = t['date']
        if isinstance(date_raw, str):
            date_obj = dt.strptime(date_raw, '%Y-%m-%d')
        else:
            date_obj = dt.combine(date_raw, dt.min.time())

        month = date_obj.strftime('%Y-%m')
        if month not in cohorts:
            cohorts[month] = {'revenue': 0.0, 'count': 0}
        cohorts[month]['revenue'] += float(t['amount'])
        cohorts[month]['count'] += 1

    sorted_cohorts = sorted(cohorts.items())

    # Calculate retention (revenue vs first month)
    first_revenue = sorted_cohorts[0][1]['revenue'] if sorted_cohorts else 1
    cohort_list = []
    for month, data in sorted_cohorts:
        retention = round((data['revenue'] / first_revenue) * 100, 1) if first_revenue > 0 else 0
        cohort_list.append({
            'month': month,
            'revenue': data['revenue'],
            'count': data['count'],
            'retention': retention
        })

    return render_template('cohort_analysis.html', cohorts=cohort_list)

# ============================================
# BUDGET ROUTES
# ============================================

@app.route('/budgets')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def budgets():
    data = BudgetDB.get_with_actuals(current_user.company_id)
    return render_template('budgets.html', budgets=data)

@app.route('/budgets/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def add_budget():
    if request.method == 'POST':
        budget = {
            'name': request.form['name'],
            'category': request.form.get('category', ''),
            'budget_type': request.form.get('budget_type', 'category'),
            'amount': float(request.form['amount']),
            'start_date': request.form['start_date'],
            'end_date': request.form['end_date']
        }
        BudgetDB.create(budget, current_user.company_id)
        flash('Budget created!', 'success')
        return redirect(url_for('budgets'))
    today = datetime.now()
    start = today.strftime('%Y-%m-01')
    end = today.strftime('%Y-%m-%d')
    return render_template('add_budget.html', start=start, end=end)

@app.route('/budgets/delete/<budget_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def delete_budget(budget_id):
    BudgetDB.delete(budget_id, current_user.company_id)
    flash('Budget deleted.', 'success')
    return redirect(url_for('budgets'))

@app.route('/budgets/departments')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def department_budgets():
    company_id = current_user.company_id
    budgets = BudgetDB.get_with_actuals(company_id)
    transactions = TransactionDB.get_all(company_id)

    # Group by department
    departments = {}
    for b in budgets:
        dept = b.get('department') or 'General'
        if dept not in departments:
            departments[dept] = {'budgets': [], 'total_budget': 0, 'total_spent': 0}
        departments[dept]['budgets'].append(b)
        departments[dept]['total_budget'] += float(b.get('amount', 0))
        departments[dept]['total_spent'] += float(b.get('actual_spent', 0) or 0)

    return render_template('department_budgets.html', departments=departments)

@app.route('/budget-alerts')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def budget_alerts():
    company_id = current_user.company_id
    budgets = BudgetDB.get_with_actuals(company_id)

    alerts = []
    for b in budgets:
        budgeted = float(b.get('amount', 0))
        actual = float(b.get('actual_spent', 0))
        pct = (actual / budgeted * 100) if budgeted > 0 else 0

        if actual > budgeted:
            alerts.append({
                'budget': b,
                'level': 'error',
                'message': f"Over budget by ${actual - budgeted:,.0f} ({pct:.0f}% used)",
                'percent': min(pct, 150)
            })
        elif pct >= 90:
            alerts.append({
                'budget': b,
                'level': 'warning',
                'message': f"Nearly exhausted: {pct:.0f}% used, ${budgeted - actual:,.0f} remaining",
                'percent': pct
            })
        elif pct >= 75:
            alerts.append({
                'budget': b,
                'level': 'info',
                'message': f"Watch closely: {pct:.0f}% used, ${budgeted - actual:,.0f} remaining",
                'percent': pct
            })

    alerts.sort(key=lambda x: {'error': 0, 'warning': 1, 'info': 2}[x['level']])

    return render_template('budget_alerts.html', alerts=alerts, budgets=budgets)

@app.route('/projections', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])

def projections():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    raw_metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)

    current_mrr = raw_metrics['mrr']
    current_burn = raw_metrics['monthly_burn']
    current_cash = raw_metrics['current_cash']

    assumptions = {
        'starting_cash': current_cash,
        'starting_mrr': current_mrr,
        'monthly_revenue_growth': 10.0,
        'monthly_expense_growth': 3.0,
        'starting_expenses': current_burn,
        'one_time_investments': 0.0,
        'fundraise_month': 0,
        'fundraise_amount': 0.0
    }

    if request.method == 'POST':
        assumptions = {
            'starting_cash': float(request.form.get('starting_cash', current_cash)),
            'starting_mrr': float(request.form.get('starting_mrr', current_mrr)),
            'monthly_revenue_growth': float(request.form.get('monthly_revenue_growth', 10)),
            'monthly_expense_growth': float(request.form.get('monthly_expense_growth', 3)),
            'starting_expenses': float(request.form.get('starting_expenses', current_burn)),
            'one_time_investments': float(request.form.get('one_time_investments', 0)),
            'fundraise_month': int(request.form.get('fundraise_month', 0)),
            'fundraise_amount': float(request.form.get('fundraise_amount', 0))
        }

    # Build 12-month projection
    from datetime import datetime as dt
    today = dt.now()
    months = []
    cash = assumptions['starting_cash']
    revenue = assumptions['starting_mrr']
    expenses = assumptions['starting_expenses']

    for i in range(1, 13):
        month_num = today.month + i
        year = today.year + (month_num - 1) // 12
        month = ((month_num - 1) % 12) + 1
        label = f"{year}-{month:02d}"

        revenue *= (1 + assumptions['monthly_revenue_growth'] / 100)
        expenses *= (1 + assumptions['monthly_expense_growth'] / 100)

        cash_change = revenue - expenses
        if i == 1:
            cash -= assumptions['one_time_investments']
        if i == assumptions['fundraise_month']:
            cash += assumptions['fundraise_amount']

        cash += cash_change
        months.append({
            'month': label,
            'revenue': round(revenue, 2),
            'expenses': round(expenses, 2),
            'net': round(cash_change, 2),
            'cash': round(cash, 2),
            'fundraise': assumptions['fundraise_amount'] if i == assumptions['fundraise_month'] else 0
        })

    runway = next((i + 1 for i, m in enumerate(months) if m['cash'] <= 0), None)

    return render_template('projections.html',
                           months=months,
                           assumptions=assumptions,
                           runway=runway,
                           current_cash=current_cash,
                           current_mrr=current_mrr,
                           current_burn=current_burn)


@app.route('/headcount', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])

def headcount():
    company_id = current_user.company_id
    from Modules.database import HeadcountDB
    if request.method == 'POST':
        data = {
            'role': request.form['role'],
            'department': request.form.get('department', ''),
            'start_month': request.form['start_month'],
            'salary': float(request.form['salary']),
            'employment_type': request.form.get('employment_type', 'full_time'),
            'notes': request.form.get('notes', '')
        }
        HeadcountDB.create(data, company_id)
        flash('Role added to headcount plan!', 'success')
        return redirect(url_for('headcount'))

    hires = HeadcountDB.get_all(company_id)
    existing = EmployeeDB.get_all(company_id)

    # Calculate monthly payroll impact
    from datetime import datetime as dt
    today = dt.now()
    monthly_impact = {}
    for i in range(1, 13):
        month_num = today.month + i
        year = today.year + (month_num - 1) // 12
        month = ((month_num - 1) % 12) + 1
        label = f"{year}-{month:02d}"
        monthly_impact[label] = sum(
            float(h['salary']) for h in hires
            if h['start_month'] <= label
        )

    total_new_cost = sum(float(h['salary']) for h in hires)
    current_payroll = sum(float(e['salary']) for e in existing)

    return render_template('headcount.html',
                           hires=hires,
                           monthly_impact=monthly_impact,
                           total_new_cost=total_new_cost,
                           current_payroll=current_payroll)

@app.route('/headcount/delete/<plan_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])

def delete_headcount(plan_id):
    HeadcountDB.delete(plan_id, current_user.company_id)
    flash('Role removed.', 'success')
    return redirect(url_for('headcount'))

@app.route('/headcount/status/<plan_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def update_headcount_status(plan_id):
    status = request.form.get('status', 'planned')
    HeadcountDB.update_status(plan_id, status, current_user.company_id)
    return redirect(url_for('headcount'))

@app.route('/runway-planner', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])

def runway_planner():
    company_id = current_user.company_id
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    raw_metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)

    # Canonical baseline. This page previously divided cash by GROSS burn, so it
    # reported a shorter runway than the dashboard for any company with revenue.
    canon = canonical_metrics.compute_metrics(transactions, starting_cash)
    current_cash = canon['cash_balance']
    monthly_burn = canon['gross_burn_monthly']
    current_runway = canon['runway_months']

    scenarios = []
    raise_amounts = [500000, 1000000, 2000000, 5000000]
    for amount in raise_amounts:
        modelled = canonical_metrics.compute_scenario(canon, cash_delta=amount)
        scenarios.append({
            'amount': amount,
            'new_cash': modelled['cash_balance'],
            'new_runway': modelled['runway_months'],          # None = profitable
            'new_runway_display': modelled['runway_display'],
            'extra_months': modelled['runway_delta_months'],
        })

    # Custom scenario
    custom = None
    if request.method == 'POST':
        raise_amount = float(request.form.get('raise_amount', 0))
        new_burn = float(request.form.get('new_burn', monthly_burn))
        new_headcount_cost = float(request.form.get('new_headcount_cost', 0))
        total_burn = new_burn + new_headcount_cost

        # The form supplies a replacement GROSS burn, so express it to the
        # engine as a delta against today's gross figure. Runway then nets
        # revenue exactly as every other surface does.
        modelled = canonical_metrics.compute_scenario(
            canon,
            monthly_expense_delta=total_burn - canon['gross_burn_monthly'],
            cash_delta=raise_amount)
        new_cash = modelled['cash_balance']
        new_runway = modelled['runway_months']          # None = profitable
        net_drain = modelled['net_burn_monthly']

        # Month by month. A profitable scenario never hits zero, so cap the
        # projection at 36 months instead of iterating against a None runway.
        timeline = []
        cash = new_cash
        horizon = 36 if new_runway is None else int(min(new_runway + 3, 37))
        for i in range(1, max(horizon, 2)):
            from datetime import datetime as dt, timedelta
            today = dt.now()
            month_num = today.month + i
            year = today.year + (month_num - 1) // 12
            month = ((month_num - 1) % 12) + 1
            label = f"{year}-{month:02d}"
            cash -= net_drain
            timeline.append({'month': label, 'cash': round(cash, 2)})
            if cash <= 0:
                break

        custom = {
            'raise_amount': raise_amount,
            'new_burn': total_burn,
            'new_net_burn': net_drain,
            'new_runway': new_runway,
            'new_runway_display': modelled['runway_display'],
            'extra_months': modelled['runway_delta_months'],
            'timeline': timeline
        }

    return render_template('runway_planner.html',
                           current_cash=current_cash,
                           monthly_burn=monthly_burn,
                           current_runway=current_runway,
                           scenarios=scenarios,
                           custom=custom)

# ============================================
# RECURRING TRANSACTION SCHEDULER
# ============================================

def process_recurring_transactions():
    from datetime import date
    print("⏰ Running recurring transaction job...")
    today = date.today()

    try:
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM companies")
                companies = cursor.fetchall()

        for company in companies:
            company_id = company['id']
            recurring_txs = TransactionDB.get_recurring(company_id)

            for tx in recurring_txs:
                frequency = tx.get('recurring_frequency') or 'monthly'

                # Parse the original transaction date
                original_date = tx['date']
                if isinstance(original_date, str):
                    from datetime import datetime
                    original_date = datetime.strptime(original_date, '%Y-%m-%d').date()

                should_run = False

                if frequency == 'weekly':
                    # Run if today is the same weekday as original date
                    should_run = today.weekday() == original_date.weekday()

                elif frequency == 'monthly':
                    # Run if today is the same day of month as original date
                    should_run = today.day == original_date.day

                elif frequency == 'yearly':
                    # Run if today is same month and day as original date
                    should_run = (today.month == original_date.month and
                                  today.day == original_date.day)

                if not should_run:
                    continue

                # Don't duplicate — check if already created today
                already_exists = TransactionDB.exists_today(
                    company_id,
                    tx['category'],
                    tx['description'] or '',
                    today,
                    float(tx['amount'])
                )
                if already_exists:
                    continue

                new_tx = {
                    'date': today.strftime('%Y-%m-%d'),
                    'amount': float(tx['amount']),
                    'type': tx['type'],
                    'category': tx['category'],
                    'description': tx['description'] or '',
                    'recurring': True,
                    'recurring_frequency': frequency
                }
                TransactionDB.create(new_tx, company_id)
                print(f"  ✓ Created {frequency} recurring tx: {tx['category']}")

        print("✅ Done.")
    except Exception as e:
        print(f"❌ Error: {e}")


def sweep_demo_sandboxes():
    """Delete demo sandboxes abandoned without an explicit logout (e.g. the visitor
    just closed the tab). Runs hourly; sandboxes older than 6h are removed. Idempotent,
    so it's safe even if several workers each run their own scheduler."""
    try:
        stale = UserDB.get_stale_demo_sandboxes(hours=6)
        for s in stale:
            UserDB.delete_demo_sandbox(s.get('id'), s.get('company_id'))
        if stale:
            print(f"🧹 swept {len(stale)} abandoned demo sandbox(es)")
    except Exception as e:
        print(f"❌ demo sandbox sweep failed: {e}")

# Start the scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=process_recurring_transactions,
    trigger='cron',
    hour=0,
    minute=0
)
scheduler.add_job(
    func=sweep_demo_sandboxes,
    trigger='interval',
    hours=1
)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

@app.route('/run-recurring')
@login_required
@demo_readonly
def run_recurring():
    if not current_user.is_admin:
        return render_template('404.html')
    process_recurring_transactions()
    flash('Recurring transactions processed!', 'success')
    return redirect(url_for('transactions'))

# ============================================
# GOALS ROUTES
# ============================================

@app.route('/goals')
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def goals():
    company_id = current_user.company_id
    all_goals = GoalDB.get_all(company_id)
    transactions = TransactionDB.get_all(company_id)
    starting_cash = CompanyDB.get_starting_cash(company_id)
    metrics = FinancialCalculator.get_all_metrics(transactions, starting_cash)

    # Calculate current value and progress for each goal
    for goal in all_goals:
        goal_type = goal['goal_type']
        target = float(goal['target_value'])

        if goal_type == 'cash':
            current = metrics['current_cash']
        elif goal_type == 'revenue':
            current = metrics['mrr']
        elif goal_type == 'expenses':
            # For expenses goal, progress is how much UNDER the limit you are
            current = metrics['monthly_burn']
            goal['inverted'] = True  # lower is better
        elif goal_type == 'runway':
            current = metrics['runway_months'] or 0
        else:
            current = 0

        goal['current_value'] = round(current, 2)
        goal['target_value'] = target

        if goal.get('inverted'):
            # For expenses: 100% means spending exactly at target, over = bad
            goal['percent'] = round(min((target / current * 100) if current > 0 else 100, 100), 1)
        else:
            goal['percent'] = round(min((current / target * 100) if target > 0 else 0, 100), 1)

        goal['achieved'] = goal['percent'] >= 100

        # Build monthly progress data for chart (cash/revenue only)
        goal['monthly_labels'] = []
        goal['monthly_values'] = []
        if goal_type in ('cash', 'revenue'):
            for m in metrics['monthly_breakdown']:
                goal['monthly_labels'].append(m['month'])
                if goal_type == 'cash':
                    goal['monthly_values'].append(m['cash_balance'])
                else:
                    goal['monthly_values'].append(m['revenue'])

    return render_template('goals.html', goals=all_goals, metrics=metrics)


@app.route('/goals/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def add_goal():
    if request.method == 'POST':
        goal = {
            'name': request.form['name'],
            'goal_type': request.form['goal_type'],
            'target_value': float(request.form['target_value']),
            'deadline': request.form.get('deadline') or None
        }
        GoalDB.create(goal, current_user.company_id)
        flash('Goal created!', 'success')
        return redirect(url_for('goals'))
    return render_template('add_goal.html')


@app.route('/goals/delete/<goal_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['starter', 'pro'])
def delete_goal(goal_id):
    GoalDB.delete(goal_id, current_user.company_id)
    flash('Goal deleted.', 'success')
    return redirect(url_for('goals'))


# ============================================
# VENDOR ROUTES
# ============================================

@app.route('/vendors')
@login_required
@demo_readonly
@subscription_required(['pro'])
def vendors():
    data = VendorDB.get_all(current_user.company_id)
    return render_template('vendors.html', vendors=data)


@app.route('/vendors/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_vendor():
    if request.method == 'POST':
        vendor = {
            'name': request.form['name'],
            'category': request.form.get('category', ''),
            'contact_name': request.form.get('contact_name', ''),
            'contact_email': request.form.get('contact_email', ''),
            'contact_phone': request.form.get('contact_phone', ''),
            'website': request.form.get('website', ''),
            'payment_terms': request.form.get('payment_terms', 'Net 30'),
            'notes': request.form.get('notes', '')
        }
        VendorDB.create(vendor, current_user.company_id)
        flash('Vendor added!', 'success')
        return redirect(url_for('vendors'))
    return render_template('add_vendor.html')


@app.route('/vendors/edit/<vendor_id>', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def edit_vendor(vendor_id):
    vendor = VendorDB.get_by_id(vendor_id, current_user.company_id)
    if not vendor:
        flash('Vendor not found.', 'error')
        return redirect(url_for('vendors'))
    if request.method == 'POST':
        updated = {
            'name': request.form['name'],
            'category': request.form.get('category', ''),
            'contact_name': request.form.get('contact_name', ''),
            'contact_email': request.form.get('contact_email', ''),
            'contact_phone': request.form.get('contact_phone', ''),
            'website': request.form.get('website', ''),
            'payment_terms': request.form.get('payment_terms', 'Net 30'),
            'notes': request.form.get('notes', '')
        }
        VendorDB.update(vendor_id, updated, current_user.company_id)
        flash('Vendor updated!', 'success')
        return redirect(url_for('vendors'))
    return render_template('add_vendor.html', vendor=vendor, editing=True)


@app.route('/vendors/delete/<vendor_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_vendor(vendor_id):
    VendorDB.delete(vendor_id, current_user.company_id)
    flash('Vendor removed.', 'success')
    return redirect(url_for('vendors'))

# ============================================
# PAYROLL ROUTES
# ============================================

@app.route('/contractor-w9', methods=['GET', 'POST'])
@login_required
@demo_readonly
def contractor_w9():
    """
    W-9 Contractor Form
    GET  — render the blank form
    POST — validate, save to DB, flash confirmation
    """
    today = datetime.now().strftime('%Y-%m-%d')
 
    if request.method == 'POST':
        # ── Collect fields ──────────────────────────────────
        full_name           = request.form.get('full_name', '').strip()
        business_name       = request.form.get('business_name', '').strip()
        tax_classification  = request.form.get('tax_classification', '').strip()
        llc_type            = request.form.get('llc_type', '').strip()
        exempt_payee_code   = request.form.get('exempt_payee_code', '').strip()
        fatca_code          = request.form.get('fatca_code', '').strip()
        address             = request.form.get('address', '').strip()
        city                = request.form.get('city', '').strip()
        state               = request.form.get('state', '').strip()
        zip_code            = request.form.get('zip_code', '').strip()
        account_numbers     = request.form.get('account_numbers', '').strip()
        ssn_1 = request.form.get('ssn_1', '').strip()
        ssn_2 = request.form.get('ssn_2', '').strip()
        ssn_3 = request.form.get('ssn_3', '').strip()
        ein_1 = request.form.get('ein_1', '').strip()
        ein_2 = request.form.get('ein_2', '').strip()
        signature           = request.form.get('signature', '').strip()
        signed_date         = request.form.get('signed_date', today)
        certified           = request.form.get('certified') == 'yes'
 
        # ── Validation ──────────────────────────────────────
        errors = []
        if not full_name:
            errors.append('Legal name (Line 1) is required.')
        if not tax_classification:
            errors.append('Please select a federal tax classification.')
        if tax_classification in ('llc', 'other') and not llc_type:
            errors.append('Please describe the LLC type or entity.')
        if not address:
            errors.append('Street address is required.')
        if not city:
            errors.append('City is required.')
        if not state:
            errors.append('State is required.')
        if not zip_code:
            errors.append('ZIP code is required.')
 
        # Must have SSN or EIN (not both, not neither)
        has_ssn = ssn_1 and ssn_2 and ssn_3
        has_ein = ein_1 and ein_2
        if not has_ssn and not has_ein:
            errors.append('Please provide either a Social Security Number or an Employer Identification Number.')
 
        if not signature:
            errors.append('Signature is required.')
        if not certified:
            errors.append('You must check the certification box before submitting.')
 
        if errors:
            for e in errors:
                flash(e, 'error')
            # Re-render with previously entered values
            form_data = request.form
            return render_template('contractor_w9.html', form=form_data, today=today)
 
        # ── Save to database ────────────────────────────────
        try:
            Database.execute("""
                INSERT INTO contractor_w9 (
                    submitted_by,
                    full_name, business_name, tax_classification, llc_type,
                    exempt_payee_code, fatca_code,
                    address, city, state, zip_code, account_numbers,
                    ssn_1, ssn_2, ssn_3, ein_1, ein_2,
                    signature, signed_date, certified
                ) VALUES (
                    %s,
                    %s, %s, %s, %s,
                    %s, %s, 
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
            """, (
                str(current_user.id),
                full_name, business_name, tax_classification, llc_type,
                exempt_payee_code, fatca_code,
                address, city, state, zip_code, account_numbers,
                ssn_1, ssn_2, ssn_3, ein_1, ein_2,
                signature, signed_date, certified
            ), fetch=False)
 
            flash(
                f'W-9 submitted successfully for {full_name}. '
                'Your information has been securely saved for tax reporting.',
                'success'
            )
            return redirect(url_for('home'))
 
        except Exception as e:
            print(f'[W-9 ERROR] {e}')
            flash('Something went wrong saving your W-9. Please try again.', 'error')
 
    return render_template('contractor_w9.html', form=None, today=today)

@app.route('/admin/w9s')
@login_required
@demo_readonly
def admin_w9s():
    """Admin-only list of all submitted W-9 forms (no TINs shown)."""
    if not current_user.is_admin:
        return render_template('404.html')
 
    rows = Database.execute("""
        SELECT * FROM contractor_w9 
    """)
 
    w9s = [dict(r) for r in rows] if rows else []
    # Never expose tax identifiers (SSN/EIN) in the admin list view.
    for _w in w9s:
        for _f in ('ssn_1', 'ssn_2', 'ssn_3', 'ein_1', 'ein_2'):
            _w.pop(_f, None)
    return render_template('admin_w9s.html', w9s=w9s)

@app.route('/admin/w9s/delete/<w9_full_name>', methods=['POST'])
@login_required
@demo_readonly
def delete_w9(w9_full_name):
    """Admin-only deletion of W-9 forms by full name."""
    if not current_user.is_admin:
        return render_template('404.html')
 
    Database.execute("""
        DELETE FROM contractor_w9 WHERE full_name = %s
    """, (w9_full_name,), fetch=False)
 
    flash(f'W-9 for {w9_full_name} has been deleted.', 'success')
    return redirect(url_for('admin_w9s'))

@app.route('/payroll')
@login_required
@demo_readonly
@subscription_required(['pro'])
def payroll():
    company_id = current_user.company_id
    employees = EmployeeDB.get_all(company_id)
    pay_runs = PayRunDB.get_all(company_id)
    summary = EmployeeDB.get_summary(company_id)

    # Group by department
    departments = {}
    for emp in employees:
        dept = emp.get('department') or 'General'
        if dept not in departments:
            departments[dept] = {'employees': [], 'total': 0}
        departments[dept]['employees'].append(emp)
        departments[dept]['total'] += float(emp['salary'])

    return render_template('payroll.html',
                           employees=employees,
                           pay_runs=pay_runs,
                           summary=summary,
                           departments=departments)


@app.route('/payroll/add-employee', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_employee():
    if request.method == 'POST':
        employee = {
            'name': request.form['name'],
            'title': request.form.get('title', ''),
            'department': request.form.get('department', ''),
            'employment_type': request.form.get('employment_type', 'full_time'),
            'salary': float(request.form['salary']),
            'pay_frequency': request.form.get('pay_frequency', 'monthly'),
            'start_date': request.form.get('start_date') or None,
            'equity_percent': float(request.form.get('equity_percent') or 0),
            'notes': request.form.get('notes', '')
        }
        emp = EmployeeDB.create(employee, current_user.company_id)

        flash(f"{employee['name']} added and salary transaction created!", 'success')
        return redirect(url_for('payroll'))
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('add_employee.html', today=today)


@app.route('/payroll/edit-employee/<employee_id>', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def edit_employee(employee_id):
    employee = EmployeeDB.get_by_id(employee_id, current_user.company_id)
    if not employee:
        flash('Employee not found.', 'error')
        return redirect(url_for('payroll'))
    if request.method == 'POST':
        updated = {
            'name': request.form['name'],
            'title': request.form.get('title', ''),
            'department': request.form.get('department', ''),
            'employment_type': request.form.get('employment_type', 'full_time'),
            'salary': float(request.form['salary']),
            'pay_frequency': request.form.get('pay_frequency', 'monthly'),
            'start_date': request.form.get('start_date') or None,
            'equity_percent': float(request.form.get('equity_percent') or 0),
            'notes': request.form.get('notes', '')
        }
        EmployeeDB.update(employee_id, updated, current_user.company_id)
        flash('Employee updated!', 'success')
        return redirect(url_for('payroll'))
    return render_template('add_employee.html', employee=employee, editing=True)


@app.route('/payroll/delete-employee/<employee_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_employee(employee_id):
    EmployeeDB.delete(employee_id, current_user.company_id)
    flash('Employee removed.', 'success')
    return redirect(url_for('payroll'))


@app.route('/payroll/add-run', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_pay_run():
    company_id = current_user.company_id
    employees = EmployeeDB.get_all(company_id)
    if request.method == 'POST':
        total_gross = float(request.form['total_gross'])
        total_tax = float(request.form.get('total_tax') or 0)
        run_date = request.form['run_date']

        pay_run = {
            'run_date': run_date,
            'period_start': request.form['period_start'],
            'period_end': request.form['period_end'],
            'total_gross': total_gross,
            'total_tax': total_tax,
            'total_net': total_gross - total_tax,
            'notes': request.form.get('notes', '')
        }
        PayRunDB.create(pay_run, company_id)

        # Create a transaction per employee for this pay run
        for emp in employees:
            tx = {
                'date': run_date,
                'amount': float(emp['salary']),
                'type': 'expense',
                'category': 'Salaries',
                'description': f"Salary — {emp['name']}",
                'recurring': False
            }
            TransactionDB.create(tx, company_id)

        flash(f'Pay run recorded and {len(employees)} salary transactions created!', 'success')
        return redirect(url_for('payroll'))

    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('add_pay_run.html', employees=employees, today=today)


@app.route('/payroll/delete-run/<run_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_pay_run(run_id):
    company_id = current_user.company_id

    # Get the pay run date before deleting
    run = PayRunDB.get_by_id(run_id, company_id)
    if run:
        # Delete salary transactions for that run date
        TransactionDB.delete_by_date_and_category(
            str(run['run_date']),
            'Salaries',
            company_id
        )
        PayRunDB.delete(run_id, company_id)
        flash('Pay run and salary transactions deleted.', 'success')

    return redirect(url_for('payroll'))

@app.route('/payroll/upload-contract/<employee_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def upload_contract(employee_id):
    file = request.files.get('contract')
    if file and file.filename:
        EmployeeDB.save_contract(
            employee_id, current_user.company_id,
            file.read(), file.filename, file.mimetype
        )
        flash('Contract uploaded!', 'success')
    return redirect(url_for('payroll'))

@app.route('/payroll/upload-resume/<employee_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def upload_employee_resume(employee_id):
    file = request.files.get('resume')
    if file and file.filename:
        EmployeeDB.save_resume(
            employee_id, current_user.company_id,
            file.read(), file.filename, file.mimetype
        )
        flash('Resume uploaded!', 'success')
    return redirect(url_for('payroll'))

@app.route('/payroll/download-contract/<employee_id>')
@login_required
@demo_readonly
@subscription_required(['pro'])
def download_contract(employee_id):
    emp = EmployeeDB.get_full(employee_id, current_user.company_id)
    if not emp or not emp.get('contract_data'):
        flash('No contract found.', 'error')
        return redirect(url_for('payroll'))
    return send_file(
        io.BytesIO(bytes(emp['contract_data'])),
        mimetype=emp['contract_type'],
        as_attachment=True,
        download_name=emp['contract_name']
    )

@app.route('/payroll/download-resume/<employee_id>')
@login_required
@demo_readonly
@subscription_required(['pro'])
def download_employee_resume(employee_id):
    emp = EmployeeDB.get_full(employee_id, current_user.company_id)
    if not emp or not emp.get('resume_data'):
        flash('No resume found.', 'error')
        return redirect(url_for('payroll'))
    return send_file(
        io.BytesIO(bytes(emp['resume_data'])),
        mimetype=emp['resume_type'],
        as_attachment=True,
        download_name=emp['resume_name']
    )

@app.route('/payroll/generate-contract/<employee_id>')
@login_required
@demo_readonly
@subscription_required(['pro'])
def generate_contract(employee_id):
    emp = EmployeeDB.get_full(employee_id, current_user.company_id)
    company = CompanyDB.get(current_user.company_id)
    if not emp:
        flash('Employee not found.', 'error')
        return redirect(url_for('payroll'))
    return render_template('employee_contract.html', emp=emp, company=company,
                           date=datetime.now().strftime('%B %d, %Y'))

@app.route('/payroll/sign-contract/<employee_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def sign_contract(employee_id):
    company_sig = request.form.get('company_signature', '').strip()
    employee_email = request.form.get('employee_email', '').strip()

    if not company_sig:
        flash('Please enter your signature.', 'error')
        return redirect(url_for('generate_contract', employee_id=employee_id))

    if not employee_email:
        flash('Please enter the employee email.', 'error')
        return redirect(url_for('generate_contract', employee_id=employee_id))

    token = EmployeeDB.save_signatures(employee_id, current_user.company_id, company_sig)
    EmployeeDB.save_employee_email(employee_id, current_user.company_id, employee_email)

    emp = EmployeeDB.get_by_id(employee_id, current_user.company_id)
    company = CompanyDB.get(current_user.company_id)
    sign_url = f"{request.host_url}sign-contract/{token}"

    send_in_background(
        send_contract_signing_request,
        employee_email,
        emp['name'],
        company['name'],
        sign_url
    )

    flash(f'Contract signed and sent to {employee_email}!', 'success')
    return redirect(url_for('payroll'))


@app.route('/sign-contract/<token>', methods=['GET', 'POST'])
def employee_sign_contract(token):
    """Public route — employee signs their contract"""
    emp = EmployeeDB.get_by_contract_token(token)
    if not emp:
        return render_template('404.html'), 404

    if emp.get('contract_status') == 'fully_signed':
        return render_template('contract_already_signed.html', emp=emp)

    if request.method == 'POST':
        employee_sig = request.form.get('employee_signature', '').strip()
        employee_email = request.form.get('employee_email', '').strip()

        if not employee_sig:
            flash('Please enter your signature.', 'error')
            return render_template('employee_sign_contract.html', emp=emp, token=token)

        EmployeeDB.save_employee_signature(emp['id'], employee_sig, employee_email)
        return render_template('contract_signed_success.html', emp=emp)

    return render_template('employee_sign_contract.html', emp=emp, token=token)

# ============================================
# CAP TABLE ROUTES
# ============================================

@app.route('/cap-table')
@login_required
@demo_readonly
@subscription_required(['pro'])
def cap_table():
    summary = CapTableDB.get_summary(current_user.company_id)
    return render_template('cap_table.html', summary=summary)


@app.route('/cap-table/add', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def add_cap_entry():
    if request.method == 'POST':
        entry = {
            'shareholder_name': request.form['shareholder_name'],
            'shareholder_type': request.form.get('shareholder_type', 'founder'),
            'share_class': request.form.get('share_class', 'common'),
            'shares': float(request.form['shares']),
            'price_per_share': float(request.form.get('price_per_share') or 0),
            'investment_amount': float(request.form.get('investment_amount') or 0),
            'grant_date': request.form.get('grant_date') or None,
            'vesting_schedule': request.form.get('vesting_schedule', ''),
            'notes': request.form.get('notes', '')
        }
        CapTableDB.create(entry, current_user.company_id)
        flash('Shareholder added!', 'success')
        return redirect(url_for('cap_table'))
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('add_cap_entry.html', today=today)


@app.route('/cap-table/edit/<entry_id>', methods=['GET', 'POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def edit_cap_entry(entry_id):
    entry = CapTableDB.get_by_id(entry_id, current_user.company_id)
    if not entry:
        flash('Entry not found.', 'error')
        return redirect(url_for('cap_table'))
    if request.method == 'POST':
        updated = {
            'shareholder_name': request.form['shareholder_name'],
            'shareholder_type': request.form.get('shareholder_type', 'founder'),
            'share_class': request.form.get('share_class', 'common'),
            'shares': float(request.form['shares']),
            'price_per_share': float(request.form.get('price_per_share') or 0),
            'investment_amount': float(request.form.get('investment_amount') or 0),
            'grant_date': request.form.get('grant_date') or None,
            'vesting_schedule': request.form.get('vesting_schedule', ''),
            'notes': request.form.get('notes', '')
        }
        CapTableDB.update(entry_id, updated, current_user.company_id)
        flash('Entry updated!', 'success')
        return redirect(url_for('cap_table'))
    return render_template('add_cap_entry.html', entry=entry, editing=True)


@app.route('/cap-table/delete/<entry_id>', methods=['POST'])
@login_required
@demo_readonly
@subscription_required(['pro'])
def delete_cap_entry(entry_id):
    CapTableDB.delete(entry_id, current_user.company_id)
    flash('Entry deleted.', 'success')
    return redirect(url_for('cap_table'))

# ============================================
# FEEDBACK & CONTACT ROUTES
# ============================================

@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    if request.method == 'POST':
        feedback_data = {
            'name': request.form.get('name', '').strip(),
            'email': request.form.get('email', '').strip(),
            'type': request.form.get('type', 'general'),
            'message': request.form.get('message', '').strip()
        }
        if feedback_data['message']:
            FeedbackDB.create(feedback_data)
            flash('Thanks for your feedback! We really appreciate it.', 'success')
            return redirect(url_for('feedback'))
    return render_template('feedback.html')


@app.route('/admin/feedback')
@login_required
@demo_readonly

def admin_feedback():
    if not current_user.is_admin:
        return render_template('404.html')
    feedback_list = FeedbackDB.get_all()
    contact_list = ContactDB.get_all()
    return render_template('admin_feedback.html',
                           feedback_list=feedback_list,
                           contact_list=contact_list)


@app.route('/admin/feedback/status/<feedback_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_feedback_status(feedback_id):
    if not current_user.is_admin:
        return render_template('404.html')
    status = request.form.get('status', 'reviewed')
    FeedbackDB.update_status(feedback_id, status)
    return redirect(url_for('admin_feedback'))


@app.route('/admin/feedback/delete/<feedback_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_feedback_delete(feedback_id):
    if not current_user.is_admin:
        return render_template('404.html')
    FeedbackDB.delete(feedback_id)
    return redirect(url_for('admin_feedback'))


@app.route('/admin/contact/status/<message_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_contact_status(message_id):
    if not current_user.is_admin:
        return render_template('404.html')
    status = request.form.get('status', 'replied')
    ContactDB.update_status(message_id, status)
    return redirect(url_for('admin_feedback'))


@app.route('/admin/contact/delete/<message_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_contact_delete(message_id):
    if not current_user.is_admin:
        return render_template('404.html')
    ContactDB.delete(message_id)
    return redirect(url_for('admin_feedback'))

# ============================================
# OUTREACH ROUTES
# ============================================

@app.route('/admin/outreach')
@login_required
@demo_readonly

def admin_outreach():
    if not current_user.is_admin:
        return render_template('404.html')
    contacts = OutreachDB.get_all()
    summary = OutreachDB.get_summary()
    return render_template('admin_outreach.html', contacts=contacts, summary=summary)

@app.route('/admin/outreach/add', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_add():
    if not current_user.is_admin:
        return render_template('404.html')
    data = {
        'name': request.form.get('name', '').strip(),
        'email': request.form.get('email', '').strip(),
        'company': request.form.get('company', '').strip(),
        'role': request.form.get('role', '').strip(),
        'type': request.form.get('type', 'investor'),
        'status': 'not_contacted',
        'notes': request.form.get('notes', '').strip()
    }
    OutreachDB.create(data)
    flash(f'{data["name"]} added to outreach!', 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/status/<outreach_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_status(outreach_id):
    if not current_user.is_admin:
        return render_template('404.html')
    status = request.form.get('status', 'contacted')
    notes = request.form.get('notes', '')
    OutreachDB.update_status(outreach_id, status, notes)
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/delete/<outreach_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_delete(outreach_id):
    if not current_user.is_admin:
        return render_template('404.html')
    OutreachDB.delete(outreach_id)
    flash('Contact removed.', 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/clear', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_clear():
    if not current_user.is_admin:
        return render_template('404.html')
    OutreachDB.delete_all()
    flash('Outreach log cleared.', 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/import-csv', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_import_csv():
    if not current_user.is_admin:
        return render_template('404.html')
    import csv
    import io as _io
    file = request.files.get('csv_file')
    contact_type = request.form.get('contact_type', 'investor')

    if not file or not file.filename.endswith('.csv'):
        flash('Please upload a valid CSV file.', 'error')
        return redirect(url_for('admin_outreach'))

    content = file.read().decode('utf-8')
    reader = csv.DictReader(_io.StringIO(content))
    added = 0
    for row in reader:
        name = (row.get('founder') or '').strip()
        email = (row.get('company_email') or '').strip()
        company = (row.get('startup') or '').strip()
        website = (row.get('website') or '').strip()

        if not name or not email:
            continue

        OutreachDB.create({
            'name': name,
            'email': email,
            'company': company,
            'role': 'Founder',
            'type': contact_type,
            'notes': f"Website: {website}" if website else ''
        })
        added += 1

    flash(f'Imported {added} contacts as {contact_type.title()}!', 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/send-email/<outreach_id>', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_send_email(outreach_id):
    if not current_user.is_admin:
        return render_template('404.html')
    contact = OutreachDB.get_by_id(outreach_id)
    if not contact:
        flash('Contact not found.', 'error')
        return redirect(url_for('admin_outreach'))

    subject = request.form.get('subject', '').strip()
    body = request.form.get('body', '').strip()
    sender = request.form.get('sender', '').strip()
    if sender == "pranav":
        sender_name = "Pranav Kethireddy"
        sender_email = "pranav.kethireddy@fintoit.com"
    else:
        sender_name = "Fintoit"
        sender_email = "founders@fintoit.com"

    if not subject or not body:
        flash('Subject and body are required.', 'error')
        return redirect(url_for('admin_outreach'))

    def send():
        try:
            import resend as r
            r.api_key = os.getenv('RESEND_API_KEY')
            r.Emails.send({
                "from": f"{sender_name} <{sender_email}>",
                "to": contact['email'],
                "subject": subject,
                "text": body
            })
            print(f"✅ Outreach email sent to {contact['email']}")
        except Exception as e:
            print(f"❌ Outreach email failed: {e}")

    import threading
    threading.Thread(target=send, daemon=True).start()

    OutreachDB.update_status(outreach_id, 'contacted', f"Emailed: {subject}")
    flash(f"Email sent to {contact['name']} ({contact['email']})!", 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/bulk-email', methods=['POST'])
@login_required
@demo_readonly

def admin_outreach_bulk_email():
    if not current_user.is_admin:
        return render_template('404.html')

    subject_template = request.form.get('subject', '')
    body_template = request.form.get('body', '')
    target_status = request.form.get('target_status', 'not_contacted')
    sender = request.form.get('sender', '').strip()
    if sender == "pranav":
        sender_name = "Pranav Kethireddy"
        sender_email = "pranav.kethireddy@fintoit.com"
    else:
        # Bug fix 1: fallback sender so sender_name/sender_email are always defined
        sender_name = "Fintoit"
        sender_email = "hello@fintoit.com"

    if not subject_template or not body_template:
        flash('Subject and message are required.', 'error')
        return redirect('/admin/outreach')

    # Get contacts by selected status
    if target_status == 'all':
        contacts = Database.execute("SELECT * FROM outreach ORDER BY created_at ASC")
    else:
        contacts = Database.execute(
            "SELECT * FROM outreach WHERE status = %s ORDER BY created_at ASC",
            (target_status,)  # Bug fix 2: must be a tuple — trailing comma required
        )
    contacts = [dict(c) for c in contacts] if contacts else []

    if not contacts:
        flash(f'No contacts with status "{target_status.replace("_", " ").title()}" found.', 'error')
        return redirect('/admin/outreach')

    sent = 0
    failed = 0

    import resend as r
    r.api_key = os.getenv('RESEND_API_KEY')

    for contact in contacts:
        name = contact.get('name', 'there')
        subject = subject_template.replace('{name}', name)
        body = body_template.replace('{name}', name)

        # Bug fix 3: wrap in try/except so one bad send doesn't crash the whole loop
        try:
            result = r.Emails.send({
                "from": f"{sender_name} <{sender_email}>",
                "to": contact['email'],
                "subject": subject,
                "text": body
            })
            success = bool(result and getattr(result, 'id', None))
        except Exception as e:
            print(f"❌ Bulk email failed for {contact['email']}: {e}")
            success = False

        if success:
            sent += 1
            # Mark as contacted
            Database.execute(
                "UPDATE outreach SET status = 'contacted', last_contacted_at = NOW() WHERE id = %s",
                (contact['id'],),
                fetch=False
            )
        else:
            failed += 1

    flash(f'Bulk email complete: {sent} sent, {failed} failed.', 'success' if failed == 0 else 'warning')
    return redirect('/admin/outreach')

@app.route('/admin/outreach/edit/<outreach_id>', methods=['POST'])
@login_required
@demo_readonly
def admin_outreach_edit(outreach_id):
    if not current_user.is_admin:
        return render_template('404.html')
    data = {
        'name': request.form.get('name', '').strip(),
        'email': request.form.get('email', '').strip(),
        'company': request.form.get('company', '').strip(),
        'role': request.form.get('role', '').strip(),
        'type': request.form.get('type', 'investor'),
        'notes': request.form.get('notes', '').strip(),
    }
    Database.execute("""
        UPDATE outreach SET name=%s, email=%s, company=%s, role=%s, type=%s, notes=%s
        WHERE id=%s
    """, (data['name'], data['email'], data['company'], data['role'], data['type'], data['notes'], outreach_id), fetch=False)
    flash(f'Contact updated!', 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/outreach/bulk-status', methods=['POST'])
@login_required
@demo_readonly
def admin_outreach_bulk_status():
    if not current_user.is_admin:
        return render_template('404.html')
    ids = request.form.getlist('contact_ids')
    new_status = request.form.get('bulk_status', '')
    if not ids or not new_status:
        flash('Select at least one contact and a status.', 'error')
        return redirect(url_for('admin_outreach'))
    for cid in ids:
        Database.execute(
            "UPDATE outreach SET status=%s WHERE id=%s",
            (new_status, cid), fetch=False
        )
    flash(f'Updated {len(ids)} contact(s) to "{new_status.replace("_", " ").title()}".', 'success')
    return redirect(url_for('admin_outreach'))

@app.route('/admin/zoho/connect')
@login_required
def admin_zoho_connect():
    """Step 1 — Show the admin the Zoho OAuth connect page."""
    if not current_user.is_admin:
        return render_template('404.html')
    connected = is_connected()
    auth_url = None
    if not connected:
        state = secrets.token_urlsafe(32)
        session['zoho_oauth_state'] = state
        auth_url = get_authorization_url(state)
    return render_template('admin_zoho_connect.html',
                           connected=connected,
                           auth_url=auth_url)


@app.route('/admin/zoho/callback')
@login_required
def admin_zoho_callback():
    """Step 2 — Zoho redirects here with ?code=... after the user authorises."""
    if not current_user.is_admin:
        return render_template('404.html')

    code  = request.args.get('code', '')
    error = request.args.get('error', '')
    state = request.args.get('state', '')

    expected_state = session.pop('zoho_oauth_state', None)
    if not expected_state or state != expected_state:
        flash('Invalid OAuth state: possible CSRF. Please reconnect.', 'error')
        return redirect(url_for('admin_zoho_connect'))

    if error:
        flash(f'Zoho authorisation denied: {error}', 'error')
        return redirect(url_for('admin_zoho_connect'))

    if not code:
        flash('No authorisation code received from Zoho.', 'error')
        return redirect(url_for('admin_zoho_connect'))

    try:
        exchange_code_for_tokens(code)
        flash('✅ Zoho CRM connected! Tokens saved. Auto-refresh is active.', 'success')
    except Exception as e:
        flash(f'Zoho token exchange failed: {e}', 'error')

    return redirect(url_for('admin_zoho_connect'))


@app.route('/admin/zoho/disconnect', methods=['POST'])
@login_required
@demo_readonly
def admin_zoho_disconnect():
    """Remove stored Zoho tokens."""
    if not current_user.is_admin:
        return render_template('404.html')
    zoho_disconnect()
    flash('Zoho CRM disconnected. Tokens removed.', 'success')
    return redirect(url_for('admin_zoho_connect'))


@app.route('/admin/outreach/zoho-sync', methods=['POST'])
@login_required
@demo_readonly
def admin_outreach_zoho_sync():
    """Sync outreach contacts to Zoho CRM as Leads (uses auto-refresh tokens)."""
    if not current_user.is_admin:
        return render_template('404.html')

    if not is_connected():
        flash('Zoho is not connected. Visit Admin → Zoho CRM Connect first.', 'error')
        return redirect(url_for('admin_outreach'))

    contacts = OutreachDB.get_all()

    try:
        result = sync_contacts_to_zoho(contacts)
    except RuntimeError as e:
        flash(str(e), 'error')
        return redirect(url_for('admin_outreach'))

    synced = result['synced']
    failed = result['failed']
    for err in result['errors'][:5]:
        print(f"[ZOHO SYNC ERROR] {err}")

    flash(
        f'Zoho sync complete: {synced} synced, {failed} failed.',
        'success' if failed == 0 else 'warning'
    )
    return redirect(url_for('admin_outreach'))

# ============================================
# PROMO EMAIL CAPTURE  (homepage popup)
# ============================================
#
# The code itself lives in Stripe as a Promotion Code. Checkout already passes
# allow_promotion_codes=True, so whatever is configured there is what actually
# applies at the till — this route only hands out the string. Keeping the value
# in an env var means the code can be rotated or retired without a deploy, and
# without this page ever promising a discount Stripe won't honour.

PROMO_CODE = os.getenv('PROMO_CODE', 'AICFO')
PROMO_PERCENT = os.getenv('PROMO_PERCENT', '15')

# Naive per-IP throttle. This endpoint is unauthenticated and sends mail, so
# without it someone can make us deliver unsolicited email to arbitrary
# addresses and get the sending domain flagged. Per-process, like
# Modules/cache.py — with several workers the effective limit is
# PROMO_MAX_PER_WINDOW x workers, which is still far below abuse levels.
_promo_hits = {}
_PROMO_WINDOW_SECONDS = 3600
_PROMO_MAX_PER_WINDOW = 5


def _promo_rate_limited(ip):
    import time
    now = time.time()
    hits = [t for t in _promo_hits.get(ip, []) if now - t < _PROMO_WINDOW_SECONDS]
    if len(hits) >= _PROMO_MAX_PER_WINDOW:
        _promo_hits[ip] = hits
        return True
    hits.append(now)
    _promo_hits[ip] = hits
    if len(_promo_hits) > 5000:                 # bound memory
        for stale in [k for k, v in _promo_hits.items()
                      if not any(now - t < _PROMO_WINDOW_SECONDS for t in v)]:
            _promo_hits.pop(stale, None)
    return False


def _unsubscribe_token(email):
    from itsdangerous import URLSafeSerializer
    return URLSafeSerializer(app.secret_key, salt='newsletter-unsub').dumps(email)


@app.route('/promo/subscribe', methods=['POST'])
def promo_subscribe():
    """Capture an anonymous visitor's email and return the discount code.

    Responds with JSON so the popup can reveal the code in place.
    """
    import re

    payload = request.get_json(silent=True) or {}
    email = (request.form.get('email') or payload.get('email') or '').strip()

    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({'ok': False, 'error': 'Please enter a valid email address.'}), 400

    ip = (request.headers.get('X-Forwarded-For', request.remote_addr or '')
          .split(',')[0].strip())
    if _promo_rate_limited(ip):
        return jsonify({'ok': False,
                        'error': 'Too many requests. Please try again later.'}), 429

    already = False
    try:
        already = NewsletterDB.exists(email)
        if not already:
            NewsletterDB.subscribe(email)
    except Exception as exc:                                  # noqa: BLE001
        # Never withhold the code because our list write failed — the visitor
        # did their part.
        print(f"[PROMO SUBSCRIBE DB ERROR] {type(exc).__name__}")

    # Mirror into Resend so the address is actually reachable by a broadcast.
    # Backgrounded: the visitor is waiting on this response for their code, and
    # a slow provider call should not sit in front of it.
    try:
        from Modules import contacts_sync
        from Modules.email_utils import send_in_background
        send_in_background(contacts_sync.sync_subscriber, email)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[PROMO CONTACT SYNC ERROR] {type(exc).__name__}")

    # Mail the code so it survives closing the tab — but only the first time,
    # so a repeat submitter can't be used to spam an address.
    if not already:
        try:
            from Modules.email_utils import send_html_email
            unsub = url_for('newsletter_unsubscribe',
                            token=_unsubscribe_token(email), _external=True)
            send_html_email(
                from_name="Fintoit",
                to=email,
                subject=f"Your {PROMO_PERCENT}% off code: {PROMO_CODE}",
                html_body=f"""
                <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:32px;background:#f7f6f2;border-radius:12px;">
                    <h2 style="color:#0a0a0f;margin-top:0;">Here's {PROMO_PERCENT}% off Fintoit</h2>
                    <p style="color:#6b6b7a;">Enter this code at checkout:</p>
                    <div style="font-size:2rem;font-weight:800;letter-spacing:0.18em;color:#0a0a0f;margin:24px 0;">{PROMO_CODE}</div>
                    <p style="color:#6b6b7a;">Applies to your first payment on any paid plan.</p>
                    <p style="margin:28px 0 0;">
                      <a href="{url_for('pricing', _external=True)}"
                         style="background:#0a0a0f;color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:700;">See plans →</a>
                    </p>
                    <hr style="border:none;border-top:1px solid #e2e1db;margin:28px 0 14px;">
                    <p style="color:#9ca3af;font-size:0.75rem;line-height:1.6;margin:0;">
                      You're receiving this because you requested a discount code at fintoit.com.<br>
                      Fintoit · Collin County, TX, USA<br>
                      <a href="{unsub}" style="color:#9ca3af;">Unsubscribe</a>
                    </p>
                </div>"""
            )
        except Exception as exc:                              # noqa: BLE001
            print(f"[PROMO SUBSCRIBE EMAIL ERROR] {type(exc).__name__}")

    return jsonify({
        'ok': True,
        'code': PROMO_CODE,
        'percent': PROMO_PERCENT,
        'already': already,
    })


@app.route('/admin/newsletter/sync', methods=['POST'])
@login_required
def admin_newsletter_sync():
    """Push every existing newsletter_subscribers row into Resend.

    Deliberately a manual admin action rather than something that runs on
    deploy. These people subscribed under an earlier description of what they
    were signing up for, so moving them into a marketing audience is a judgement
    call about consent — not a migration.
    """
    if not current_user.is_admin:
        return render_template('404.html'), 404

    from Modules import contacts_sync
    if not contacts_sync.enabled():
        flash('RESEND_API_KEY is not configured. Nothing to sync to.', 'error')
        return redirect(url_for('admin_panel'))

    try:
        rows = NewsletterDB.get_all() or []
    except Exception as exc:                                  # noqa: BLE001
        flash(f'Could not read the subscriber list ({type(exc).__name__}).', 'error')
        return redirect(url_for('admin_panel'))

    emails = [r['email'] for r in rows if r.get('email')]
    synced, failed = contacts_sync.sync_many(emails)

    flash(
        f'Resend contact sync complete: {synced} synced, {failed} failed '
        f'({len(emails)} on the list).',
        'success' if failed == 0 else 'warning')
    return redirect(url_for('admin_panel'))


@app.route('/newsletter/unsubscribe/<token>')
def newsletter_unsubscribe(token):
    """One-click unsubscribe. Signed so a link cannot be forged for someone
    else's address, and required by CAN-SPAM on any marketing mail."""
    from itsdangerous import URLSafeSerializer, BadSignature
    try:
        email = URLSafeSerializer(app.secret_key,
                                  salt='newsletter-unsub').loads(token)
    except BadSignature:
        return render_template('404.html'), 404

    try:
        NewsletterDB.delete(email)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[UNSUBSCRIBE ERROR] {type(exc).__name__}")

    # Resend is what actually sends the broadcasts, so removing the local row
    # is not enough — an address still marked subscribed there would keep
    # receiving mail after opting out. Done inline rather than in a background
    # thread so a failure is logged against this request.
    try:
        from Modules import contacts_sync
        contacts_sync.mark_unsubscribed(email)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[UNSUBSCRIBE SYNC ERROR] {type(exc).__name__}")

    return render_template('unsubscribed.html', email=email)


# ============================================
# Blog ROUTES
# ============================================

@app.route("/blog")
def index():
    query = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    posts = search_posts(query=query, category=category)
    featured = next((p for p in POSTS if p.get("featured")), None)

    # Don't show featured in the grid if it matches the current filter
    grid_posts = [p for p in posts if not p.get("featured")]

    return render_template(
        "blog.html",
        posts=grid_posts,
        featured=featured,
        categories=CATEGORIES,
        active_category=category or "All",
        query=query,
        total=len(posts),
    )

@app.route("/blog/<slug>")
def post(slug):
    p = get_post(slug)
    if not p:
        abort(404)
    related = get_related(p)
    return render_template(f"posts/{slug}.html", post=p, related=related)


@app.route("/blog/subscribe", methods=["POST"])
def blogsubscribe():
    email = request.form.get("email")

    if not email:
        return "Email required", 400

    email = request.form.get("email")

    if not email:
        return redirect("/?error=missing_email")
    
    # save to DB (ignore if already exists)
    if not NewsletterDB.exists(email):
        NewsletterDB.subscribe(email)

    # Same list, so the same mirror into Resend — otherwise blog subscribers
    # exist locally but are invisible to any broadcast.
    try:
        from Modules import contacts_sync
        from Modules.email_utils import send_in_background
        send_in_background(contacts_sync.sync_subscriber, email)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[BLOG CONTACT SYNC ERROR] {type(exc).__name__}")
    html_body = """
    <div style="font-family:Arial,sans-serif;line-height:1.6;">
      <h2>Welcome to Fintoit 👋</h2>
      <p>Thanks for subscribing to our monthly newsletter.</p>

      <p>
        Every month you'll receive:
        <ul>
          <li>Startup finance insights</li>
          <li>New blog posts</li>
          <li>Product updates</li>
        </ul>
      </p>

      <p><strong>Test email notice:</strong> We just sent this email to confirm delivery.</p>

      <p>
        If you don’t see future emails, check spam/promotions.
      </p>

      <hr />
      <p style="color:#666;font-size:12px;">— Fintoit Blog Team</p>
    </div>
    """

    send_html_email(
        to=email,
        subject="Welcome to Fintoit Newsletter 🎉",
        html_body=html_body,
        from_name="Blog",
    )

    return render_template("subscribed.html")


# ============================================
# PLAID — bank account linking & transaction sync
# ============================================
@app.route('/plaid/connect')
@login_required
def plaid_connect_page():
    if not (os.getenv('PLAID_CLIENT_ID') and os.getenv('PLAID_SECRET')):
        abort(404)
    return render_template('plaid_link.html')


@app.route('/plaid/create_link_token', methods=['POST'])
@login_required
def plaid_create_link_token():
    result = create_link_token(current_user.company_id)
    return jsonify(result), (400 if 'error' in result else 200)


@app.route('/plaid/exchange_public_token', methods=['POST'])
@login_required
@demo_readonly
def plaid_exchange_public_token():
    data = request.get_json(silent=True) or {}
    public_token = data.get('public_token')
    if not public_token:
        return jsonify({'error': 'public_token is required'}), 400
    result = exchange_public_token(current_user.company_id, public_token)
    return jsonify(result), (400 if 'error' in result else 200)


@app.route('/plaid/items', methods=['GET'])
@login_required
def plaid_list_items():
    return jsonify({'items': get_items_for_company(current_user.company_id)})


@app.route('/plaid/disconnect', methods=['POST'])
@login_required
@demo_readonly
def plaid_disconnect():
    data = request.get_json(silent=True) or {}
    item_id = data.get('item_id')
    if not item_id:
        return jsonify({'error': 'item_id is required'}), 400
    result = disconnect_item(current_user.company_id, item_id)
    return jsonify(result), (400 if 'error' in result else 200)


@app.route('/plaid/sync', methods=['POST'])
@login_required
@demo_readonly
def plaid_manual_sync():
    return jsonify(sync_transactions(current_user.company_id))


@app.route('/plaid/webhook', methods=['POST'])
def plaid_webhook_route():
    # Server-to-server: verify Plaid's signed JWT over the RAW body before trusting it.
    if not verify_webhook(request.get_data(), request.headers.get('Plaid-Verification', '')):
        return jsonify({'error': 'invalid signature'}), 401
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(handle_webhook(payload))


if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true', host='0.0.0.0', port=PORT)