"""
Email utilities for Fintoit
Uses Resend API - works on Render (no SMTP port blocking)
"""

import os
import resend


def get_resend_client():
    resend.api_key = os.getenv('RESEND_API_KEY')
    return resend


def send_email(to: str, subject: str, body: str, from_name: str = 'Fintoit'):
    """
    Send a plain text email via Resend.
    Falls back to printing if no API key configured.
    """
    try:
        client = get_resend_client()
        client.Emails.send({
            "from": f"{from_name} <{from_name}@fintoit.com>",
            "to": to,
            "subject": subject,
            "text": body
        })
        print(f"✅ Email sent to {to}: {subject}")
        return True
    except Exception as e:
        print(f"❌ Email failed to {to}: {e}")
        return False


def send_html_email(to: str, subject: str, html_body: str, from_name: str = 'Fintoit'):
    """
    Send an HTML email via Resend.
    Falls back to printing if no API key configured.
    """
    try:
        client = get_resend_client()
        client.Emails.send({
            "from": f"{from_name} <{from_name}@fintoit.com>",
            "to": to,
            "subject": subject,
            "html": html_body
        })
        print(f"✅ Email sent to {to}: {subject}")
        return True
    except Exception as e:
        print(f"❌ Email failed to {to}: {e}")
        return False


def send_password_reset(to: str, reset_url: str):
    body = f"""Hi,

You requested a password reset for your Fintoit account.

Click the link below to reset your password (expires in 1 hour):

{reset_url}

If you didn't request this, you can safely ignore this email.

— Fintoit
"""
    return send_email(to, "Reset your Fintoit password", body)


def send_invoice_email(to: str, customer_name: str, invoice_number: str,
                       total_amount: float, invoice_date: str, due_date: str,
                       notes: str, company_name: str, reply_to_email: str):
    body = f"""Hi {customer_name},

Please find your invoice details below:

  Invoice #:    {invoice_number}
  Amount Due:   ${total_amount:,.2f}
  Invoice Date: {invoice_date}
  Due Date:     {due_date}
  Notes:        {notes or 'N/A'}

If you have any questions, please reply to this email {reply_to_email}.

Thanks,
{company_name}
{reply_to_email}
"""
    return send_email(to, f"Invoice {invoice_number} from {company_name}", body, company_name)


def send_contract_signing_request(to: str, employee_name: str,
                                   company_name: str, sign_url: str):
    body = f"""Hi {employee_name},

{company_name} has signed your employment contract and it's ready for your signature.

Please click the link below to review and sign:

{sign_url}

This link is unique to you. Once signed, both parties will have a fully executed contract.

Thanks,
{company_name}
"""
    return send_email(to, f"Please sign your employment contract ({company_name})", body, company_name)


def send_in_background(func, *args, **kwargs):
    """Run any email function in a background thread"""
    import threading
    thread = threading.Thread(target=func, args=args, kwargs=kwargs)
    thread.daemon = True
    thread.start()