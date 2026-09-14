"""
Authentication utilities for Fintoit
"""

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from Modules.database import UserDB, CompanyDB


class User(UserMixin):
    """User model for Flask-Login"""
    
    def __init__(self, user_id, email, full_name=None, company_id=None, is_admin=False):
        self.id = user_id
        self.email = email
        self.full_name = full_name
        self.company_id = company_id
        self.is_admin = is_admin
        try:
            from database import UserDB
            smtp = UserDB.get_smtp_settings(user_id)
            self.smtp_configured = bool(smtp.get('smtp_host') and smtp.get('smtp_username') and smtp.get('smtp_password'))
        except:
            self.smtp_configured = False
    @staticmethod
    def get(user_id):
        user_data = UserDB.get_by_id(user_id)
        if user_data:
            return User(
                user_data['id'],
                user_data['email'],
                user_data.get('full_name'),
                user_data.get('company_id'),
                user_data.get('is_admin', False)  # ← must be False by default
            )
        return None

    @staticmethod
    def create_user(email, password, full_name, company_name, starting_cash=0.0, phone='', referral_code='', password_hash=None):
        # Accept a pre-computed hash so callers never need to hold the plaintext
        # password (e.g. signup verification, which must not stash it in the session).
        if password_hash is None:
            password_hash = generate_password_hash(password)
        company = CompanyDB.create(company_name, starting_cash)
        user_data = UserDB.create(email, password_hash, full_name, company['id'], phone, referral_code)
        return User(
            user_data['id'],
            user_data['email'],
            user_data.get('full_name'),
            user_data.get('company_id'),
            user_data.get('is_admin', False)
        )
    
    @staticmethod
    def authenticate(email, password):
        user_data = UserDB.get_by_email(email)
        if user_data and check_password_hash(user_data['password_hash'], password):
            if user_data.get('is_banned'):
                return None  # blocked
            return User(
                user_data['id'],
                user_data['email'],
                user_data.get('full_name'),
                user_data.get('company_id'),
                user_data.get('is_admin', False)
            )
        return (None)