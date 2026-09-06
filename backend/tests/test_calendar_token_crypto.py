"""Calendar token encrypt/decrypt + fail-soft when key missing."""
import os
from cryptography.fernet import Fernet

from app.services import calendar_token_crypto as c


def setup_function(_):
    os.environ.pop("CALENDAR_TOKEN_ENCRYPTION_KEY", None)


def test_passthrough_without_key():
    assert c.encrypt_secret("tok_plain") == "tok_plain"
    assert c.decrypt_secret("tok_plain") == "tok_plain"
    assert c.encryption_configured() is False


def test_roundtrip_with_fernet_key():
    key = Fernet.generate_key().decode()
    os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = key
    enc = c.encrypt_secret("secret-token")
    assert enc.startswith("enc:v1:")
    assert enc != "secret-token"
    assert c.decrypt_secret(enc) == "secret-token"
    # idempotent encrypt
    assert c.encrypt_secret(enc) == enc


def test_derived_key_from_arbitrary_secret():
    os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = "bricks-dev-calendar-secret-please-rotate"
    enc = c.encrypt_secret("abc")
    assert c.decrypt_secret(enc) == "abc"


def test_missing_key_cannot_decrypt_ciphertext():
    key = Fernet.generate_key().decode()
    os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = key
    enc = c.encrypt_secret("hidden")
    os.environ.pop("CALENDAR_TOKEN_ENCRYPTION_KEY", None)
    assert c.decrypt_secret(enc) is None


def test_encrypt_connection_secrets_fields():
    os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    row = {
        "provider": "google",
        "access_token": "a",
        "refresh_token": "r",
        "caldav_password": "p",
        "ics_url": "https://example.com/x.ics",
    }
    stored = c.encrypt_connection_secrets(row)
    assert stored["access_token"].startswith("enc:v1:")
    assert stored["refresh_token"].startswith("enc:v1:")
    assert stored["caldav_password"].startswith("enc:v1:")
    assert stored["ics_url"] == "https://example.com/x.ics"
    plain = c.decrypt_connection_secrets(stored)
    assert plain["access_token"] == "a"
    assert plain["refresh_token"] == "r"
    assert plain["caldav_password"] == "p"


def test_needs_reencrypt():
    os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    assert c.needs_reencrypt({"access_token": "plain"}) is True
    enc = c.encrypt_secret("plain")
    assert c.needs_reencrypt({"access_token": enc}) is False
