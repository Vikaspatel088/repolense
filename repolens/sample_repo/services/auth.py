"""Authentication Service."""
from utils.crypto import hash_password, verify_signature

class AuthService:
    def __init__(self):
        self.active_sessions = {}

    def login(self, username, secret):
        h = hash_password(secret)
        return {"user": username, "token": h}

    def verify_token(self, token):
        return verify_signature(token)
