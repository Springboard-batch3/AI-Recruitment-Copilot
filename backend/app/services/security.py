import hashlib
import hmac
import os
import re
import time
from typing import Tuple

_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,62}[A-Za-z0-9])?\.[A-Za-z]{2,63}$"
)
_SCRYPT_PREFIX = "scrypt"
_SCRYPT_VERSION = "1"


def canonicalize_email(raw_email: str) -> str:
    """
    Canonicalizes an email address:
    - Strips leading and trailing whitespace
    - Lowercases the entire address
    - Validates email syntax strictly
    Does NOT modify dots, plus-tags, or domain-specific nuances.
    """
    if not raw_email or not isinstance(raw_email, str):
        raise ValueError("Email address must be a non-empty string.")
    
    cleaned = raw_email.strip().lower()
    if not cleaned:
        raise ValueError("Email address cannot be blank.")
    
    if not _EMAIL_PATTERN.match(cleaned):
        raise ValueError(f"Invalid email address format: '{raw_email}'")
        
    return cleaned


def hash_password(
    plaintext_password: str,
    n: int = 16384,
    r: int = 8,
    p: int = 1,
    dklen: int = 32,
    salt_bytes: int = 16,
) -> str:
    """
    Hashes a password using Python's standard-library hashlib.scrypt with a cryptographically
    secure random salt from os.urandom.

    Returns a versioned encoded string:
    scrypt$v=1$n={n}$r={r}$p={p}${salt_hex}${derived_key_hex}
    """
    if not plaintext_password:
        raise ValueError("Password cannot be empty.")
    if n <= 1 or (n & (n - 1)) != 0:
        raise ValueError("scrypt parameter n must be a power of 2 greater than 1.")
    if r <= 0 or p <= 0 or dklen <= 0 or salt_bytes <= 0:
        raise ValueError("scrypt parameters must be positive integers.")

    salt = os.urandom(salt_bytes)
    derived_key = hashlib.scrypt(
        plaintext_password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=0,
        dklen=dklen,
    )

    salt_hex = salt.hex()
    key_hex = derived_key.hex()
    return f"{_SCRYPT_PREFIX}$v={_SCRYPT_VERSION}$n={n}$r={r}$p={p}${salt_hex}${key_hex}"


def verify_password(plaintext_password: str, encoded_hash: str) -> bool:
    """
    Verifies a plaintext password against a versioned scrypt hash using constant-time comparison.
    Never raises for invalid password; returns False on mismatch or corrupted hash format.
    """
    if not plaintext_password or not encoded_hash:
        return False

    try:
        parts = encoded_hash.split("$")
        # Format: ["scrypt", "v=1", "n=16384", "r=8", "p=1", salt_hex, key_hex]
        if len(parts) != 7:
            return False

        algo, v_part, n_part, r_part, p_part, salt_hex, expected_key_hex = parts

        if algo != _SCRYPT_PREFIX or not v_part.startswith("v="):
            return False

        n = int(n_part.split("=")[1])
        r = int(r_part.split("=")[1])
        p = int(p_part.split("=")[1])
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(expected_key_hex)
        dklen = len(expected_key)

        candidate_key = hashlib.scrypt(
            plaintext_password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=0,
            dklen=dklen,
        )

        return hmac.compare_digest(candidate_key, expected_key)

    except Exception:
        return False


def needs_rehash(
    encoded_hash: str,
    target_n: int = 16384,
    target_r: int = 8,
    target_p: int = 1,
    target_dklen: int = 32,
) -> bool:
    """
    Checks if an encoded scrypt hash was created with older/different parameters than the target parameters.
    """
    if not encoded_hash:
        return True

    try:
        parts = encoded_hash.split("$")
        if len(parts) != 7:
            return True

        algo, v_part, n_part, r_part, p_part, salt_hex, expected_key_hex = parts

        if algo != _SCRYPT_PREFIX or v_part != f"v={_SCRYPT_VERSION}":
            return True

        n = int(n_part.split("=")[1])
        r = int(r_part.split("=")[1])
        p = int(p_part.split("=")[1])
        dklen = len(bytes.fromhex(expected_key_hex))

        return n != target_n or r != target_r or p != target_p or dklen != target_dklen
    except Exception:
        return True


def hash_token(raw_token: str) -> str:
    """
    Computes the standard SHA-256 hex digest for a raw token (session token or CSRF token).
    Never stores or compares raw tokens directly in persistence layer.
    """
    if not raw_token or not isinstance(raw_token, str):
        raise ValueError("Token must be a non-empty string.")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_secure_token() -> str:
    """
    Generates a cryptographically secure URL-safe random string containing at least 256 bits of entropy.
    """
    import secrets
    return secrets.token_urlsafe(32)


# Pre-computed dummy hash using standard parameters for constant-time mitigation when account is absent
_DUMMY_SCRYPT_HASH = (
    "scrypt$v=1$n=16384$r=8$p=1$"
    "00000000000000000000000000000000$"
    "0000000000000000000000000000000000000000000000000000000000000000"
)


def dummy_verify_password(plaintext_password: str) -> None:
    """
    Executes a dummy scrypt verification against a pre-computed hash to normalize response timing
    and prevent account enumeration via timing side-channels.
    """
    _ = verify_password(plaintext_password or "dummy_password_timing_normalizer", _DUMMY_SCRYPT_HASH)


def benchmark_scrypt(
    iterations: int = 5,
    n: int = 16384,
    r: int = 8,
    p: int = 1,
    dklen: int = 32,
    salt_bytes: int = 16,
) -> float:
    """
    Benchmarks hashlib.scrypt with the specified parameters across multiple iterations.
    Returns the average duration in milliseconds without logging or leaking password/hash material.
    """
    sample_password = "BenchmarkSyntheticPassword!2026"
    durations = []

    for _ in range(iterations):
        salt = os.urandom(salt_bytes)
        t0 = time.perf_counter()
        _ = hashlib.scrypt(
            sample_password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=0,
            dklen=dklen,
        )
        t1 = time.perf_counter()
        durations.append((t1 - t0) * 1000.0)

    return sum(durations) / len(durations)

