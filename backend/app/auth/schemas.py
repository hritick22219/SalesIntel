from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional
from models import UserRole

class UserBase(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.VIEWER
    company_id: Optional[int] = None

class UserCreate(UserBase):
    password: str = Field(..., min_length=6)

class UserResponse(UserBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenRefreshRequest(BaseModel):
    refresh_token: str
