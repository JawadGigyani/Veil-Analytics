import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip_preserves_arbitrary_bytes():
    key = Fernet.generate_key().decode()
    plaintext = b"CSV,parquet\x00binary\xffpayload"

    encrypted = encrypt(plaintext, key)

    assert encrypted != plaintext
    assert decrypt(encrypted, key) == plaintext


def test_encrypted_payload_cannot_be_decrypted_with_another_key():
    encrypted = encrypt(b"sensitive dataset", Fernet.generate_key().decode())

    with pytest.raises(InvalidToken):
        decrypt(encrypted, Fernet.generate_key().decode())
