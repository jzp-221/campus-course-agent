"""MCP 工具鉴权：从请求头取得当前用户，复用业务 API 的 JWT。"""

from jwt import InvalidTokenError

from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.user import User
from app.utils.jwt import decode_access_token, matches_password_version


def user_id_from_authorization(authorization: str | None) -> int:
    """校验 Bearer Token 及用户状态，返回当前用户的 ID。"""
    if not authorization:
        raise BusinessException(message="缺少 Authorization", code=401)

    if not isinstance(authorization, str):
        raise BusinessException(message="无效的登录凭证", code=401)

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise BusinessException(message="无效的登录凭证", code=401)

    try:
        payload = decode_access_token(parts[1])
    except InvalidTokenError as exc:
        raise BusinessException(
            message="登录已失效，请重新登录", code=401
        ) from exc

    user_id = payload.get("user_id")
    # bool、数字字符串和浮点数不能作为用户 ID，避免数据库隐式转换。
    if type(user_id) is not int or user_id <= 0:
        raise BusinessException(message="无效的登录凭证", code=401)

    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id, User.status == 1).first()
        if user is None:
            raise BusinessException(message="登录已失效或用户已被禁用", code=401)
        if not matches_password_version(payload, user.password):
            raise BusinessException(message="登录已失效，请重新登录", code=401)
        return user.id
