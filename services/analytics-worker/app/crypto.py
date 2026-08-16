from __future__ import annotations

import structlog

from cryptography.fernet import Fernet

# See the privacy-constraint comment in app/config.py -- never log the key
# or the plaintext/ciphertext content, only byte counts.
logger = structlog.get_logger(__name__)

def cipher(key: str) -> Fernet:
    return Fernet(key.encode())

def encrypt(data: bytes, key: str) -> bytes:
    result = cipher(key).encrypt(data)
    logger.debug("encrypt_complete", bytes_in=len(data), bytes_out=len(result))
    return result

def decrypt(data: bytes, key: str) -> bytes:
    # Intentionally not wrapped in try/except: a failure here (bad key or
    # corrupt ciphertext) must propagate unchanged so callers keep their
    # existing exception handling. Callers log the failure themselves.
    result = cipher(key).decrypt(data)
    logger.debug("decrypt_complete", bytes_in=len(data), bytes_out=len(result))
    return result
