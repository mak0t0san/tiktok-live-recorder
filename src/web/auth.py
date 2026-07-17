"""Single-password session auth for the web UI."""

import hmac
import secrets

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "tlr_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # seconds


class SessionAuth:
    """
    Checks the shared password and mints/verifies signed session tokens.

    The signing secret is random per process: restarting the server logs
    everyone out, which is acceptable for a single-operator tool.
    """

    def __init__(self, password: str, secret: str | None = None):
        if not password:
            raise ValueError("A web password is required.")
        self._password = password
        self._signer = TimestampSigner(secret or secrets.token_hex(32))

    def check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(self._password, candidate or "")

    def issue_token(self) -> str:
        return self._signer.sign(b"ok").decode()

    def verify_token(self, token: str | None) -> bool:
        if not token:
            return False
        try:
            self._signer.unsign(token, max_age=SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return False
        return True
