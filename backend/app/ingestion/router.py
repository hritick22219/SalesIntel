import os
import shutil
import uuid
import time
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
import asyncpg
import pymysql

from database import get_db
from models import ImportJobStatus, UserDoc
from auth.dependencies import get_current_user
from worker.tasks import celery_app, async_process_import_job
from ingestion.schemas import PostgresImportRequest, MysqlImportRequest, JobStatusResponse

router = APIRouter(tags=["Ingestion"])

app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(app_dir)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(backend_dir, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def validate_postgres_conn(req: PostgresImportRequest) -> bool:
    try:
        conn = await asyncpg.connect(
            host=req.host,
            port=req.port,
            user=req.username,
            password=req.password,
            database=req.database,
            timeout=3,
        )
        await conn.close()
        return True
    except Exception:
        return False

def validate_mysql_conn(req: MysqlImportRequest) -> bool:
    try:
        conn = pymysql.connect(
            host=req.host,
            port=req.port,
            user=req.username,
            password=req.password,
            database=req.database,
            connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False

def generate_job_id() -> int:
    return int(time.time() * 1000)

@router.post("/upload/csv", status_code=status.HTTP_202_ACCEPTED)
async def upload_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV files are supported on this endpoint.",
        )

    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    job_id = generate_job_id()
    now = datetime.now(timezone.utc)
    job_doc = {
        "job_id": job_id,
        "user_id": getattr(current_user, "id", None),
        "company_id": current_user.company_id,
        "source_type": "csv",
        "status": ImportJobStatus.PENDING,
        "file_path": file_path,
        "rows_processed": 0,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.import_jobs.insert_one(job_doc)

    try:
        celery_app.send_task("process_import_job", args=[job_id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job_id)

    return {"job_id": job_id, "status": ImportJobStatus.PENDING}

@router.post("/upload/excel", status_code=status.HTTP_202_ACCEPTED)
async def upload_excel(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db),
):
    if not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only Excel files (.xlsx, .xls) are supported.",
        )

    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    job_id = generate_job_id()
    now = datetime.now(timezone.utc)
    job_doc = {
        "job_id": job_id,
        "user_id": getattr(current_user, "id", None),
        "company_id": current_user.company_id,
        "source_type": "excel",
        "status": ImportJobStatus.PENDING,
        "file_path": file_path,
        "rows_processed": 0,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.import_jobs.insert_one(job_doc)

    try:
        celery_app.send_task("process_import_job", args=[job_id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job_id)

    return {"job_id": job_id, "status": ImportJobStatus.PENDING}

@router.post("/import/postgres", status_code=status.HTTP_202_ACCEPTED)
async def import_postgres(
    req: PostgresImportRequest,
    background_tasks: BackgroundTasks,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db),
):
    is_valid = await validate_postgres_conn(req)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect to the external PostgreSQL database. Check connection parameters.",
        )

    job_id = generate_job_id()
    now = datetime.now(timezone.utc)
    job_doc = {
        "job_id": job_id,
        "user_id": getattr(current_user, "id", None),
        "company_id": current_user.company_id,
        "source_type": "postgres",
        "status": ImportJobStatus.PENDING,
        "connection_details": req.model_dump(),
        "rows_processed": 0,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.import_jobs.insert_one(job_doc)

    try:
        celery_app.send_task("process_import_job", args=[job_id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job_id)

    return {"job_id": job_id, "status": ImportJobStatus.PENDING}

@router.post("/import/mysql", status_code=status.HTTP_202_ACCEPTED)
async def import_mysql(
    req: MysqlImportRequest,
    background_tasks: BackgroundTasks,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db),
):
    is_valid = validate_mysql_conn(req)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect to the external MySQL database. Check connection parameters.",
        )

    job_id = generate_job_id()
    now = datetime.now(timezone.utc)
    job_doc = {
        "job_id": job_id,
        "user_id": getattr(current_user, "id", None),
        "company_id": current_user.company_id,
        "source_type": "mysql",
        "status": ImportJobStatus.PENDING,
        "connection_details": req.model_dump(),
        "rows_processed": 0,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.import_jobs.insert_one(job_doc)

    try:
        celery_app.send_task("process_import_job", args=[job_id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job_id)

    return {"job_id": job_id, "status": ImportJobStatus.PENDING}

@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: int,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db),
):
    job = await db.import_jobs.find_one({"job_id": job_id})
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found.",
        )

    if current_user.company_id != job.get("company_id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You do not have permission to view this job.",
        )

    created_at = job.get("created_at")
    updated_at = job.get("updated_at")
    c_str = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
    u_str = updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at)

    return JobStatusResponse(
        job_id=job["job_id"],
        source_type=job.get("source_type", "csv"),
        status=job.get("status", "pending"),
        rows_processed=job.get("rows_processed", 0),
        error_message=job.get("error_message"),
        created_at=c_str,
        updated_at=u_str,
    )
