"""
Database connection and helper functions for Fintoit
"""

import psycopg2
from psycopg2 import pool as _pg_pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import threading
from typing import List, Dict
import os
from dotenv import load_dotenv
from supabase import create_client
from Modules import cache as _cache
from datetime import datetime, timedelta
import os

#supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
DEMO_COMPANY_ID = '00000000-0000-0000-0000-000000000001'

# ── Connection pool ────────────────────────────────────────────────
# Reuse warm Postgres connections across requests instead of opening a brand-new
# TCP+TLS+auth connection on every query. This is the main fix for slow page
# loads: a page that runs several queries no longer pays a full connection
# handshake each time. The pool is per worker process; tune size with DB_POOL_MAX.
_POOL = None
_POOL_LOCK = threading.Lock()

def _get_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = _pg_pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=int(os.getenv("DB_POOL_MAX", "5")),
                    dsn=DATABASE_URL,
                    cursor_factory=RealDictCursor,
                    connect_timeout=10,
                    # TCP keepalives so managed Postgres (e.g. Supabase) doesn't
                    # silently drop idle pooled connections.
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )
    return _POOL


class Database:
    """Database connection handler"""
    
    @staticmethod
    @contextmanager
    def get_connection():
        """Get database connection (context manager)"""
        pool = _get_pool()
        conn = pool.getconn()
        errored = False
        try:
            yield conn
            conn.commit()
        except Exception as e:
            errored = True
            try:
                conn.rollback()
            except Exception:
                pass
            raise e
        finally:
            # Return the connection to the pool for reuse. Discard it only if it
            # errored or is dead, so a bad connection is never handed out again.
            try:
                pool.putconn(conn, close=errored or bool(getattr(conn, "closed", 0)))
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
    
    @staticmethod
    def execute(query, params=None, fetch=True):
        """Execute a query and return results"""
        for _attempt in (1, 2):
            try:
                with Database.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(query, params or ())
                        if fetch:
                            return cursor.fetchall()
                        return None
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # A pooled connection may have been dropped by the server while
                # idle; the bad one is discarded in get_connection, so retry once
                # with a fresh connection before giving up.
                if _attempt == 2:
                    raise
    
    @staticmethod
    def execute_one(query, params=None):
        """Execute query and return one result"""
        for _attempt in (1, 2):
            try:
                with Database.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(query, params or ())
                        return cursor.fetchone()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                if _attempt == 2:
                    raise
    

class TransactionDB:
    """Transaction database operations"""
    
    @staticmethod
    def get_all(company_id=DEMO_COMPANY_ID):
        """Get all transactions for a company"""
        query = """
            SELECT * FROM transactions 
            WHERE company_id = %s 
            ORDER BY date ASC
        """
        results = Database.execute(query, (company_id,))
        
        # Convert to list of dicts (same format as before)
        return [dict(row) for row in results] if results else []
    
    @staticmethod
    def create(transaction, company_id=DEMO_COMPANY_ID):
        query = """
            INSERT INTO transactions 
            (company_id, date, amount, type, category, description, recurring, recurring_frequency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            transaction['date'],
            transaction['amount'],
            transaction['type'],
            transaction['category'],
            transaction.get('description', ''),
            transaction.get('recurring', False),
            transaction.get('recurring_frequency', 'monthly')
        ))
        _cache.bump(company_id)
        return result

    @staticmethod
    def bulk_create(transactions, company_id=DEMO_COMPANY_ID):
        """Create multiple transactions at once"""
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                for t in transactions:
                    cursor.execute("""
                        INSERT INTO transactions 
                        (company_id, date, amount, type, category, description, recurring)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        company_id,
                        t['date'],
                        t['amount'],
                        t['type'],
                        t['category'],
                        t.get('description', ''),
                        t.get('recurring', False)
                    ))
        _cache.bump(company_id)
        return len(transactions)
    
    @staticmethod
    def delete_all(company_id=DEMO_COMPANY_ID):
        """Delete all transactions (for demo data reload)"""
        query = "DELETE FROM transactions WHERE company_id = %s"
        Database.execute(query, (company_id,), fetch=False)
        _cache.bump(company_id)
    @staticmethod
    def delete(transaction_id: str, company_id: str):
        query = "DELETE FROM transactions WHERE id = %s AND company_id = %s"
        Database.execute(query, (transaction_id, company_id), fetch=False)
        _cache.bump(company_id)

    @staticmethod
    def update(transaction_id: str, transaction, company_id: str):
        """Update a single transaction, scoped to the owning company."""
        query = """
            UPDATE transactions
            SET date = %s, amount = %s, type = %s, category = %s,
                description = %s, recurring = %s, recurring_frequency = %s
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (
            transaction['date'],
            transaction['amount'],
            transaction['type'],
            transaction['category'],
            transaction.get('description', ''),
            transaction.get('recurring', False),
            transaction.get('recurring_frequency', 'monthly'),
            transaction_id,
            company_id,
        ), fetch=False)
        _cache.bump(company_id)

    @staticmethod
    def get_recurring(company_id: str):
        """Get all recurring transactions for a company"""
        query = """
            SELECT * FROM transactions 
            WHERE company_id = %s AND recurring = TRUE
            ORDER BY date DESC
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def exists_today(company_id: str, category: str, description: str, today, amount=None) -> bool:
        """Check if a recurring transaction already exists for today.
        When amount is provided it is part of the match, so two distinct-amount
        recurring transactions in the same category aren't wrongly collapsed."""
        query = """
            SELECT COUNT(*) as count FROM transactions
            WHERE company_id = %s
            AND category = %s
            AND description = %s
            AND DATE(date) = %s
        """
        params = [company_id, category, description, today]
        if amount is not None:
            query += " AND ROUND(amount::numeric, 2) = ROUND(%s::numeric, 2)"
            params.append(amount)
        result = Database.execute_one(query, tuple(params))
        return result['count'] > 0 if result else False
    @staticmethod
    def delete_by_date_and_category(date: str, category: str, company_id: str):
        """Delete all transactions for a specific date and category"""
        query = """
            DELETE FROM transactions
            WHERE company_id = %s
            AND category = %s
            AND DATE(date) = %s::date
        """
        Database.execute(query, (company_id, category, date), fetch=False)
        _cache.bump(company_id)
class CompanyDB:
    """Company database operations"""
    
    @staticmethod
    def get(company_id=DEMO_COMPANY_ID):
        """Get company by ID"""
        query = "SELECT * FROM companies WHERE id = %s"
        return Database.execute_one(query, (company_id,))
    
    @staticmethod
    def get_starting_cash(company_id=DEMO_COMPANY_ID):
        """Get starting cash for a company"""
        company = CompanyDB.get(company_id)
        return float(company['starting_cash']) if company else 100000.0
    
    @staticmethod
    def create(name: str, starting_cash: float = 100000.00):
        """Create a new company"""
        query = """
            INSERT INTO companies (name, starting_cash)
            VALUES (%s, %s)
            RETURNING *
        """
        return Database.execute_one(query, (name, starting_cash))

    # ── Tax profile (entity type + state) ──────────────────────────────
    # Columns are added lazily so a deploy never fails on a schema that has not
    # been migrated yet (same pattern as the Plaid tables).
    _tax_columns_ready = False

    @staticmethod
    def _ensure_tax_columns():
        if CompanyDB._tax_columns_ready:
            return
        try:
            Database.execute(
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS entity_type TEXT",
                fetch=False)
            Database.execute(
                "ALTER TABLE companies ADD COLUMN IF NOT EXISTS state TEXT",
                fetch=False)
            CompanyDB._tax_columns_ready = True
        except Exception:
            # Never block a page render on this.
            pass

    @staticmethod
    def get_tax_profile(company_id: str):
        """Return {'entity_type': ..., 'state': ...}; empty values if unset."""
        CompanyDB._ensure_tax_columns()
        try:
            row = Database.execute_one(
                "SELECT entity_type, state FROM companies WHERE id = %s",
                (company_id,))
            if row:
                return {'entity_type': row.get('entity_type') or '',
                        'state': row.get('state') or ''}
        except Exception:
            pass
        return {'entity_type': '', 'state': ''}

    @staticmethod
    def set_tax_profile(company_id: str, entity_type: str, state: str):
        CompanyDB._ensure_tax_columns()
        try:
            Database.execute(
                "UPDATE companies SET entity_type = %s, state = %s WHERE id = %s",
                (entity_type or None, state or None, company_id), fetch=False)
            return True
        except Exception:
            return False

class UserDB:
    """User database operations"""
    
    @staticmethod
    def get_by_email(email: str):
        """Get user by email"""
        query = "SELECT * FROM users WHERE email = %s"
        return Database.execute_one(query, (email,))
    
    @staticmethod
    def get_by_id(user_id: str):
        """Get user by ID"""
        query = "SELECT * FROM users WHERE id = %s"
        return Database.execute_one(query, (user_id,))

    @staticmethod
    def get_referral_count(referral_code: str) -> int:
        """Number of sign-ups that used this referral code."""
        if not referral_code:
            return 0
        result = Database.execute_one(
            "SELECT COUNT(*) AS c FROM users WHERE LOWER(referral) = LOWER(%s)",
            (referral_code,))
        return int(result['c']) if result and result.get('c') is not None else 0

    @staticmethod
    def get_referral_leaderboard(limit: int = 10):
        """Top referrers, computed from real sign-ups. A user's referral code is the
        first 8 chars of their id; referees store that code in users.referral."""
        query = """
            SELECT u.id, u.full_name, u.email, cnt.referrals
            FROM (
                SELECT LOWER(referral) AS code, COUNT(*) AS referrals
                FROM users
                WHERE referral IS NOT NULL AND referral <> ''
                GROUP BY LOWER(referral)
            ) cnt
            JOIN users u ON LOWER(SUBSTRING(u.id::text FROM 1 FOR 8)) = cnt.code
            ORDER BY cnt.referrals DESC, u.created_at ASC
            LIMIT %s
        """
        results = Database.execute(query, (limit,))
        return [dict(r) for r in results] if results else []
    
    @staticmethod
    def create(email: str, password_hash: str, full_name: str, company_id: str, phone: str = '', referral: str = ''):
        query = """
            INSERT INTO users (email, password_hash, full_name, company_id, phone, referral)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        return Database.execute_one(query, (email, password_hash, full_name, company_id, phone, referral))
    
    @staticmethod
    def update_company(user_id: str, company_id: str):
        """Update user's company"""
        query = "UPDATE users SET company_id = %s WHERE id = %s"
        Database.execute(query, (company_id, user_id), fetch=False)

    @staticmethod
    def get_all():
        """Get all users (admin only). Excludes throwaway demo sandbox accounts,
        which live under the reserved @sandbox.fintoit.com domain."""
        query = """
            SELECT u.id, u.email, u.full_name, u.is_admin, u.is_banned, u.phone,
               u.password_hash, u.created_at, u.plan,
               c.name as company_name
        FROM users u
        LEFT JOIN companies c ON u.company_id = c.id
        WHERE u.email NOT LIKE %s
        ORDER BY u.created_at DESC
        """
        results = Database.execute(query, ('%@sandbox.fintoit.com',))
        return [dict(row) for row in results] if results else []
    
    @staticmethod
    def ban(user_id: str):
        query = "UPDATE users SET is_banned = TRUE WHERE id = %s"
        Database.execute(query, (user_id,), fetch=False)

    @staticmethod
    def unban(user_id: str):
        query = "UPDATE users SET is_banned = FALSE WHERE id = %s"
        Database.execute(query, (user_id,), fetch=False)

    @staticmethod
    def delete(user_id: str):
        query = "DELETE FROM users WHERE id = %s"
        Database.execute(query, (user_id,), fetch=False)

    @staticmethod
    def get_stats():
        """Get platform-wide usage statistics"""
        query = """
            SELECT
                (SELECT COUNT(*) FROM users) as total_users,
                (SELECT COUNT(*) FROM users WHERE is_banned = TRUE) as banned_users,
                (SELECT COUNT(*) FROM users
                WHERE created_at >= NOW() - INTERVAL '7 days') as new_this_week,
                (SELECT COUNT(*) FROM users
                WHERE created_at >= NOW() - INTERVAL '30 days') as new_this_month,
                (SELECT COUNT(*) FROM companies) as total_companies,
                (SELECT COUNT(*) FROM transactions) as total_transactions,
                (SELECT COUNT(*) FROM invoices) as total_invoices
        """
        result = Database.execute_one(query)
        return dict(result) if result else {}

    @staticmethod
    def get_signups_by_day():
        """Get signups per day for the last 30 days"""
        query = """
            SELECT
                DATE(created_at) as day,
                COUNT(*) as signups
            FROM users
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """
        results = Database.execute(query)
        return [dict(r) for r in results] if results else []
    @staticmethod
    def set_reset_token(email: str, token: str, expiry):
        query = """
            UPDATE users SET reset_token = %s, reset_token_expiry = %s
            WHERE email = %s
        """
        Database.execute(query, (token, expiry, email), fetch=False)

    @staticmethod
    def get_by_reset_token(token: str):
        query = "SELECT * FROM users WHERE reset_token = %s"
        result = Database.execute_one(query, (token,))
        return dict(result) if result else None

    @staticmethod
    def clear_reset_token(user_id: str):
        query = """
            UPDATE users SET reset_token = NULL, reset_token_expiry = NULL
            WHERE id = %s
        """
        Database.execute(query, (user_id,), fetch=False)

    @staticmethod
    def update_password(user_id: str, password_hash: str):
        query = "UPDATE users SET password_hash = %s WHERE id = %s"
        Database.execute(query, (password_hash, user_id), fetch=False)

    @staticmethod
    def set_admin(user_id: str, is_admin: bool):
        query = "UPDATE users SET is_admin = %s WHERE id = %s"
        Database.execute(query, (is_admin, user_id), fetch=False)
    @staticmethod
    def save_smtp_settings(user_id: str, settings: dict):
        query = """
            UPDATE users SET
                smtp_host = %s, smtp_port = %s, smtp_username = %s,
                smtp_password = %s, smtp_use_tls = %s, smtp_from_name = %s
            WHERE id = %s
        """
        Database.execute(query, (
            settings.get('smtp_host'),
            settings.get('smtp_port', 587),
            settings.get('smtp_username'),
            settings.get('smtp_password'),
            settings.get('smtp_use_tls', True),
            settings.get('smtp_from_name', ''),
            user_id
        ), fetch=False)

    @staticmethod
    def get_smtp_settings(user_id: str):
        query = """
            SELECT smtp_host, smtp_port, smtp_username, smtp_password,
                smtp_use_tls, smtp_from_name
            FROM users WHERE id = %s
        """
        result = Database.execute_one(query, (user_id,))
        return dict(result) if result else {}
    
    @staticmethod
    def update_profile(user_id: str, full_name: str, email: str, phone: str, company_name: str):
        # Update user
        query = "UPDATE users SET full_name = %s, email = %s, phone = %s WHERE id = %s"
        Database.execute(query, (full_name, email, phone, user_id), fetch=False)

    @staticmethod
    def delete_account(user_id: str, company_id: str):
        # Delete company and user (cascade)
        Database.execute("DELETE FROM transactions WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM invoices WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM bills WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM budgets WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM goals WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM employees WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM vendors WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM cap_table WHERE company_id = %s", (company_id,), fetch=False)
        Database.execute("DELETE FROM users WHERE id = %s", (user_id,), fetch=False)
        Database.execute("DELETE FROM companies WHERE id = %s", (company_id,), fetch=False)

    @staticmethod
    def delete_demo_sandbox(user_id, company_id):
        """Fully remove a per-session demo sandbox: every company-scoped row plus the
        throwaway user and company. Each statement runs on its own connection, so a
        missing table or already-deleted row can never abort the rest of the wipe."""
        company_scoped = [
            "transactions", "invoices", "bills", "budgets", "goals",
            "employees", "vendors", "cap_table", "pay_runs", "headcount_plan",
            "plaid_items",
        ]
        if company_id:
            for table in company_scoped:
                try:
                    Database.execute(
                        "DELETE FROM %s WHERE company_id = %%s" % table,
                        (company_id,), fetch=False)
                except Exception:
                    pass
        if user_id:
            for stmt in ("DELETE FROM chat_history WHERE user_id = %s",
                         "DELETE FROM users WHERE id = %s"):
                try:
                    Database.execute(stmt, (user_id,), fetch=False)
                except Exception:
                    pass
        if company_id:
            try:
                Database.execute("DELETE FROM companies WHERE id = %s",
                                 (company_id,), fetch=False)
            except Exception:
                pass

    @staticmethod
    def get_stale_demo_sandboxes(hours: int = 6):
        """Throwaway demo sandbox users older than `hours` — used by the hourly sweep
        to clean up sessions that were abandoned without logging out."""
        query = """
            SELECT id, company_id
            FROM users
            WHERE email LIKE %s
              AND created_at < NOW() - make_interval(hours => %s)
        """
        try:
            results = Database.execute(query, ('%@sandbox.fintoit.com', hours))
            return [dict(r) for r in results] if results else []
        except Exception:
            return []

    @staticmethod
    def set_stripe_customer(user_id: str, customer_id: str):
        # Match on the primary key (user id). Callers pass current_user.id / user['id'].
        Database.execute(
            "UPDATE users SET stripe_customer_id = %s WHERE id = %s",
            (customer_id, user_id), fetch=False
        )
 
    @staticmethod
    def set_subscription(user_id: str, subscription_id: str, plan: str, expires_at=None):
        Database.execute(
            """UPDATE users
               SET stripe_subscription_id = %s,
                   plan = %s,
                   plan_expires_at = %s
               WHERE id = %s""",
            (subscription_id, plan, expires_at, user_id), fetch=False
        )
 
    @staticmethod
    def set_plan(user_id: str, plan: str):
        Database.execute(
            "UPDATE users SET plan = %s WHERE id = %s",
            (plan, user_id), fetch=False
        )
 
    @staticmethod
    def get_by_stripe_customer(customer_id: str):
        result = Database.execute_one(
            "SELECT * FROM users WHERE stripe_customer_id = %s", (customer_id,)
        )
        return dict(result) if result else None
 
    @staticmethod
    def get_by_stripe_subscription(subscription_id: str):
        result = Database.execute_one(
            "SELECT * FROM users WHERE stripe_subscription_id = %s", (subscription_id,)
        )
        return dict(result) if result else None
    
    @staticmethod
    def update_plan(user_id: str, plan_key: str):
        Database.execute("UPDATE users SET plan = %s WHERE id = %s", (plan_key, user_id), fetch=False)
    
    @staticmethod
    def cancel(coustomer_id: str):
        Database.execute("UPDATE users SET plan = 'free' WHERE stripe_customer_id = %s", (coustomer_id,), fetch=False)
    
    @staticmethod
    def save_avatar(user_id: str, data: bytes, mime_type: str):
        query = "UPDATE users SET avatar_data = %s, avatar_type = %s WHERE id = %s"
        Database.execute(query, (data, mime_type, user_id), fetch=False)

    @staticmethod
    def delete_avatar(user_id: str):
        query = "UPDATE users SET avatar_data = NULL, avatar_type = NULL WHERE id = %s"
        Database.execute(query, (user_id,), fetch=False)
    
    @staticmethod
    def enable_2fa(user_id: str, method: str, totp_secret: str = None):
        query = """
            UPDATE users SET two_factor_enabled = TRUE,
            two_factor_method = %s, totp_secret = %s
            WHERE id = %s
        """
        Database.execute(query, (method, totp_secret, user_id), fetch=False)

    @staticmethod
    def disable_2fa(user_id: str):
        query = """
            UPDATE users SET two_factor_enabled = FALSE,
            two_factor_method = NULL, totp_secret = NULL
            WHERE id = %s
        """
        Database.execute(query, (user_id,), fetch=False)

    @staticmethod
    def save_2fa_code(user_id: str, code: str, expiry):
        query = """
            UPDATE users SET two_factor_code = %s,
            two_factor_code_expiry = %s WHERE id = %s
        """
        Database.execute(query, (code, expiry, user_id), fetch=False)

    @staticmethod
    def clear_2fa_code(user_id: str):
        query = """
            UPDATE users SET two_factor_code = NULL,
            two_factor_code_expiry = NULL WHERE id = %s
        """
        Database.execute(query, (user_id,), fetch=False)

class BudgetDB:
    """Budget database operations"""

    @staticmethod
    def get_all(company_id: str):
        query = "SELECT * FROM budgets WHERE company_id = %s ORDER BY start_date DESC"
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def create(budget: dict, company_id: str):
        query = """
            INSERT INTO budgets (company_id, name, category, budget_type, amount, 
                                start_date, end_date, department, owner)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id, budget['name'], budget.get('category', ''),
            budget.get('budget_type', 'category'), budget['amount'],
            budget['start_date'], budget['end_date'],
            budget.get('department', ''), budget.get('owner', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def delete(budget_id: str, company_id: str):
        query = "DELETE FROM budgets WHERE id = %s AND company_id = %s"
        Database.execute(query, (budget_id, company_id), fetch=False)

    @staticmethod
    def get_with_actuals(company_id: str):
        """Get all budgets with actual spending calculated from transactions"""
        query = """
            SELECT 
                b.*,
                COALESCE(SUM(
                    CASE 
                        WHEN t.type = 'expense'
                        AND t.date BETWEEN b.start_date AND b.end_date
                        AND (b.budget_type = 'overall' OR LOWER(t.category) = LOWER(b.category))
                        THEN t.amount 
                        ELSE 0 
                    END
                ), 0) AS actual_spent
            FROM budgets b
            LEFT JOIN transactions t ON t.company_id = b.company_id
            WHERE b.company_id = %s
            GROUP BY b.id
            ORDER BY b.start_date DESC
        """
        results = Database.execute(query, (company_id,))
        budgets = []
        for r in (results or []):
            b = dict(r)
            b['amount'] = float(b['amount'])
            b['actual_spent'] = float(b['actual_spent'])
            b['remaining'] = b['amount'] - b['actual_spent']
            b['percent_used'] = round((b['actual_spent'] / b['amount']) * 100, 1) if b['amount'] > 0 else 0
            budgets.append(b)
        return budgets
    
    @staticmethod
    def get_by_department(company_id: str):
        query = """
            SELECT * FROM budgets 
            WHERE company_id = %s 
            ORDER BY department, name
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []
    
    @staticmethod
    def get_alerts(company_id: str, threshold: float = 0.8) -> List[Dict]:
        """Return budgets that are over threshold % spent"""
        budgets = BudgetDB.get_with_actuals(company_id)
        alerts = []
        for b in budgets:
            amount = float(b.get('amount', 0))
            actual = float(b.get('actual_spent', 0) or 0)
            if amount > 0:
                pct = actual / amount
                if pct >= threshold:
                    alerts.append({
                        **b,
                        'pct_used': round(pct * 100, 1),
                        'remaining': round(amount - actual, 2),
                        'severity': 'critical' if pct >= 1.0 else 'warning'
                    })
        return sorted(alerts, key=lambda x: x['pct_used'], reverse=True)

class GoalDB:
    """Goal database operations"""

    @staticmethod
    def get_all(company_id: str):
        query = "SELECT * FROM goals WHERE company_id = %s ORDER BY created_at DESC"
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def create(goal: dict, company_id: str):
        query = """
            INSERT INTO goals (company_id, name, goal_type, target_value, deadline)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            goal['name'],
            goal['goal_type'],
            goal['target_value'],
            goal.get('deadline') or None
        ))
        return dict(result) if result else {}

    @staticmethod
    def delete(goal_id: str, company_id: str):
        query = "DELETE FROM goals WHERE id = %s AND company_id = %s"
        Database.execute(query, (goal_id, company_id), fetch=False)

class VendorDB:
    """Vendor database operations"""

    @staticmethod
    def get_all(company_id: str):
        query = """
            SELECT v.*,
                COALESCE(SUM(b.total_amount), 0) as total_spent
            FROM vendors v
            LEFT JOIN bills b ON LOWER(b.vendor_name) = LOWER(v.name)
                AND b.company_id = v.company_id
            WHERE v.company_id = %s AND v.is_active = TRUE
            GROUP BY v.id
            ORDER BY total_spent DESC
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def get_by_id(vendor_id: str, company_id: str):
        query = "SELECT * FROM vendors WHERE id = %s AND company_id = %s"
        result = Database.execute_one(query, (vendor_id, company_id))
        return dict(result) if result else {}

    @staticmethod
    def create(vendor: dict, company_id: str):
        query = """
            INSERT INTO vendors
            (company_id, name, category, contact_name, contact_email,
             contact_phone, website, payment_terms, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            vendor['name'],
            vendor.get('category', ''),
            vendor.get('contact_name', ''),
            vendor.get('contact_email', ''),
            vendor.get('contact_phone', ''),
            vendor.get('website', ''),
            vendor.get('payment_terms', 'Net 30'),
            vendor.get('notes', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def update(vendor_id: str, vendor: dict, company_id: str):
        query = """
            UPDATE vendors SET
                name = %s, category = %s, contact_name = %s,
                contact_email = %s, contact_phone = %s,
                website = %s, payment_terms = %s, notes = %s
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (
            vendor['name'],
            vendor.get('category', ''),
            vendor.get('contact_name', ''),
            vendor.get('contact_email', ''),
            vendor.get('contact_phone', ''),
            vendor.get('website', ''),
            vendor.get('payment_terms', 'Net 30'),
            vendor.get('notes', ''),
            vendor_id, company_id
        ), fetch=False)

    @staticmethod
    def delete(vendor_id: str, company_id: str):
        query = "UPDATE vendors SET is_active = FALSE WHERE id = %s AND company_id = %s"
        Database.execute(query, (vendor_id, company_id), fetch=False)

class EmployeeDB:
    """Employee database operations"""

    @staticmethod
    def save_contract(employee_id: str, company_id: str, data: bytes, name: str, mime_type: str):
        query = """
            UPDATE employees SET contract_data = %s, contract_name = %s, contract_type = %s
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (data, name, mime_type, employee_id, company_id), fetch=False)

    @staticmethod
    def save_resume(employee_id: str, company_id: str, data: bytes, name: str, mime_type: str):
        query = """
            UPDATE employees SET resume_data = %s, resume_name = %s, resume_type = %s
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (data, name, mime_type, employee_id, company_id), fetch=False)

    @staticmethod
    def get_full(employee_id: str, company_id: str):
        query = "SELECT * FROM employees WHERE id = %s AND company_id = %s"
        result = Database.execute_one(query, (employee_id, company_id))
        return dict(result) if result else {}
    
    @staticmethod
    def save_signatures(employee_id: str, company_id: str, company_sig: str):
        import secrets
        token = secrets.token_urlsafe(32)
        query = """
            UPDATE employees SET
                company_signature = %s,
                company_signed_at = NOW(),
                contract_status = 'company_signed',
                contract_token = %s
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (company_sig, token, employee_id, company_id), fetch=False)
        return token

    @staticmethod
    def get_by_contract_token(token: str):
        query = "SELECT * FROM employees WHERE contract_token = %s"
        result = Database.execute_one(query, (token,))
        return dict(result) if result else {}

    @staticmethod
    def save_employee_signature(employee_id: str, employee_sig: str, email: str):
        query = """
            UPDATE employees SET
                employee_signature = %s,
                employee_signed_at = NOW(),
                employee_email = %s,
                contract_status = 'fully_signed'
            WHERE id = %s
        """
        Database.execute(query, (employee_sig, email, employee_id), fetch=False)

    @staticmethod
    def save_employee_email(employee_id: str, company_id: str, email: str):
        query = "UPDATE employees SET employee_email = %s WHERE id = %s AND company_id = %s"
        Database.execute(query, (email, employee_id, company_id), fetch=False)
    
    @staticmethod
    def get_all(company_id: str):
        query = """
            SELECT * FROM employees
            WHERE company_id = %s AND is_active = TRUE
            ORDER BY department, name
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def get_by_id(employee_id: str, company_id: str):
        query = "SELECT * FROM employees WHERE id = %s AND company_id = %s"
        result = Database.execute_one(query, (employee_id, company_id))
        return dict(result) if result else {}

    @staticmethod
    def create(employee: dict, company_id: str):
        query = """
            INSERT INTO employees
            (company_id, name, title, department, employment_type,
             salary, pay_frequency, start_date, equity_percent, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            employee['name'],
            employee.get('title', ''),
            employee.get('department', ''),
            employee.get('employment_type', 'full_time'),
            employee['salary'],
            employee.get('pay_frequency', 'monthly'),
            employee.get('start_date') or None,
            employee.get('equity_percent', 0),
            employee.get('notes', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def update(employee_id: str, employee: dict, company_id: str):
        query = """
            UPDATE employees SET
                name=%s, title=%s, department=%s, employment_type=%s,
                salary=%s, pay_frequency=%s, start_date=%s,
                equity_percent=%s, notes=%s
            WHERE id=%s AND company_id=%s
        """
        Database.execute(query, (
            employee['name'],
            employee.get('title', ''),
            employee.get('department', ''),
            employee.get('employment_type', 'full_time'),
            employee['salary'],
            employee.get('pay_frequency', 'monthly'),
            employee.get('start_date') or None,
            employee.get('equity_percent', 0),
            employee.get('notes', ''),
            employee_id, company_id
        ), fetch=False)

    @staticmethod
    def delete(employee_id: str, company_id: str):
        query = "UPDATE employees SET is_active = FALSE WHERE id = %s AND company_id = %s"
        Database.execute(query, (employee_id, company_id), fetch=False)

    @staticmethod
    def get_summary(company_id: str):
        query = """
            SELECT
                COUNT(*) as headcount,
                SUM(salary) as total_monthly_salary,
                COUNT(CASE WHEN employment_type = 'full_time' THEN 1 END) as full_time,
                COUNT(CASE WHEN employment_type = 'contractor' THEN 1 END) as contractors
            FROM employees
            WHERE company_id = %s AND is_active = TRUE
        """
        result = Database.execute_one(query, (company_id,))
        return dict(result) if result else {}


class PayRunDB:
    """Pay run database operations"""

    @staticmethod
    def get_all(company_id: str):
        query = """
            SELECT * FROM pay_runs
            WHERE company_id = %s
            ORDER BY run_date DESC
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def create(pay_run: dict, company_id: str):
        query = """
            INSERT INTO pay_runs
            (company_id, run_date, period_start, period_end,
             total_gross, total_tax, total_net, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            pay_run['run_date'],
            pay_run['period_start'],
            pay_run['period_end'],
            pay_run['total_gross'],
            pay_run.get('total_tax', 0),
            pay_run['total_net'],
            pay_run.get('notes', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def delete(pay_run_id: str, company_id: str):
        query = "DELETE FROM pay_runs WHERE id = %s AND company_id = %s"
        Database.execute(query, (pay_run_id, company_id), fetch=False)
    
    @staticmethod
    def get_by_id(run_id: str, company_id: str):
        query = "SELECT * FROM pay_runs WHERE id = %s AND company_id = %s"
        result = Database.execute_one(query, (run_id, company_id))
        return dict(result) if result else {}

class CapTableDB:
    """Cap table database operations"""

    @staticmethod
    def get_all(company_id: str):
        query = """
            SELECT * FROM cap_table
            WHERE company_id = %s
            ORDER BY shares DESC
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def get_by_id(entry_id: str, company_id: str):
        query = "SELECT * FROM cap_table WHERE id = %s AND company_id = %s"
        result = Database.execute_one(query, (entry_id, company_id))
        return dict(result) if result else {}

    @staticmethod
    def create(entry: dict, company_id: str):
        query = """
            INSERT INTO cap_table
            (company_id, shareholder_name, shareholder_type, share_class,
             shares, price_per_share, investment_amount, grant_date,
             vesting_schedule, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            entry['shareholder_name'],
            entry.get('shareholder_type', 'founder'),
            entry.get('share_class', 'common'),
            entry['shares'],
            entry.get('price_per_share', 0),
            entry.get('investment_amount', 0),
            entry.get('grant_date') or None,
            entry.get('vesting_schedule', ''),
            entry.get('notes', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def update(entry_id: str, entry: dict, company_id: str):
        query = """
            UPDATE cap_table SET
                shareholder_name=%s, shareholder_type=%s, share_class=%s,
                shares=%s, price_per_share=%s, investment_amount=%s,
                grant_date=%s, vesting_schedule=%s, notes=%s
            WHERE id=%s AND company_id=%s
        """
        Database.execute(query, (
            entry['shareholder_name'],
            entry.get('shareholder_type', 'founder'),
            entry.get('share_class', 'common'),
            entry['shares'],
            entry.get('price_per_share', 0),
            entry.get('investment_amount', 0),
            entry.get('grant_date') or None,
            entry.get('vesting_schedule', ''),
            entry.get('notes', ''),
            entry_id, company_id
        ), fetch=False)

    @staticmethod
    def delete(entry_id: str, company_id: str):
        query = "DELETE FROM cap_table WHERE id = %s AND company_id = %s"
        Database.execute(query, (entry_id, company_id), fetch=False)

    @staticmethod
    def get_summary(company_id: str):
        """Calculate total shares, ownership percentages and valuations"""
        entries = CapTableDB.get_all(company_id)
        if not entries:
            return {'entries': [], 'total_shares': 0, 'total_raised': 0}

        total_shares = sum(float(e['shares']) for e in entries)
        total_raised = sum(float(e['investment_amount']) for e in entries)

        for e in entries:
            e['shares'] = float(e['shares'])
            e['price_per_share'] = float(e['price_per_share'] or 0)
            e['investment_amount'] = float(e['investment_amount'] or 0)
            e['ownership_pct'] = round((e['shares'] / total_shares * 100), 2) if total_shares > 0 else 0

        return {
            'entries': entries,
            'total_shares': total_shares,
            'total_raised': total_raised
        }

class FeedbackDB:
    """Feedback database operations"""

    @staticmethod
    def create(feedback: dict):
        query = """
            INSERT INTO feedback (name, email, type, message)
            VALUES (%s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            feedback.get('name', ''),
            feedback.get('email', ''),
            feedback.get('type', 'general'),
            feedback['message']
        ))
        return dict(result) if result else {}

    @staticmethod
    def get_all():
        query = "SELECT * FROM feedback ORDER BY created_at DESC"
        results = Database.execute(query)
        return [dict(r) for r in results] if results else []

    @staticmethod
    def update_status(feedback_id: str, status: str):
        query = "UPDATE feedback SET status = %s WHERE id = %s"
        Database.execute(query, (status, feedback_id), fetch=False)

    @staticmethod
    def delete(feedback_id: str):
        query = "DELETE FROM feedback WHERE id = %s"
        Database.execute(query, (feedback_id,), fetch=False)


class ContactDB:
    """Contact message database operations"""

    @staticmethod
    def create(message: dict):
        query = """
            INSERT INTO contact_messages (name, email, subject, message)
            VALUES (%s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            message['name'],
            message['email'],
            message.get('subject', ''),
            message['message']
        ))
        return dict(result) if result else {}

    @staticmethod
    def get_all():
        query = "SELECT * FROM contact_messages ORDER BY created_at DESC"
        results = Database.execute(query)
        return [dict(r) for r in results] if results else []

    @staticmethod
    def update_status(message_id: str, status: str):
        query = "UPDATE contact_messages SET status = %s WHERE id = %s"
        Database.execute(query, (status, message_id), fetch=False)

    @staticmethod
    def delete(message_id: str):
        query = "DELETE FROM contact_messages WHERE id = %s"
        Database.execute(query, (message_id,), fetch=False)

class JobApplicationDB:
    """Job application database operations"""

    @staticmethod
    def create(app: dict, resume_data=None, resume_name=None, resume_type=None,
               extra_data=None, extra_name=None, extra_type=None):
        query = """
            INSERT INTO job_applications
            (role, full_name, email, phone, location, linkedin_url, portfolio_url,
             years_experience, why_Fintoit, biggest_achievement, availability,
             resume_data, resume_name, resume_type,
             extra_doc_data, extra_doc_name, extra_doc_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            app['role'], app['full_name'], app['email'],
            app.get('phone', ''), app.get('location', ''),
            app.get('linkedin_url', ''), app.get('portfolio_url', ''),
            app.get('years_experience', ''), app.get('why_Fintoit', ''),
            app.get('biggest_achievement', ''), app.get('availability', ''),
            resume_data, resume_name, resume_type,
            extra_data, extra_name, extra_type
        ))
        return dict(result) if result else {}

    @staticmethod
    def get_all():
        query = "SELECT id, role, full_name, email, phone, location, linkedin_url, portfolio_url, years_experience, why_Fintoit, biggest_achievement, availability, resume_name, resume_type, extra_doc_name, extra_doc_type, status, notes, created_at FROM job_applications ORDER BY created_at DESC"
        results = Database.execute(query)
        return [dict(r) for r in results] if results else []

    @staticmethod
    def get_by_id(app_id: str):
        query = "SELECT * FROM job_applications WHERE id = %s"
        result = Database.execute_one(query, (app_id,))
        return dict(result) if result else {}

    @staticmethod
    def update_status(app_id: str, status: str, notes: str = None):
        query = "UPDATE job_applications SET status = %s, notes = %s WHERE id = %s"
        Database.execute(query, (status, notes, app_id), fetch=False)

    @staticmethod
    def delete(app_id: str):
        query = "DELETE FROM job_applications WHERE id = %s"
        Database.execute(query, (app_id,), fetch=False)

class OutreachDB:
    """Outreach tracking database operations"""

    @staticmethod
    def get_all():
        query = "SELECT * FROM outreach ORDER BY created_at DESC"
        results = Database.execute(query)
        return [dict(r) for r in results] if results else []

    @staticmethod
    def get_by_id(outreach_id: str):
        query = "SELECT * FROM outreach WHERE id = %s"
        result = Database.execute_one(query, (outreach_id,))
        return dict(result) if result else {}

    @staticmethod
    def create(data: dict):
        query = """
            INSERT INTO outreach (name, email, company, role, type, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            data['name'], data['email'],
            data.get('company', ''), data.get('role', ''),
            data.get('type', 'investor'), data.get('status', 'not_contacted'),
            data.get('notes', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def update_status(outreach_id: str, status: str, notes: str = None):
        query = """
            UPDATE outreach SET status = %s, notes = %s,
            last_contacted_at = CASE WHEN %s = 'contacted' THEN NOW() ELSE last_contacted_at END
            WHERE id = %s
        """
        Database.execute(query, (status, notes, status, outreach_id), fetch=False)

    @staticmethod
    def delete(outreach_id: str):
        query = "DELETE FROM outreach WHERE id = %s"
        Database.execute(query, (outreach_id,), fetch=False)

    @staticmethod
    def delete_all():
        Database.execute("DELETE FROM outreach", fetch=False)

    @staticmethod
    def get_summary():
        query = """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'not_contacted') as not_contacted,
                COUNT(*) FILTER (WHERE status = 'contacted') as contacted,
                COUNT(*) FILTER (WHERE status = 'replied') as replied,
                COUNT(*) FILTER (WHERE status = 'meeting') as meeting,
                COUNT(*) FILTER (WHERE status IN ('bounced', 'passed')) as bounced
            FROM outreach
        """
        result = Database.execute_one(query)
        return dict(result) if result else {}

class HeadcountDB:
    @staticmethod
    def get_all(company_id: str):
        query = "SELECT * FROM headcount_plan WHERE company_id = %s ORDER BY start_month"
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def create(plan: dict, company_id: str):
        query = """
            INSERT INTO headcount_plan (company_id, role, department, start_month, salary, status, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """
        result = Database.execute_one(query, (
            company_id, plan['role'], plan.get('department', ''),
            plan.get('start_month'), float(plan.get('salary', 0)),
            plan.get('status', 'planned'), plan.get('notes', '')
        ))
        return dict(result) if result else {}

    @staticmethod
    def delete(plan_id: str, company_id: str):
        Database.execute("DELETE FROM headcount_plan WHERE id = %s AND company_id = %s",
                        (plan_id, company_id), fetch=False)

    @staticmethod
    def update_status(plan_id: str, status: str, company_id: str):
        Database.execute("UPDATE headcount_plan SET status = %s WHERE id = %s AND company_id = %s",
                        (status, plan_id, company_id), fetch=False)

class ChatHistoryDB:
    """Handles storing and fetching chat history for users"""

    @staticmethod
    def add_message(user_id, role, content):
        """Add a message to the chat history"""
        query = """
        INSERT INTO chat_history (user_id, role, content, created_at)
        VALUES (%s, %s, %s, NOW())
        """
        Database.execute(query, (user_id, role, content), fetch=False)

    @staticmethod
    def get_recent_messages(user_id, days=7):
        """Get chat messages for a user within the last `days` days"""
        query = """
        SELECT role, content
        FROM chat_history
        WHERE user_id = %s AND created_at >= NOW() - make_interval(days => %s)
        ORDER BY created_at ASC
        """
        return Database.execute(query, (user_id, days))
    
class NewsletterDB:
    """Newsletter subscribers"""

    @staticmethod
    def subscribe(email: str):
        query = """
            INSERT INTO newsletter_subscribers (email)
            VALUES (%s)
            ON CONFLICT (email) DO NOTHING
            RETURNING *
        """
        result = Database.execute_one(query, (email,))
        return dict(result) if result else None

    @staticmethod
    def exists(email: str) -> bool:
        query = "SELECT id FROM newsletter_subscribers WHERE email = %s"
        result = Database.execute_one(query, (email,))
        return result is not None

    @staticmethod
    def get_all():
        query = """
            SELECT * FROM newsletter_subscribers
            ORDER BY created_at DESC
        """
        results = Database.execute(query)
        return [dict(r) for r in results] if results else []

    @staticmethod
    def delete(email: str):
        query = "DELETE FROM newsletter_subscribers WHERE email = %s"
        Database.execute(query, (email,), fetch=False)