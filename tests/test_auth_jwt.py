"""Supabase JWT verification tests — asymmetric (ES256 via JWKS) and legacy HS256.

Regression coverage for the July 2026 outage: the Supabase project migrated to
asymmetric JWT signing keys (ES256), but web.auth only verified HS256 with the
shared secret, so every authenticated request 401'd right after a successful
login.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwk, jwt
from sqlmodel import Session

import web.auth as web_auth
from database.models import User

SUPABASE_UID = "11111111-2222-3333-4444-555555555555"
HS256_SECRET = "test-legacy-jwt-secret"


@pytest.fixture()
def ec_keypair():
    """Generate a P-256 keypair; returns (private_pem, public_jwks_dict)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_jwk = jwk.construct(public_pem, algorithm="ES256").to_dict()
    public_jwk["kid"] = "test-key-1"
    return private_pem, {"keys": [public_jwk]}


def _claims(sub: str = SUPABASE_UID, aud: str = "authenticated") -> dict:
    return {
        "sub": sub,
        "aud": aud,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }


def _seed_supabase_user(engine) -> None:
    with Session(engine) as session:
        session.add(User(name="Jwt User", email="jwt@example.com", supabase_uid=SUPABASE_UID))
        session.commit()


@pytest.fixture(autouse=True)
def _fresh_jwks_cache(monkeypatch):
    monkeypatch.setattr(web_auth, "_jwks_cache", {"jwks": None, "fetched_at": 0.0})
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


def _configure_jwks(monkeypatch, jwks: dict) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(web_auth, "_fetch_jwks", lambda url: jwks)


def test_es256_token_verified_via_jwks(isolated_engine, monkeypatch, ec_keypair):
    private_pem, jwks = ec_keypair
    _configure_jwks(monkeypatch, jwks)
    _seed_supabase_user(isolated_engine)

    token = jwt.encode(_claims(), private_pem, algorithm="ES256", headers={"kid": "test-key-1"})
    user = web_auth._user_from_supabase_jwt(token)
    assert user is not None
    assert user.supabase_uid == SUPABASE_UID


def test_hs256_fallback_still_verified(isolated_engine, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", HS256_SECRET)
    _seed_supabase_user(isolated_engine)

    token = jwt.encode(_claims(), HS256_SECRET, algorithm="HS256")
    user = web_auth._user_from_supabase_jwt(token)
    assert user is not None
    assert user.supabase_uid == SUPABASE_UID


def test_hs256_fallback_when_jwks_configured(isolated_engine, monkeypatch, ec_keypair):
    """A legacy HS256 token (e.g. issued mid-migration) still resolves when JWKS is live."""
    _, jwks = ec_keypair
    _configure_jwks(monkeypatch, jwks)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", HS256_SECRET)
    _seed_supabase_user(isolated_engine)

    token = jwt.encode(_claims(), HS256_SECRET, algorithm="HS256")
    assert web_auth._user_from_supabase_jwt(token) is not None


def test_token_signed_by_unknown_key_rejected(isolated_engine, monkeypatch, ec_keypair):
    _, jwks = ec_keypair
    _configure_jwks(monkeypatch, jwks)
    _seed_supabase_user(isolated_engine)

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    other_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    token = jwt.encode(_claims(), other_pem, algorithm="ES256", headers={"kid": "test-key-1"})
    assert web_auth._user_from_supabase_jwt(token) is None


def test_wrong_audience_rejected(isolated_engine, monkeypatch, ec_keypair):
    private_pem, jwks = ec_keypair
    _configure_jwks(monkeypatch, jwks)
    _seed_supabase_user(isolated_engine)

    token = jwt.encode(
        _claims(aud="anon"), private_pem, algorithm="ES256", headers={"kid": "test-key-1"}
    )
    assert web_auth._user_from_supabase_jwt(token) is None


def test_jwks_fetch_failure_serves_stale_cache(isolated_engine, monkeypatch, ec_keypair):
    private_pem, jwks = ec_keypair
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    # Pre-populated but expired cache + a fetch that now fails.
    monkeypatch.setattr(
        web_auth, "_jwks_cache", {"jwks": jwks, "fetched_at": time.time() - 7200}
    )
    def _boom(url):
        raise ConnectionError("supabase unreachable")
    monkeypatch.setattr(web_auth, "_fetch_jwks", _boom)
    _seed_supabase_user(isolated_engine)

    token = jwt.encode(_claims(), private_pem, algorithm="ES256", headers={"kid": "test-key-1"})
    assert web_auth._user_from_supabase_jwt(token) is not None
