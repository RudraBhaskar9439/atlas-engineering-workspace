"""Synthetic repository fixture; Atlas analyses this file without executing it."""
from hashlib import sha256


def fingerprint(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def rotate_refresh_token(previous: str, replacement: str, revoked: set[str]) -> str:
    if fingerprint(previous) in revoked:
        raise ValueError("Refresh token has already been used")
    revoked.add(fingerprint(previous))
    return fingerprint(replacement)
