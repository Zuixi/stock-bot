"""Authenticated user principal and permission models."""

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """Represents the security context of a request (authenticated or anonymous)."""

    user_id: str | None = None
    username: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    session_id: str | None = None
    is_authenticated: bool = False

    def has_role(self, *required_roles: str) -> bool:
        """Check if principal has any of the required roles (or admin)."""
        if not self.is_authenticated:
            return False
        if "admin" in self.roles:
            return True
        return any(role in self.roles for role in required_roles)

    def has_all_roles(self, *required_roles: str) -> bool:
        """Check if principal has all of the required roles (or admin)."""
        if not self.is_authenticated:
            return False
        if "admin" in self.roles:
            return True
        return all(role in self.roles for role in required_roles)

    def has_permission(self, *required_permissions: str) -> bool:
        """Check if principal has any of the required permissions (or admin role)."""
        if not self.is_authenticated:
            return False
        if "admin" in self.roles or "*:*" in self.permissions:
            return True
        return any(perm in self.permissions for perm in required_permissions)

    def has_all_permissions(self, *required_permissions: str) -> bool:
        """Check if principal has all of the required permissions (or admin role)."""
        if not self.is_authenticated:
            return False
        if "admin" in self.roles or "*:*" in self.permissions:
            return True
        return all(perm in self.permissions for perm in required_permissions)

    @classmethod
    def anonymous(cls, trace_id: str | None = None) -> "Principal":
        """Create an unauthenticated anonymous principal."""
        return cls(
            user_id=None,
            username=None,
            email=None,
            roles=[],
            permissions=[],
            trace_id=trace_id,
            session_id=None,
            is_authenticated=False,
        )
