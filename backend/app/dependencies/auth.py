from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.database import get_db
from app.models.user import User
from app.utils.jwt import decode_access_token, matches_password_version

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """JWT token 鉴权验证合法性"""
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    # 获取到用户ID
    user_id = payload.get("user_id")
    if type(user_id) is not int or user_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的登录凭证"
        )
    # 从数据库根据用户ID查询用户信息
    user = db.query(User).filter(User.id == user_id, User.status == 1).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效或用户已被禁用",
        )
    if not matches_password_version(payload, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录"
        )
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """判断JWT token内角色是否为admin"""
    if current_user.role != "admin":
        raise BusinessException(message="无权限访问", code=403)
    return current_user
