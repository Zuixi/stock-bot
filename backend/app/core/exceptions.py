"""Application-level exception hierarchy."""

from fastapi import HTTPException, status


class AppError(Exception):
    """Base exception for all application errors."""


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    def __init__(self, resource: str, identifier: str | int) -> None:
        self.resource = resource
        self.identifier = identifier
        super().__init__(f"{resource} not found: {identifier}")


class ValidationError(AppError):
    """Raised when business rule validation fails."""


class ConflictError(AppError):
    """Raised when a unique constraint or state conflict occurs."""


def not_found_response(resource: str, identifier: str | int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource} not found: {identifier}",
    )


def conflict_response(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )
