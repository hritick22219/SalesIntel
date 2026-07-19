import asyncio
import os
import logging
from datetime import datetime, timezone
import httpx
from celery import Celery
from celery.utils.log import get_task_logger
from sqlalchemy import insert
from sqlalchemy.future import select

from config import settings
from database import SessionLocal, engine
from models import ImportJob, ImportJobStatus, SalesRecord, User
from worker.pipeline import load_data, clean_and_normalize, validate_records

# Initialize Celery app
celery_app = Celery("tasks", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

logger = get_task_logger(__name__)

# --- ETL Import Task ---

async def async_process_import_job(job_id: int):
    """Asynchronous core execution of the import pipeline."""
    async with SessionLocal() as db:
        # 1. Fetch Job details
        result = await db.execute(select(ImportJob).filter(ImportJob.id == job_id))
        job = result.scalars().first()

        if not job:
            logger.error(f"Import job {job_id} not found in database. Exiting.")
            return

        # 2. Update job status to Processing
        job.status = ImportJobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            logger.info(f"Loading raw data for job {job_id} ({job.source_type})...")
            df = load_data(job)

            logger.info(f"Cleaning and normalizing data headers for job {job_id}...")
            clean_df = clean_and_normalize(df)

            logger.info(f"Validating and typing records for job {job_id}...")
            valid_records = validate_records(clean_df)

            # 3. Insert records in bulk
            logger.info(f"Inserting {len(valid_records)} rows into sales_records table...")
            if valid_records:
                # Add metadata parameters to every record dict
                for record in valid_records:
                    record["job_id"] = job.id
                    record["company_id"] = job.company_id

                # Bulk insert via SQLAlchemy Core Statement execution (extremely fast)
                await db.execute(insert(SalesRecord), valid_records)

            # 4. Mark job as Completed
            job.status = ImportJobStatus.COMPLETED
            job.rows_processed = len(valid_records)
            job.error_message = None
            job.updated_at = datetime.now(timezone.utc)
            await db.commit()

            # Trigger analytics cache invalidation for the tenant
            try:
                from analytics.cache import invalidate_company_cache
                await invalidate_company_cache(job.company_id)
                logger.info(f"Successfully invalidated cache for company {job.company_id}")
            except Exception as cache_err:
                logger.warning(f"Failed to invalidate cache for company {job.company_id}: {cache_err}")

            logger.info(f"Successfully processed import job {job_id}. Imported {len(valid_records)} rows.")

            # Trigger notifications
            try:
                celery_app.send_task("send_notifications", args=[job.id])
                logger.info(f"Triggered success notifications for job {job.id}")
            except Exception as notify_err:
                logger.warning(f"Failed to trigger success notifications for job {job.id}: {notify_err}")
                asyncio.create_task(async_send_notifications(job.id))

        except Exception as e:
            # 5. Handle failures and log traceback
            await db.rollback()
            error_msg = str(e)
            logger.exception(f"Error occurred while processing import job {job_id}: {error_msg}")

            # Mark job as Failed
            job.status = ImportJobStatus.FAILED
            job.error_message = error_msg[:1000]  # truncate to fit column size
            job.updated_at = datetime.now(timezone.utc)
            await db.commit()

            # Trigger notifications
            try:
                celery_app.send_task("send_notifications", args=[job.id])
                logger.info(f"Triggered failure notifications for job {job.id}")
            except Exception as notify_err:
                logger.warning(f"Failed to trigger failure notifications for job {job.id}: {notify_err}")
                asyncio.create_task(async_send_notifications(job.id))

@celery_app.task(name="process_import_job")
def process_import_job(job_id: int):
    """Synchronous Celery entrypoint wrapper that spawns the async event loop."""
    logger.info(f"Celery task received for job {job_id}.")
    try:
        return asyncio.run(async_process_import_job(job_id))
    except Exception as e:
        logger.error(f"Failed to execute event loop for task {job_id}: {e}")
        raise

# --- Notifications Task ---

async def async_send_notifications(job_id: int):
    # Establish async DB session
    async with SessionLocal() as db:
        # 1. Fetch import job
        result = await db.execute(select(ImportJob).filter(ImportJob.id == job_id))
        job = result.scalars().first()
        if not job:
            logger.error(f"Import job {job_id} not found in database.")
            return

        # 2. Fetch user email
        user_result = await db.execute(select(User).filter(User.id == job.user_id))
        user = user_result.scalars().first()
        user_email = user.email if user else "unknown_user@example.com"

        # 3. Create mock email content
        subject = f"Sales Import Job #{job.id} - {str(job.status).split('.')[-1].upper()}"
        body = (
            f"Dear User,\n\n"
            f"Your import job with ID #{job.id} has completed processing.\n"
            f"Status: {str(job.status).split('.')[-1].upper()}\n"
            f"Source Type: {job.source_type}\n"
            f"Rows Processed: {job.rows_processed or 0}\n"
        )
        if job.error_message:
            body += f"Error Message: {job.error_message}\n"
        body += f"\nProcessed At: {datetime.now(timezone.utc).isoformat()}\n"

        # Write mock email to file (simulate SMTP sending)
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backend_dir = os.path.dirname(app_dir)
        emails_dir = os.path.join(backend_dir, "emails")
        os.makedirs(emails_dir, exist_ok=True)
        email_filepath = os.path.join(emails_dir, f"job_{job.id}.txt")
        
        with open(email_filepath, "w") as f:
            f.write(f"To: {user_email}\nSubject: {subject}\n\n{body}")
        
        logger.info(f"Mock email written to: {email_filepath}")

        # 4. Dispatch Webhook if WEBHOOK_URL is configured
        webhook_url = os.getenv("WEBHOOK_URL")
        if webhook_url:
            payload = {
                "event": "import.completed",
                "job_id": job.id,
                "company_id": job.company_id,
                "source_type": job.source_type,
                "status": str(job.status).split('.')[-1].upper(),
                "rows_processed": job.rows_processed or 0,
                "error_message": job.error_message,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            logger.info(f"Dispatching Webhook to {webhook_url}...")
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(webhook_url, json=payload, timeout=10.0)
                    logger.info(f"Webhook response status: {resp.status_code}")
            except Exception as webhook_err:
                logger.error(f"Failed to deliver webhook to {webhook_url}: {webhook_err}")

@celery_app.task(name="send_notifications")
def send_notifications(job_id: int):
    """Celery background wrapper to invoke notifications asynchronously."""
    try:
        return asyncio.run(async_send_notifications(job_id))
    except Exception as e:
        logger.error(f"Failed to run notification task {job_id}: {e}")
        raise
