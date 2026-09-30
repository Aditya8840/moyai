import hashlib
import hmac
import secrets
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def local_secret(path: Path, generate) -> str:
    try:
        with path.open("x") as handle:
            path.chmod(0o600)
            handle.write(generate())
    except FileExistsError:
        pass
    return path.read_text().strip()


class Security:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.secret = settings.session_secret or local_secret(settings.data_dir / "session.key", lambda: secrets.token_urlsafe(48))
        key = settings.encryption_key or local_secret(settings.data_dir / "encryption.key", lambda: Fernet.generate_key().decode())
        self.fernet = Fernet(key.encode())
        self.signer = URLSafeTimedSerializer(self.secret, salt="workspace-session-v1")
        self.origin = settings.public_url.rstrip("/")
        self.local = urlparse(self.origin).hostname in {"localhost", "127.0.0.1", "::1"}
        if not self.local and (not settings.workspace_password or len(settings.workspace_password) < 16):
            raise ValueError("Set WORKSPACE_PASSWORD to at least 16 characters before exposing the workspace.")
        if not self.local and not self.origin.startswith("https://"):
            raise ValueError("PUBLIC_URL must use HTTPS for a remote workspace.")
        if settings.workspace_member_password and (
            len(settings.workspace_member_password) < 16
            or not settings.workspace_password
            or hmac.compare_digest(settings.workspace_member_password, settings.workspace_password)
        ):
            raise ValueError("WORKSPACE_MEMBER_PASSWORD must be at least 16 characters and different from the administrator password.")

    def session_info(self, request: Request) -> dict | None:
        try:
            info = self.signer.loads(request.cookies.get("workspace_session", ""), max_age=43200)
            # Sessions from the original single-password deployment belong to
            # its administrator. New member sessions always carry a role/tag.
            role = info.get("role", "admin")
            if role not in {"admin", "member"} or not isinstance(info.get("sid"), str):
                return None
            if role == "member" and (
                not self.settings.workspace_member_password or not hmac.compare_digest(
                    info.get("password_tag", ""), digest(self.settings.workspace_member_password))
            ):
                return None
            return {**info, "role": role}
        except (BadSignature, SignatureExpired, KeyError, TypeError):
            return None

    def session(self, request: Request) -> str | None:
        info = self.session_info(request)
        return info["sid"] if info else None

    def role(self, request: Request) -> str | None:
        info = self.session_info(request)
        return info["role"] if info else None

    def csrf(self, sid: str) -> str:
        return hmac.new(self.secret.encode(), f"csrf:{sid}".encode(), hashlib.sha256).hexdigest()

    def require(self, request: Request, *, mutation: bool = False, admin: bool = False) -> str:
        sid = self.session(request)
        if not sid:
            raise HTTPException(401, "Sign in to the workspace.")
        if admin and self.role(request) != "admin":
            raise HTTPException(403, "An organization administrator must perform this action.")
        if mutation:
            self.check_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), self.csrf(sid)):
                raise HTTPException(403, "Refresh the page and try again.")
        return sid

    def check_origin(self, request: Request):
        if request.headers.get("origin") != self.origin:
            raise HTTPException(403, "This request must come from the workspace.")

    def new_session(self, response, role: str = "admin") -> str:
        sid = secrets.token_urlsafe(32)
        info = {"sid": sid, "role": role}
        if role == "member":
            info["password_tag"] = digest(self.settings.workspace_member_password)
        response.set_cookie("workspace_session", self.signer.dumps(info), max_age=43200,
                            httponly=True, secure=not self.local, samesite="lax", path="/")
        return sid

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode()).decode()
