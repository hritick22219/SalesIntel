from pydantic import BaseModel, Field
from typing import Optional

class PostgresImportRequest(BaseModel):
    host: str = Field(..., json_schema_extra={"example": "db"})
    port: int = Field(5432, json_schema_extra={"example": 5432})
    username: str = Field(..., json_schema_extra={"example": "postgres"})
    password: str = Field(..., json_schema_extra={"example": "postgres"})
    database: str = Field(..., json_schema_extra={"example": "source_db"})
    table_name: str = Field(..., json_schema_extra={"example": "sales_data"})
    query: Optional[str] = Field(None, json_schema_extra={"example": "SELECT * FROM sales_data"})

class MysqlImportRequest(BaseModel):
    host: str = Field(..., json_schema_extra={"example": "localhost"})
    port: int = Field(3306, json_schema_extra={"example": 3306})
    username: str = Field(..., json_schema_extra={"example": "root"})
    password: str = Field(..., json_schema_extra={"example": "password"})
    database: str = Field(..., json_schema_extra={"example": "source_db"})
    table_name: str = Field(..., json_schema_extra={"example": "sales_data"})
    query: Optional[str] = Field(None, json_schema_extra={"example": "SELECT * FROM sales_data"})

class JobStatusResponse(BaseModel):
    job_id: int
    source_type: str
    status: str
    rows_processed: int
    error_message: Optional[str]
    created_at: str
    updated_at: str
