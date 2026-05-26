"""Fernet encryption for integration tokens stored in Postgres."""

from cryptography.fernet import Fernet, InvalidToken


def encrypt_secret(plaintext: str, fernet_key: str) -> str:
    f = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str, fernet_key: str) -> str:
    f = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Invalid ciphertext or key") from exc
