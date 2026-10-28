"""argon2id hashing, password policy and generated passwords."""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library defaults

# Alphabet without look-alike characters (0 O o 1 l I).
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"

MIN_LENGTH = 12
MAX_LENGTH = 256

_COMMON = {
    "password", "password1", "password12", "password123", "password1234", "passw0rd1234",
    "123456789012", "1234567890123", "12345678901234", "123456123456", "111111111111",
    "000000000000", "qwertyuiopas", "qwertyuiop12", "qwerty123456", "qwertyqwerty",
    "abcdefghijkl", "abcd12345678", "iloveyou1234", "letmein12345", "welcome12345",
    "administrator", "admin1234567", "adminadmin12", "changeme1234", "changemenow1",
    "walnutgrove1", "walnutgrove12", "walnutgrove123", "walnutdesk12", "walnutgrovedesk",
    "veterinarian", "veterinary12", "animalhospital", "monkey123456", "dragon123456",
    "football1234", "baseball1234", "sunshine1234", "princess1234", "trustno1trust",
    "passwordpassword", "password1password", "1q2w3e4r5t6y", "1qaz2wsx3edc", "zaq12wsxcde3",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


# Verified against when the username does not exist, so timing does not reveal accounts.
DUMMY_HASH = hash_password("dummy-password-for-timing-only")


def generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def check_new_password(new: str, current: str, username: str) -> str | None:
    """Return a plain-words problem, or None when the password is acceptable."""
    if len(new) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(new) > MAX_LENGTH:
        return "That password is too long."
    low = new.lower()
    if low == username.lower():
        return "The password cannot be your username."
    if new == current:
        return "Pick a password you have not used here before."
    if low in _COMMON or low.strip() in _COMMON or len(set(low)) <= 3:
        return "That password is too easy to guess. Try a few unrelated words."
    return None
