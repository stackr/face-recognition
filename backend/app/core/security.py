import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet
from pwdlib import PasswordHash

COOKIE_NAME = "cctv_session"
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(session_token: str, secret: str) -> str:
    return hmac.new(secret.encode(), session_token.encode(), hashlib.sha256).hexdigest()


def encrypt_rtsp(url: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(url.encode()).decode()


def redact_rtsp(ciphertext: str, key: str) -> str:
    parsed = urlsplit(Fernet(key.encode()).decrypt(ciphertext.encode()).decode())
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


class LoginLimiter:
    """Bounded, thread-safe limiter for the single-process MVP API."""

    def __init__(self, limit: int = 10, window: float = 300, max_keys: int = 4096):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._attempts: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._attempts.pop(key, []) if now - stamp < self.window]
            allowed = len(recent) < self.limit
            if allowed:
                recent.append(now)
            self._attempts[key] = recent
            while len(self._attempts) > self.max_keys:
                self._attempts.popitem(last=False)
            return allowed
