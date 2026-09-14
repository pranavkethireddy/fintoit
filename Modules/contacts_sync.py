"""
Keep the Resend contact list in step with newsletter_subscribers.

Why this exists at all: the local table records who asked for marketing email,
but Resend is what actually sends it. Once a contact exists in Resend, the
local row stops being the source of truth for deliverability — and the
important half of that is UNSUBSCRIBES. Removing someone locally while Resend
still lists them as subscribed means the next broadcast reaches a person who
opted out, which is the exact thing CAN-SPAM prohibits. So the unsubscribe path
below matters more than the subscribe path.

Design rules
------------
* **Never break a user flow.** Every call is wrapped and returns a bool; a
  Resend outage must not stop someone getting their discount code or, worse,
  stop an unsubscribe from being recorded locally.
* **Unsubscribe means unsubscribed, not deleted.** Flipping `unsubscribed=True`
  makes Resend suppress the address permanently. Deleting the contact would let
  the same address be silently re-added by a later signup and start receiving
  mail again.
* **Silent no-op when unconfigured**, so nothing here is a deploy dependency.

Audience vs global contact
--------------------------
resend 2.24.0 accepts `audience_id` as optional: with it you get an
audience-scoped contact, without it a global one. Broadcasts can target either
an audience or a segment. Set RESEND_AUDIENCE_ID and contacts land in that
audience, which is the straightforward path to sending a broadcast; leave it
unset and they are created globally.
"""

import os


def enabled():
    """Resend is already configured for transactional mail, so contact sync
    needs no new credential — only the same key."""
    return bool(os.getenv('RESEND_API_KEY'))


def audience_id():
    return (os.getenv('RESEND_AUDIENCE_ID') or '').strip() or None


def _client():
    import resend
    resend.api_key = os.getenv('RESEND_API_KEY')
    return resend


def _params(email, first_name='', last_name='', unsubscribed=False):
    params = {'email': email, 'unsubscribed': bool(unsubscribed)}
    if first_name:
        params['first_name'] = first_name
    if last_name:
        params['last_name'] = last_name
    aud = audience_id()
    if aud:
        params['audience_id'] = aud
    return params


def sync_subscriber(email, first_name='', last_name=''):
    """Add (or revive) a contact in Resend. Returns True on success.

    Create is attempted first and update is the fallback, because an address
    that already exists must not be left in whatever state it was in — someone
    re-subscribing after an unsubscribe has to be flipped back to subscribed,
    or they silently never hear from us again.
    """
    if not enabled() or not email:
        return False

    resend = _client()
    params = _params(email, first_name, last_name, unsubscribed=False)

    try:
        resend.Contacts.create(params)
        return True
    except Exception as create_error:                      # noqa: BLE001
        try:
            update = dict(params)
            resend.Contacts.update(update)
            return True
        except Exception as update_error:                  # noqa: BLE001
            # Log the type only — an exception body can echo the address.
            print("[RESEND CONTACT SYNC FAILED] create=%s update=%s"
                  % (type(create_error).__name__, type(update_error).__name__))
            return False


def mark_unsubscribed(email):
    """Flag a contact as unsubscribed in Resend.

    This is the half that carries legal weight. If it fails, the caller should
    still complete the local unsubscribe — but the failure is worth surfacing
    in logs, because until it succeeds the address can still receive a
    broadcast.
    """
    if not enabled() or not email:
        return False

    resend = _client()
    params = {'email': email, 'unsubscribed': True}
    aud = audience_id()
    if aud:
        params['audience_id'] = aud

    try:
        resend.Contacts.update(params)
        return True
    except Exception as exc:                               # noqa: BLE001
        print("[RESEND UNSUBSCRIBE FAILED] %s — address may still receive "
              "broadcasts until this is retried" % type(exc).__name__)
        return False


def sync_many(emails):
    """Backfill helper. Returns (synced, failed).

    Deliberately sequential and not run automatically: pushing an existing list
    into a marketing audience is a decision about people who subscribed under
    an earlier description, not a migration to run on deploy.
    """
    synced = failed = 0
    for email in emails or []:
        if sync_subscriber(email):
            synced += 1
        else:
            failed += 1
    return synced, failed
