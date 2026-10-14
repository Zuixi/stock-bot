"""Pure-function tests for the login denial matrix (ADR 0010)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.user_status import (
    DENIAL_LOCKED,
    DENIAL_STATUS,
    DENIAL_SUSPENDED,
    DENIAL_TEMP_PASSWORD_EXPIRED,
    LEGACY_UNUSED,
    LOGINABLE,
    login_denial,
    temp_password_expired,
)


class FakeCred:
    auth_type = "password"

    def __init__(self, must_change=False, expires=None):
        self.must_change_password = must_change
        self.temp_password_expires_at = expires


class FakeUser:
    def __init__(self, status, reason=None, cred=None):
        self.status = status
        self.status_reason = reason
        self.credentials = [cred] if cred else []


NOW = datetime.now(UTC)


def test_loginable_is_exactly_active():
    assert LOGINABLE == {"active"}


def test_legacy_pending_verification_is_dead_enum():
    assert LEGACY_UNUSED == {"pending_verification"}
    # And nothing accepts it for login:
    assert login_denial(FakeUser("pending_verification")) == DENIAL_STATUS


@pytest.mark.parametrize(
    "status,expected",
    [
        ("active", None),
        ("suspended", DENIAL_SUSPENDED),
        ("pending_verification", DENIAL_STATUS),
        ("bogus_status", DENIAL_STATUS),
    ],
)
def test_denial_matrix_by_status(status, expected):
    assert login_denial(FakeUser(status), NOW) == expected


def test_locked_within_window_denies_with_generic_credentials():
    user = FakeUser("locked")
    user.locked_until = NOW + timedelta(minutes=5)
    code = login_denial(user, NOW)
    assert code == DENIAL_LOCKED
    # Locked denial must NOT reveal more than a wrong password would.
    assert code[1] == "AUTH_INVALID_CREDENTIALS"


def test_expired_lock_proceeds():
    user = FakeUser("locked")
    user.locked_until = NOW - timedelta(minutes=1)
    assert login_denial(user, NOW) is None


def test_suspended_reason_appended_to_message():
    denial = login_denial(FakeUser("suspended", reason="离职"), NOW)
    assert denial[0] == 403
    assert denial[1] == "AUTH_ACCOUNT_SUSPENDED"
    assert "离职" in denial[2]


def test_temp_password_expiry():
    past = NOW - timedelta(hours=1)
    future = NOW + timedelta(hours=71)
    # Expired temp password blocks login
    assert temp_password_expired(FakeCred(True, past), NOW) is True
    # Not yet expired -> allowed (but gated to change-password by session)
    assert temp_password_expired(FakeCred(True, future), NOW) is False
    # Regular password: no deadline at all
    assert temp_password_expired(FakeCred(False, past), NOW) is False
    assert temp_password_expired(None, NOW) is False


def test_temp_password_expired_in_login_denial():
    user = FakeUser(
        "active",
        cred=FakeCred(True, expires=NOW - timedelta(hours=1)),
    )
    assert login_denial(user, NOW) == DENIAL_TEMP_PASSWORD_EXPIRED


def test_active_with_unexpired_temp_password_logs_in():
    user = FakeUser("active", cred=FakeCred(True, expires=NOW + timedelta(hours=72)))
    assert login_denial(user, NOW) is None
