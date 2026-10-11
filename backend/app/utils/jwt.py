from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import jwt
from app.config import settings


def _password_fingerprint(password_hash: str) -> str:
    # JWT 不携带密码或可用于离线猜解的 bcrypt 哈希。
    return hmac.new(settings.JWT_SECRET_KEY.encode("utf-8"),
                    ("campus-password-version:" + password_hash).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def create_access_token(user_id: int, password_hash: str) -> str:
    """凭证绑定签发时的密码状态，改密码立即撤销旧凭证，无需迁移表。"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)

    payload = {"user_id": user_id, "exp": expire,
               "pwdv": _password_fingerprint(password_hash)}

    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_access_token(token: str) -> dict:
    """解析jwt token"""
    return jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["user_id", "exp", "pwdv"]},
    )


def matches_password_version(payload: dict, password_hash: str) -> bool:
    value = payload.get("pwdv")
    return (isinstance(value, str) and len(value) == 64 and value.isascii()
            and hmac.compare_digest(value, _password_fingerprint(password_hash)))
