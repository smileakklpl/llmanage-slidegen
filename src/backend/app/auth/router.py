"""Auth API routes — login and register with password support."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr

from fastapi import APIRouter, HTTPException, status

from app.auth.jwt import create_access_token
from app.auth.users import get_user_by_email, register_user, verify_user_password

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- Schemas ----------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str = ""


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    name: str


# ---------- Endpoints ----------


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    """Login with email + password. Returns JWT if credentials are valid."""
    user = verify_user_password(body.email, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email 或密碼錯誤",
        )

    token = create_access_token({"sub": user["email"], "name": user.get("name", "")})
    return AuthResponse(
        access_token=token,
        email=user["email"],
        name=user.get("name", ""),
    )


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(body: RegisterRequest) -> AuthResponse:
    """Register a new user with email, password, and name. Returns JWT."""
    if len(body.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="密碼至少需要 6 個字元",
        )

    try:
        user = register_user(body.email, body.password, body.name)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    token = create_access_token({"sub": user["email"], "name": user.get("name", "")})
    return AuthResponse(
        access_token=token,
        email=user["email"],
        name=user.get("name", ""),
    )
