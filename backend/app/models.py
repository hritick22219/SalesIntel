from datetime import datetime, timezone
import enum
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field, EmailStr

class ImportJobStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"

class UserDoc(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    email: EmailStr
    hashed_password: str
    role: UserRole = UserRole.VIEWER
    company_id: Optional[int] = None

class ImportJobDoc(BaseModel):
    id: Optional[Any] = Field(None, alias="_id")
    job_id: int
    user_id: Optional[int] = None
    company_id: Optional[int] = None
    source_type: str
    status: str = ImportJobStatus.PENDING
    file_path: Optional[str] = None
    connection_details: Optional[Dict[str, Any]] = None
    rows_processed: int = 0
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SalesRecordDoc(BaseModel):
    id: Optional[Any] = Field(None, alias="_id")
    job_id: int
    company_id: int
    date: str  # YYYY-MM-DD
    product: str
    customer: str
    category: Optional[str] = None
    revenue: float
    profit: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
