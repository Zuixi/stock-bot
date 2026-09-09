"""JWKS (JSON Web Key Set) endpoint for public key distribution."""

from typing import Any

from fastapi import APIRouter

from app.core.jwt_signer import key_manager

router = APIRouter(tags=["JWKS"])


@router.get("/.well-known/jwks.json", summary="Standard RFC 7517 JWKS distribution")
@router.get("/auth/jwks.json", summary="Auth JWKS distribution alias")
@router.get("/jwks.json", summary="Root JWKS distribution alias")
async def get_jwks() -> dict[str, list[dict[str, Any]]]:
    """Expose public key set for API Gateway and downstream microservices to verify assertions."""
    return key_manager.get_jwks()
