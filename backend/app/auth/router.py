from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

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
from models import User, UserRole
from auth.schemas import (
    UserCreate,
    UserResponse,
    UserLogin,
    TokenResponse,
    TokenRefreshRequest,
)
from auth.dependencies import get_current_user, RequireRole, oauth2_scheme

router = APIRouter(tags=["Authentication"])

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # Check if email exists
    result = await db.execute(select(User).filter(User.email == user_in.email))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists.",
        )

    # Create new user
    hashed_pwd = hash_password(user_in.password)
    new_user = User(
        email=user_in.email,
        hashed_password=hashed_pwd,
        role=user_in.role,
        company_id=user_in.company_id,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # Retrieve user
    result = await db.execute(select(User).filter(User.email == form_data.username))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Generate tokens
    user_payload = {"sub": user.email, "role": user.role, "company_id": user.company_id}
    access_token = create_access_token(user_payload)
    refresh_token = create_refresh_token(user_payload)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)

@router.post("/refresh", response_model=TokenResponse)
async def refresh(refresh_in: TokenRefreshRequest, db: AsyncSession = Depends(get_db)):
    token = refresh_in.refresh_token

    # Check blacklist
    is_blacklisted = await redis_client.get(f"blacklist:{token}")
    if is_blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is blacklisted. Please log in again.",
        )

    # Decode and verify refresh token
    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    # Fetch user
    email = payload.get("sub")
    result = await db.execute(select(User).filter(User.email == email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists.",
        )

    # Blacklist the old refresh token (Token Rotation)
    exp = payload.get("exp")
    now = datetime.now(timezone.utc).timestamp()
    remaining = int(exp - now)
    if remaining > 0:
        await redis_client.setex(f"blacklist:{token}", remaining, "true")

    # Generate new pair
    user_payload = {"sub": user.email, "role": user.role, "company_id": user.company_id}
    new_access_token = create_access_token(user_payload)
    new_refresh_token = create_refresh_token(user_payload)

    return TokenResponse(access_token=new_access_token, refresh_token=new_refresh_token)

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
):
    # Blacklist the access token
    payload = decode_token(token)
    if payload:
        exp = payload.get("exp")
        now = datetime.now(timezone.utc).timestamp()
        remaining = int(exp - now)
        if remaining > 0:
            await redis_client.setex(f"blacklist:{token}", remaining, "true")

    return {"detail": "Successfully logged out."}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user

# --- RBAC Test Routes ---
@router.get("/admin-only", response_model=UserResponse)
async def admin_route(
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    return current_user

@router.get("/analyst-or-admin", response_model=UserResponse)
async def analyst_route(
    current_user: User = Depends(RequireRole([UserRole.ADMIN, UserRole.ANALYST]))
):
    return current_user
