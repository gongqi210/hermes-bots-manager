from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: Literal["Owner", "Admin", "Editor", "Viewer"]
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_in: int
    refresh_expires_in: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr


class LoginResponse(BaseModel):
    user: UserOut
    tokens: TokenPair


class BootstrapRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    access_expires_in: int


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr
    role: Literal["Admin", "Editor", "Viewer"]  # Owner created only via bootstrap
