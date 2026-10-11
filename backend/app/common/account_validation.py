"""账号与密码的统一业务校验；密码保留原样，不静默截断或去空格。"""

from typing import Annotated

from pydantic import BeforeValidator

from app.common.exceptions import BusinessException


def valid_username(value):
    if not isinstance(value, str) or not value.strip():
        raise BusinessException("账号不能为空", code=400)
    value = value.strip()
    if len(value) > 50:
        raise BusinessException("账号不能超过50字", code=400)
    return value


def valid_password(value):
    if not isinstance(value, str) or not value.strip():
        raise BusinessException("密码不能为空", code=400)
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise BusinessException("密码包含无效字符", code=400) from None
    if byte_length > 72:
        raise BusinessException("密码不能超过72字节（中文等字符可能占多个字节）", code=400)
    return value


def valid_optional_name(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > 50:
        raise BusinessException("姓名必须为不超过50字的文本", code=400)
    return value.strip() or None


Username = Annotated[str, BeforeValidator(valid_username)]
Password = Annotated[str, BeforeValidator(valid_password)]
OptionalName = Annotated[str | None, BeforeValidator(valid_optional_name)]
