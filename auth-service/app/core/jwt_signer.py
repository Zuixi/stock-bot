"""RSA key management, JWKS generation, and Principal Assertion token signing."""

import base64
import secrets
import time
from typing import Any

import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings


def _int_to_base64url(val: int) -> str:
    """Encode an integer into big-endian base64url string without padding."""
    num_bytes = (val.bit_length() + 7) // 8
    byte_array = val.to_bytes(num_bytes, "big")
    return base64.urlsafe_b64encode(byte_array).decode("utf-8").rstrip("=")


class KeyManager:
    """Manages RSA asymmetric key pairs and JWKS generation."""

    def __init__(self) -> None:
        self.kid = settings.jwt_kid
        self.algorithm = settings.jwt_algorithm
        self._private_key: rsa.RSAPrivateKey
        self._public_key: rsa.RSAPublicKey

        if settings.jwt_private_key_pem:
            loaded_key = serialization.load_pem_private_key(
                settings.jwt_private_key_pem.encode("utf-8"),
                password=None,
                backend=default_backend(),
            )
            if not isinstance(loaded_key, rsa.RSAPrivateKey):
                raise ValueError("Configured private key is not an RSA private key")
            self._private_key = loaded_key
            self._public_key = self._private_key.public_key()
        else:
            # Generate 2048-bit RSA key pair dynamically
            self._private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend(),
            )
            self._public_key = self._private_key.public_key()

    @property
    def private_key(self) -> rsa.RSAPrivateKey:
        return self._private_key

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        return self._public_key

    def get_private_key_pem(self) -> str:
        """Export private key as PKCS#8 PEM string."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

    def get_public_key_pem(self) -> str:
        """Export public key as SubjectPublicKeyInfo PEM string."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def get_jwks(self) -> dict[str, list[dict[str, str]]]:
        """Export public key set in standard RFC 7517 JWKS format."""
        public_numbers = self._public_key.public_numbers()
        n_str = _int_to_base64url(public_numbers.n)
        e_str = _int_to_base64url(public_numbers.e)

        jwk_entry = {
            "kty": "RSA",
            "use": "sig",
            "alg": self.algorithm,
            "kid": self.kid,
            "n": n_str,
            "e": e_str,
        }
        return {"keys": [jwk_entry]}

    def sign_assertion(
        self,
        user_id: str,
        username: str,
        roles: list[str],
        permissions: list[str],
        session_id: str,
        ttl: int | None = None,
        custom_claims: dict[str, Any] | None = None,
    ) -> str:
        """Sign a short-lived Principal Assertion JWT with RS256."""
        now = int(time.time())
        expiry_seconds = ttl if ttl is not None else settings.assertion_ttl
        jti = f"ast_{secrets.token_hex(16)}"

        payload: dict[str, Any] = {
            "iss": settings.jwt_issuer,
            "sub": user_id,
            "aud": settings.jwt_audience,
            "session_id": session_id,
            "username": username,
            "roles": roles,
            "permissions": permissions,
            "iat": now,
            "exp": now + expiry_seconds,
            "jti": jti,
        }
        if custom_claims:
            payload.update(custom_claims)

        token = jwt.encode(
            payload,
            self._private_key,
            algorithm=self.algorithm,
            headers={"kid": self.kid, "typ": "JWT"},
        )
        return token

    def verify_assertion(
        self,
        token: str,
        leeway: int = 5,
    ) -> dict[str, Any]:
        """Verify and decode a Principal Assertion JWT."""
        return jwt.decode(
            token,
            self._public_key,
            algorithms=[self.algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway=leeway,
        )


# Singleton instance
key_manager = KeyManager()
