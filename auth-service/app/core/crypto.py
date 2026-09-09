"""Cryptographic utilities: Argon2id password hashing, session/CSRF token generators."""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings

# Initialize Argon2id password hasher with specified parameters
argon2_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
    hash_len=settings.argon2_hash_len,
    salt_len=settings.argon2_salt_len,
    type=Type.ID,
)


def hash_password(plain_password: str) -> str:
    """Hash a plain text password using Argon2id."""
    return argon2_hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against an Argon2id hash."""
    try:
        return argon2_hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        return False


def verify_and_update_password(
    plain_password: str,
    hashed_password: str,
) -> tuple[bool, str | None]:
    """Verify password and check if rehashing is needed due to parameter updates."""
    try:
        is_valid = argon2_hasher.verify(hashed_password, plain_password)
        if not is_valid:
            return False, None
        if argon2_hasher.check_needs_rehash(hashed_password):
            new_hash = argon2_hasher.hash(plain_password)
            return True, new_hash
        return True, None
    except (VerifyMismatchError, InvalidHashError):
        return False, None
    except Exception:
        return False, None


def generate_session_id() -> str:
    """Generate a high-entropy opaque session identifier."""
    return f"sess_{secrets.token_hex(24)}"


def generate_csrf_token() -> str:
    """Generate a high-entropy CSRF protection token."""
    return f"c_{secrets.token_hex(24)}"


def generate_family_id() -> str:
    """Generate a refresh token family ID."""
    return f"fam_{secrets.token_hex(16)}"


def hash_token(token: str) -> str:
    """Compute SHA-256 digest of a token for secure indexing."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_csrf_token(stored_csrf_token: str, supplied_csrf_token: str) -> bool:
    """Compare CSRF tokens using constant-time comparison to prevent timing attacks."""
    if not stored_csrf_token or not supplied_csrf_token:
        return False
    return hmac.compare_digest(stored_csrf_token, supplied_csrf_token)
