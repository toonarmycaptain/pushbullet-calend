"""Encrypt and decrypt secrets using Fernet symmetric encryption."""

from pathlib import Path

from cryptography.fernet import Fernet

_DEFAULT_KEY_PATH = Path.home() / ".pushbullet-calend.key"


def generate_key(key_path: Path = _DEFAULT_KEY_PATH) -> bytes:
    """Generate a new Fernet key and save it to *key_path*. Returns the key."""
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def load_key(key_path: Path = _DEFAULT_KEY_PATH) -> bytes:
    """Load the Fernet key from *key_path*."""
    return key_path.read_bytes().strip()


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt *plaintext* and return a URL-safe base64 token string."""
    return Fernet(key).encrypt(plaintext.encode()).decode()


def decrypt(token: str, key_path: Path = _DEFAULT_KEY_PATH) -> str:
    """Decrypt a Fernet token string using the key at *key_path*."""
    key = load_key(key_path)
    return Fernet(key).decrypt(token.encode()).decode()
