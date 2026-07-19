from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from database import get_db
from security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from redis_client import redis_client
from models import UserRole, UserDoc
from auth.schemas import (
    UserCreate,
    UserResponse,
    TokenResponse,
    TokenRefreshRequest,
)
from auth.dependencies import get_current_user, RequireRole, oauth2_scheme

router = APIRouter(tags=["Authentication"])

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, db: Any = Depends(get_db)):
    existing_user = await db.users.find_one({"email": user_in.email})
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists.",
        )

    hashed_pwd = hash_password(user_in.password)
    user_dict = {
        "email": user_in.email,
        "hashed_password": hashed_pwd,
        "role": user_in.role.value if isinstance(user_in.role, UserRole) else user_in.role,
        "company_id": user_in.company_id,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.users.insert_one(user_dict)
    user_dict["_id"] = str(result.inserted_id)
    return UserResponse(
        email=user_dict["email"],
        role=user_dict["role"],
        company_id=user_dict["company_id"]
    )

@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Any = Depends(get_db)):
    user = await db.users.find_one({"email": form_data.username})
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    user_payload = {"sub": user["email"], "role": user.get("role", "viewer"), "company_id": user.get("company_id")}
    access_token = create_access_token(user_payload)
    refresh_token = create_refresh_token(user_payload)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.post("/refresh", response_model=TokenResponse)
async def refresh(refresh_in: TokenRefreshRequest, db: Any = Depends(get_db)):
    token = refresh_in.refresh_token

    try:
        is_blacklisted = await redis_client.get(f"blacklist:{token}")
        if is_blacklisted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token is blacklisted. Please log in again.",
            )
    except Exception:
        pass

    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    email = payload.get("sub")
    user = await db.users.find_one({"email": email})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists.",
        )

    exp = payload.get("exp")
    now = datetime.now(timezone.utc).timestamp()
    remaining = int(exp - now)
    if remaining > 0:
        try:
            await redis_client.setex(f"blacklist:{token}", remaining, "true")
        except Exception:
            pass

    user_payload = {"sub": user["email"], "role": user.get("role", "viewer"), "company_id": user.get("company_id")}
    new_access_token = create_access_token(user_payload)
    new_refresh_token = create_refresh_token(user_payload)

    return TokenResponse(access_token=new_access_token, refresh_token=new_refresh_token)

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: UserDoc = Depends(get_current_user),
):
    payload = decode_token(token)
    if payload:
        exp = payload.get("exp")
        now = datetime.now(timezone.utc).timestamp()
        remaining = int(exp - now)
        if remaining > 0:
            try:
                await redis_client.setex(f"blacklist:{token}", remaining, "true")
            except Exception:
                pass

    return {"detail": "Successfully logged out."}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserDoc = Depends(get_current_user)):
    return UserResponse(
        email=current_user.email,
        role=current_user.role,
        company_id=current_user.company_id
    )
