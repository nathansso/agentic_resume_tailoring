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
