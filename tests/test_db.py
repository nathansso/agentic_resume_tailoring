"""Database and user-utils tests."""
import database.user_utils as user_utils_module
from database.auth import hash_password, verify_password


def test_get_active_profile_returns_none_on_empty_db(isolated_engine):
    """get_active_profile returns None when no profile file or DB record exists."""
    result = user_utils_module.get_active_profile()
    assert result is None


def test_create_profile_persists_and_loads(isolated_engine):
    """create_profile saves to DB and get_active_profile reloads it."""
    user = user_utils_module.create_profile("Alice", "alice@test.com", github_username="alicecodes")
    assert user is not None
    assert user.name == "Alice"
    assert isolated_engine._test_profile_file.exists()

    loaded = user_utils_module.get_active_profile()
    assert loaded is not None
    assert loaded.user_id == user.user_id
    assert loaded.name == "Alice"


def test_get_active_profile_file_is_empty(isolated_engine):
    """get_active_profile returns None when the profile file exists but is empty."""
    profile_file = isolated_engine._test_profile_file
    profile_file.write_text("")  # empty file
    result = user_utils_module.get_active_profile()
    assert result is None


def test_get_active_profile_file_has_invalid_uuid(isolated_engine):
    """get_active_profile returns None when the file contains a non-UUID string."""
    profile_file = isolated_engine._test_profile_file
    profile_file.write_text("definitely-not-a-uuid")
    result = user_utils_module.get_active_profile()
    assert result is None


def test_get_active_profile_user_deleted_from_db(isolated_engine):
    """get_active_profile returns None when the profile file points to a deleted user."""
    import uuid
    from sqlmodel import Session
    from database.models import User

    # Create a user, write its ID to the profile file, then delete the user row
    with Session(isolated_engine) as session:
        user = User(name="Temp", email="temp@test.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

    isolated_engine._test_profile_file.write_text(str(uid))

    # Delete the user from DB
    with Session(isolated_engine) as session:
        db_user = session.get(User, uid)
        if db_user:
            session.delete(db_user)
            session.commit()

    result = user_utils_module.get_active_profile()
    assert result is None, "Should return None when the user referenced in the file no longer exists"


# ── Auth tests ────────────────────────────────────────────────────────────────

def test_hash_and_verify_password():
    """hash_password produces a verifiable hash; wrong password fails."""
    pw = "mysecretpassword"
    hashed = hash_password(pw)
    assert hashed.startswith("pbkdf2:")
    assert verify_password(pw, hashed)
    assert not verify_password("wrongpassword", hashed)


def test_hash_password_unique_salts():
    """Two hashes of the same password should differ (different salts)."""
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2


def test_verify_password_bad_format():
    """verify_password returns False for a malformed hash string."""
    assert not verify_password("any", "notahash")


def test_create_profile_with_username_and_auth(isolated_engine):
    """create_profile stores username and password_hash; authenticate_local works."""
    from database.auth import hash_password
    pw_hash = hash_password("testpass123")
    user = user_utils_module.create_profile(
        "Bob",
        "bob@art.local",
        username="bob",
        password_hash=pw_hash,
    )
    assert user.username == "bob"
    assert user.password_hash == pw_hash

    found = user_utils_module.get_user_by_username("bob")
    assert found is not None
    assert found.user_id == user.user_id

    authed = user_utils_module.authenticate_local("bob", "testpass123")
    assert authed is not None
    assert authed.user_id == user.user_id

    assert user_utils_module.authenticate_local("bob", "wrongpass") is None


def test_get_user_by_username_not_found(isolated_engine):
    """get_user_by_username returns None for an unknown username."""
    assert user_utils_module.get_user_by_username("nobody") is None


def test_username_uniqueness(isolated_engine):
    """Creating two profiles with the same username raises an integrity error."""
    from database.auth import hash_password
    import pytest
    pw = hash_password("pass1234")
    user_utils_module.create_profile("Alice", "alice@art.local", username="alice", password_hash=pw)
    with pytest.raises(Exception):
        user_utils_module.create_profile("Alice2", "alice2@art.local", username="alice", password_hash=pw)
