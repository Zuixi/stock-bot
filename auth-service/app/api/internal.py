"""Internal microservice API endpoints for API Gateway."""

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis

from app.core.jwt_signer import key_manager
from app.core.redis import get_redis
from app.schemas.internal import (
    PrincipalAssertionRequest,
    PrincipalAssertionResponse,
    SessionIntrospectRequest,
    SessionIntrospectResponse,
)
from app.services.session_service import SessionService

router = APIRouter(prefix="/internal", tags=["Internal"])


@router.post(
    "/session/introspect",
    response_model=SessionIntrospectResponse,
    summary="Introspect session state and validate CSRF token",
)
async def introspect_session(
    req: SessionIntrospectRequest,
    redis: Redis = Depends(get_redis),
) -> SessionIntrospectResponse:
    """Gateway calls this to resolve opaque Session Cookie to User principal and verify CSRF."""
    session_service = SessionService(redis)
    result = await session_service.introspect_session(
        session_id=req.session_id,
        csrf_token=req.csrf_token,
    )
    return SessionIntrospectResponse(**result)


@router.post(
    "/principal/assertion",
    response_model=PrincipalAssertionResponse,
    summary="Sign short-lived Principal Assertion JWT for downstream service access",
)
async def create_principal_assertion(
    req: PrincipalAssertionRequest,
    redis: Redis = Depends(get_redis),
) -> PrincipalAssertionResponse:
    """Sign an RS256 Principal Assertion token for an active session."""
    session_service = SessionService(redis)
    session_data = await session_service.get_session(req.session_id, extend_ttl=True)

    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "会话不存在或已过期"},
        )

    assertion_ttl = req.ttl or 60
    token = key_manager.sign_assertion(
        user_id=session_data["user_id"],
        username=session_data["username"],
        roles=session_data["roles"],
        permissions=session_data["permissions"],
        session_id=session_data["session_id"],
        ttl=assertion_ttl,
        custom_claims=req.custom_claims,
    )

    return PrincipalAssertionResponse(
        assertion_token=token,
        token_type="Bearer",
        expires_in=assertion_ttl,
        kid=key_manager.kid,
        algorithm=key_manager.algorithm,
    )
