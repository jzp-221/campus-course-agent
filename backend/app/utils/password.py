import bcrypt

from app.common.account_validation import valid_password


def hash_password(password: str) -> str:
    """对密码加密"""
    password = valid_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """验证密码的有效性"""
    """  "123" -> "xxsdasdasdasfa"  """
    password = valid_password(password)
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False
