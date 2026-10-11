from pydantic import BaseModel

from app.common.account_validation import OptionalName, Password, Username

from app.schemas.user import UserResponse


class LoginRequest(BaseModel):
    username: Username
    password: Password


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class RegisterRequest(BaseModel):
    username: Username
    password: Password
    name: OptionalName = None
