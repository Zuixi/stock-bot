"""Unit tests for cryptographic functions: Argon2id password hashing and tokens."""

from app.core.crypto import (
    generate_csrf_token,
    generate_family_id,
    generate_session_id,
    hash_password,
    hash_token,
    verify_and_update_password,
    verify_csrf_token,
    verify_password,
)


def test_argon2id_hash_and_verify() -> None:
    """Test Argon2id password hashing and verification."""
    password = "MySecurePassword123!"
    hashed = hash_password(password)

    assert hashed.startswith("$argon2id$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False
    assert verify_password("", hashed) is False


def test_verify_and_update_password() -> None:
    """Test verify_and_update_password helper."""
    password = "SecretPassword456$"
    hashed = hash_password(password)

    is_valid, new_hash = verify_and_update_password(password, hashed)
    assert is_valid is True
    # If parameters haven't changed, new_hash is None
    assert new_hash is None or isinstance(new_hash, str)

    is_invalid, _ = verify_and_update_password("WrongPass", hashed)
    assert is_invalid is False


def test_token_generators() -> None:
    """Test session, CSRF, and family token format and uniqueness."""
    sess_1 = generate_session_id()
    sess_2 = generate_session_id()
    assert sess_1.startswith("sess_")
    assert sess_2.startswith("sess_")
    assert sess_1 != sess_2

    csrf_1 = generate_csrf_token()
    csrf_2 = generate_csrf_token()
    assert csrf_1.startswith("c_")
    assert csrf_2.startswith("c_")
    assert csrf_1 != csrf_2

    fam_1 = generate_family_id()
    fam_2 = generate_family_id()
    assert fam_1.startswith("fam_")
    assert fam_1 != fam_2


def test_csrf_token_constant_time_verification() -> None:
    """Test CSRF token validation against timing attacks."""
    token = generate_csrf_token()
    assert verify_csrf_token(token, token) is True
    assert verify_csrf_token(token, "c_invalidtoken123") is False
    assert verify_csrf_token(token, "") is False
    assert verify_csrf_token("", token) is False


def test_hash_token_sha256() -> None:
    """Test SHA-256 token hashing."""
    token = "sess_1234567890abcdef"
    digest = hash_token(token)
    assert len(digest) == 64
    assert digest == hash_token(token)
