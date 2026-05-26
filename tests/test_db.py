"""Database and user-utils tests."""
import database.user_utils as user_utils_module


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
