import csv
import smtplib
import time
import random
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Add parent directory to path so we can import database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from database import Database

# -------------------
# CONFIG
# -------------------

EMAIL = os.getenv('MAIL_USERNAME')
PASSWORD = os.getenv('MAIL_PASSWORD')

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

CSV_FILE = os.getenv(
    "OUTREACH_CSV_FILE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Outreach", "Fintoit_company_targets.csv"),
)

START_ROW = 0
END_ROW = 20

TEST_MODE = False  # True = send only to yourself

# -------------------
# DATABASE LOGGING
# -------------------

def log_to_db(founder, startup, email, status, error=None):
    try:
        sent_at = datetime.now() if status == 'sent' else None
        query = """
            INSERT INTO outreach_emails (founder, startup, email, status, error_message, sent_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        Database.execute(query, (founder, startup, email, status, error, sent_at), fetch=False)
    except Exception as e:
        print(f"⚠️  DB log failed: {e}")

# -------------------
# EMAIL TEMPLATE
# -------------------

def create_message(founder, startup):
    return f"""Dear {founder},

I came across {startup} and wanted to reach out.

I built Fintoit, a tool that helps startup founders track burn rate, runway, and financial forecasting more clearly.

It's already live, and I'm currently looking for a few founders willing to give honest feedback before I keep building. I'm not trying to sell anything. I mainly want to understand what's actually useful, confusing, or missing.

If you're open to it, I'd love to send the link and get your quick 2-minute reaction.

Thanks,
Pranav Kethireddy
pran.kethi@gmail.com
Fintoit.onrender.com
"""

# -------------------
# SEND EMAIL
# -------------------

def send_email(server, founder, startup, recipient):
    msg = MIMEMultipart()
    msg["From"] = EMAIL
    msg["To"] = recipient
    msg["Subject"] = f"Quick question for {startup}"
    msg.attach(MIMEText(create_message(founder, startup), "plain"))
    server.sendmail(EMAIL, recipient, msg.as_string())

# -------------------
# MAIN
# -------------------

def run():
    print("=" * 50)
    print(f"Fintoit Outreach Script")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Test Mode: {TEST_MODE}")
    print("=" * 50)

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL, PASSWORD)
        print("✅ SMTP connected\n")
    except Exception as e:
        print(f"❌ SMTP connection failed: {e}")
        return

    try:
        with open(CSV_FILE, newline="", encoding="utf-8") as file:
            reader = list(csv.DictReader(file))
            selected_rows = reader[START_ROW:END_ROW]
            print(f"📋 Processing {len(selected_rows)} rows (rows {START_ROW} to {END_ROW})\n")

            for i, row in enumerate(selected_rows, 1):
                founder = row.get("founder", "").strip()
                startup = row.get("startup", "").strip()
                recipient = row.get("company_email", "").strip()

                if not founder or not startup or not recipient:
                    print(f"[{i}] ⚠️  Skipping — missing data: {row}")
                    log_to_db(founder or '?', startup or '?', recipient or '?', 'skipped', 'Missing data')
                    skipped_count += 1
                    continue

                if TEST_MODE:
                    recipient = EMAIL

                print(f"[{i}/{len(selected_rows)}] Sending to: {founder} | {startup} | {recipient}")

                try:
                    send_email(server, founder, startup, recipient)
                    print(f"  ✅ Sent")
                    log_to_db(founder, startup, recipient, 'sent')
                    sent_count += 1
                except Exception as e:
                    print(f"  ❌ Failed: {e}")
                    log_to_db(founder, startup, recipient, 'failed', str(e))
                    failed_count += 1

                if i < len(selected_rows):
                    delay = random.randint(30, 75)
                    print(f"  ⏳ Waiting {delay}s...\n")
                    time.sleep(delay)

    except FileNotFoundError:
        print(f"❌ CSV file not found: {CSV_FILE}")
        return

    server.quit()

    print("\n" + "=" * 50)
    print("OUTREACH COMPLETE")
    print(f"  ✅ Sent:    {sent_count}")
    print(f"  ❌ Failed:  {failed_count}")
    print(f"  ⚠️  Skipped: {skipped_count}")
    print(f"  Total:     {sent_count + failed_count + skipped_count}")
    print("=" * 50)
    print("View results at: https://Fintoit.onrender.com/admin/outreach")


if __name__ == "__main__":
    run()