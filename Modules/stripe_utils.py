"""
Stripe integration for Fintoit
Handles checkout sessions, webhooks, and subscription management.
"""

import os
import stripe
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Plan definitions — edit display copy freely; keep price_id env vars correct
# ---------------------------------------------------------------------------



PLANS = {
    "free": {
    'name': 'Free',
    'display': '$0',
    'period': 'forever',
    'description': 'Core features, no credit card',
    'color': 'gray',
    'features': [
        'Dashboard & burn rate',
        'Runway tracking',
        'Transaction tracking',
    ],
    },
    'starter': {
        'name': 'Starter',
        'price_id': 'price_1TIZ4BAsD0D6h74vZtimvxaH',
        'amount': 4900,        # cents
        'display': '$49',
        'period': 'per month',
        'description': 'For growing startups',
        'color': 'blue',
        'features': [
            'Everything in Free',
            'Unlimited AI chat',
            'Unlimited investor reports',
            'Cash flow forecasting',
            'Scenario planning',
            'Budget alerts',
            'Anomaly detection'
        ],
    },
    'pro': {
        'name': 'Pro',
        'price_id': 'price_1TIZ5kAsD0D6h74vgcbwNMwi',
        'amount': 14900,
        'display': '$149',
        'period': 'per month',
        'description': 'The full CFO suite',
        'color': 'purple',
        'popular': True,
        'features': [
            'Everything in Starter',
            'Cap table management',
            'Payroll tracking',
            'Vendor management',
            'Board report PDF',
            'Priority support',
        ],
    },
}

FREE_PLAN = {
    'name': 'Free',
    'display': '$0',
    'period': 'forever',
    'description': 'Core features, no credit card',
    'color': 'gray',
    'features': [
        'Dashboard & burn rate',
        'Runway tracking',
        'Transaction tracking',
        'AI chat (10 messages/mo)',
        '1 investor report per month',
    ],
}


def _client():
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    return stripe


def create_checkout_session(plan_key: str, user_id: str, user_email: str,
                            success_url: str, cancel_url: str) -> str:
    plan = PLANS.get(plan_key)
    if not plan:
        raise ValueError(f"Plan '{plan_key}' does not exist.")

    price_id = plan.get('price_id')
    if not price_id:
        raise ValueError(f"Stripe price_id not set for plan '{plan_key}'")

    s = _client()
    session = s.checkout.Session.create(
        mode='subscription',
        payment_method_types=['card'],
        customer_email=user_email,

        # ✅ THIS IS THE FIX
        metadata={
            'user_id': str(user_id),
            'plan': plan_key
        },

        line_items=[{'price': price_id, 'quantity': 1}],
        subscription_data={
            'metadata': {  # also keep subscription metadata
                'user_id': str(user_id),
                'plan': plan_key
            }
        },
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
    )
    return session.url


def create_portal_session(stripe_customer_id: str, return_url: str) -> str:
    """Return a URL for the Stripe Customer Portal (manage / cancel subscription)."""
    s = _client()
    portal = s.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=return_url,
    )
    return portal.url


def handle_webhook(payload: bytes, sig_header: str):
    """
    Verify the Stripe webhook signature and parse the event.
    Raises stripe.error.SignatureVerificationError on bad signatures.
    """
    s = _client()
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    if webhook_secret:
        event = s.Webhook.construct_event(payload, sig_header, webhook_secret)
    else:
        import json
        event = json.loads(payload)
    return event