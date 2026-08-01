"""Auth API routes — login and register."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr

from fastapi import APIRouter, HTTPException, status

from app.auth.jwt import create_access_token
from app.auth.users import get_user_by_email, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- Schemas ----------


class LoginRequest(BaseModel):
    email: EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = ""


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    name: str


# ---------- Endpoints ----------


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    """Login with email. Returns JWT if the email is in the allowed list."""
    user = get_user_by_email(body.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="此 Email 未註冊，請先註冊",
        )

    token = create_access_token({"sub": user["email"], "name": user.get("name", "")})
    return AuthResponse(
        access_token=token,
        email=user["email"],
        name=user.get("name", ""),
    )


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest) -> AuthResponse:
    """Register a new user email and return JWT."""
    try:
        user = register_user(body.email, body.name)
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
