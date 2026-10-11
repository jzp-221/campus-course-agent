from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.schemas.auth import LoginRequest
from app.schemas.auth import RegisterRequest
from sqlalchemy.orm import Session
from app.database import get_db
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["权限验证"])


@router.post("/token")
def token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
):
    """供 Swagger 使用的 OAuth2 表单登录；前端继续调用 JSON 登录接口。"""
    try:
        result = auth_service.login(
            db, LoginRequest(username=form.username, password=form.password)
        )
    except BusinessException as exc:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": exc.message},
        )
    return {"access_token": result.token, "token_type": "bearer"}


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """登录"""
    result = auth_service.login(db, data)
    # 返回信息
    return Response.success(
        message="登录成功",
        data=result,
    )


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """注册"""
    auth_service.register(db, data)
    # 返回信息
    return Response.success(message="注册成功")
