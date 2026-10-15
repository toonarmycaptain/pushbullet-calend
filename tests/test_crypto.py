"""Tests for pushbullet_calend.crypto."""

import stat

import pytest
from cryptography.fernet import InvalidToken

from pushbullet_calend.crypto import decrypt, encrypt, generate_key, load_key


def test_generated_key_is_owner_only(tmp_path):
    key_path = tmp_path / "test.key"

    key = generate_key(key_path)

    assert load_key(key_path) == key
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("suffix", [b"", b"\n", b"\r\n"], ids=["bare", "lf", "crlf"])
def test_load_key_strips_line_endings(tmp_path, suffix):
    key_path = tmp_path / "test.key"
    key_path.write_bytes(b"KEY" + suffix)

    assert load_key(key_path) == b"KEY"


@pytest.mark.parametrize(
    "plaintext", ["hunter2", "pässwörd ✓", ""], ids=["ascii", "unicode", "empty"]
)
def test_encrypt_decrypt_round_trip(tmp_path, plaintext):
    key_path = tmp_path / "test.key"
    key = generate_key(key_path)

    assert decrypt(encrypt(plaintext, key), key_path) == plaintext


def test_decrypt_with_wrong_key_fails(tmp_path):
    token = encrypt("hunter2", generate_key(tmp_path / "other.key"))
    generate_key(tmp_path / "test.key")

    with pytest.raises(InvalidToken):
        decrypt(token, tmp_path / "test.key")
