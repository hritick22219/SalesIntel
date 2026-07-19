import os
import shutil
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import asyncpg
import pymysql

from database import get_db
from models import ImportJob, ImportJobStatus, User
from auth.dependencies import get_current_user
from worker.tasks import celery_app, async_process_import_job
from ingestion.schemas import PostgresImportRequest, MysqlImportRequest, JobStatusResponse

router = APIRouter(tags=["Ingestion"])

# Upload directory setup (backend/uploads)
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(app_dir)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(backend_dir, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Helper Functions ---
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

# --- Routes ---

@router.post("/upload/csv", status_code=status.HTTP_202_ACCEPTED)
async def upload_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only CSV files are supported on this endpoint.",
        )

    # Save raw file to disk
    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Register ImportJob in database
    job = ImportJob(
        user_id=current_user.id,
        company_id=current_user.company_id,
        source_type="csv",
        status=ImportJobStatus.PENDING,
        file_path=file_path,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue task via Celery (with local background task fallback)
    try:
        celery_app.send_task("process_import_job", args=[job.id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job.id)

    return {"job_id": job.id, "status": job.status}

@router.post("/upload/excel", status_code=status.HTTP_202_ACCEPTED)
async def upload_excel(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only Excel files (.xlsx, .xls) are supported.",
        )

    # Save raw file to disk
    file_id = str(uuid.uuid4())
    filename = f"{file_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Register ImportJob in database
    job = ImportJob(
        user_id=current_user.id,
        company_id=current_user.company_id,
        source_type="excel",
        status=ImportJobStatus.PENDING,
        file_path=file_path,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue task via Celery (with local background task fallback)
    try:
        celery_app.send_task("process_import_job", args=[job.id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job.id)

    return {"job_id": job.id, "status": job.status}

@router.post("/import/postgres", status_code=status.HTTP_202_ACCEPTED)
async def import_postgres(
    req: PostgresImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate DB credentials connection
    is_valid = await validate_postgres_conn(req)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect to the external PostgreSQL database. Check connection parameters.",
        )

    # Save job details
    job = ImportJob(
        user_id=current_user.id,
        company_id=current_user.company_id,
        source_type="postgres",
        status=ImportJobStatus.PENDING,
        connection_details=req.model_dump(),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue task via Celery (with local background task fallback)
    try:
        celery_app.send_task("process_import_job", args=[job.id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job.id)

    return {"job_id": job.id, "status": job.status}

@router.post("/import/mysql", status_code=status.HTTP_202_ACCEPTED)
async def import_mysql(
    req: MysqlImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate MySQL credentials
    is_valid = validate_mysql_conn(req)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect to the external MySQL database. Check connection parameters.",
        )

    # Save job details
    job = ImportJob(
        user_id=current_user.id,
        company_id=current_user.company_id,
        source_type="mysql",
        status=ImportJobStatus.PENDING,
        connection_details=req.model_dump(),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue task via Celery (with local background task fallback)
    try:
        celery_app.send_task("process_import_job", args=[job.id])
    except Exception:
        background_tasks.add_task(async_process_import_job, job.id)

    return {"job_id": job.id, "status": job.status}

@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ImportJob).filter(ImportJob.id == job_id))
    job = result.scalars().first()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found.",
        )

    # Security check: Ensure user can only poll jobs belonging to their company
    if current_user.company_id != job.company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You do not have permission to view this job.",
        )

    return JobStatusResponse(
        job_id=job.id,
        source_type=job.source_type,
        status=job.status,
        rows_processed=job.rows_processed,
        error_message=job.error_message,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )
