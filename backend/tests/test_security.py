import jwt
import pytest

from cloudsentinel.security import (
    create_access_token,
    decode_access_token,
    decrypt_secrets,
    encrypt_secrets,
    generate_agent_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret-password")
    assert h.startswith("scrypt$")
    assert verify_password("s3cret-password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # salted


def test_access_token_roundtrip_and_tamper():
    token = create_access_token("42", "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "42" and payload["role"] == "admin"
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token[:-2] + "xx")


def test_expired_token():
    token = create_access_token("1", "viewer", ttl_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_secret_encryption():
    blob = encrypt_secrets({"bearer_token": "abc"})
    assert blob and "abc" not in blob
    assert decrypt_secrets(blob) == {"bearer_token": "abc"}
    assert encrypt_secrets({}) is None
    assert decrypt_secrets(None) == {}
    with pytest.raises(RuntimeError):
        decrypt_secrets("not-a-valid-token")


def test_agent_tokens():
    t = generate_agent_token()
    assert t.startswith("csa_") and len(t) > 30
    assert hash_token(t) == hash_token(t) != hash_token(t + "x")
