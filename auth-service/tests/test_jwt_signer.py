"""Unit tests for RSA KeyManager, JWKS generation, and Principal Assertion signing."""

import jwt
import pytest

from app.core.jwt_signer import KeyManager, key_manager


def test_key_manager_initialization() -> None:
    """Test RSA KeyManager generates valid private/public keys and PEM strings."""
    km = KeyManager()
    assert km.private_key is not None
    assert km.public_key is not None

    priv_pem = km.get_private_key_pem()
    pub_pem = km.get_public_key_pem()

    assert "BEGIN PRIVATE KEY" in priv_pem
    assert "BEGIN PUBLIC KEY" in pub_pem


def test_jwks_format() -> None:
    """Test JWKS matches RFC 7517 specification."""
    jwks = key_manager.get_jwks()
    assert "keys" in jwks
    assert len(jwks["keys"]) >= 1

    key_entry = jwks["keys"][0]
    assert key_entry["kty"] == "RSA"
    assert key_entry["use"] == "sig"
    assert key_entry["alg"] == "RS256"
    assert "kid" in key_entry
    assert "n" in key_entry
    assert "e" in key_entry


def test_principal_assertion_sign_and_verify() -> None:
    """Test signing and verifying Principal Assertion JWT."""
    user_id = "usr_9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    username = "trader_jack"
    roles = ["trader", "researcher"]
    permissions = ["stocks:read", "watchlists:write"]
    session_id = "sess_test123"

    token = key_manager.sign_assertion(
        user_id=user_id,
        username=username,
        roles=roles,
        permissions=permissions,
        session_id=session_id,
        ttl=60,
    )

    assert isinstance(token, str)
    assert len(token) > 50

    # Verify token
    payload = key_manager.verify_assertion(token)
    assert payload["sub"] == user_id
    assert payload["username"] == username
    assert payload["roles"] == roles
    assert payload["permissions"] == permissions
    assert payload["session_id"] == session_id
    assert payload["iss"] == "stock-auth-service"
    assert payload["aud"] == "stock-api"
    assert payload["exp"] > payload["iat"]
    assert payload["jti"].startswith("ast_")


def test_assertion_expiry() -> None:
    """Test that expired assertion token fails verification."""
    km = KeyManager()
    # Sign token with ttl = -5 seconds (already expired)
    token = km.sign_assertion(
        user_id="usr_expired",
        username="expired_user",
        roles=["viewer"],
        permissions=["stocks:read"],
        session_id="sess_expired",
        ttl=-10,
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        km.verify_assertion(token, leeway=0)
