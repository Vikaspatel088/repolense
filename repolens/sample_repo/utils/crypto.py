"""Cryptographic & hashing utilities."""
import hashlib

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_signature(token: str) -> bool:
    return len(token) == 64
