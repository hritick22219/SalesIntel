import asyncio
import os
import logging
from datetime import datetime, date, timezone
import httpx
import pandas as pd
from celery import Celery
from celery.utils.log import get_task_logger

from config import settings
from database import get_mongo_db
from models import ImportJobStatus
from worker.pipeline import load_data, clean_and_normalize, validate_records

celery_app = Celery("tasks", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

logger = get_task_logger(__name__)

def sanitize_record(record: dict, job_id: int, company_id: Any) -> dict:
    """Sanitize numpy types and dates for MongoDB BSON compatibility."""
    clean = {}
    clean["job_id"] = int(job_id)
    clean["company_id"] = int(company_id) if company_id is not None else None

    for k, v in record.items():
        if k in ("job_id", "company_id"):
            continue
        if pd.isna(v):
            clean[k] = None
        elif isinstance(v, (date, datetime)):
            clean[k] = v.isoformat()
        elif hasattr(v, "item"):  # numpy scalars
            clean[k] = v.item()
        else:
            clean[k] = v
    return clean

async def async_process_import_job(job_id: int):
    db = get_mongo_db()
    job = await db.import_jobs.find_one({"job_id": job_id})

    if not job:
        logger.error(f"Import job {job_id} not found in database. Exiting.")
        return

    now = datetime.now(timezone.utc)
    await db.import_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"status": ImportJobStatus.PROCESSING, "updated_at": now}}
    )

    try:
        class JobWrapper:
            def __init__(self, data):
                self.source_type = data.get("source_type")
                self.file_path = data.get("file_path")
                self.connection_details = data.get("connection_details")

        logger.info(f"Loading raw data for job {job_id} ({job.get('source_type')})...")
        df = load_data(JobWrapper(job))

        logger.info(f"Cleaning and normalizing data headers for job {job_id}...")
        clean_df = clean_and_normalize(df)

        logger.info(f"Validating and typing records for job {job_id}...")
        valid_records = validate_records(clean_df)

        logger.info(f"Inserting {len(valid_records)} rows into sales_records collection...")
        if valid_records:
            company_id = job.get("company_id")
            sanitized_records = [sanitize_record(r, job_id, company_id) for r in valid_records]
            await db.sales_records.insert_many(sanitized_records)

        now_comp = datetime.now(timezone.utc)
        await db.import_jobs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": ImportJobStatus.COMPLETED,
                    "rows_processed": len(valid_records),
                    "error_message": None,
                    "updated_at": now_comp
                }
            }
        )

        company_id = job.get("company_id")
        if company_id:
            try:
                from analytics.cache import invalidate_company_cache
                await invalidate_company_cache(company_id)
                logger.info(f"Successfully invalidated cache for company {company_id}")
            except Exception as cache_err:
                logger.warning(f"Failed to invalidate cache for company {company_id}: {cache_err}")

        logger.info(f"Successfully processed import job {job_id}. Imported {len(valid_records)} rows.")

        try:
            celery_app.send_task("send_notifications", args=[job_id])
        except Exception:
            asyncio.create_task(async_send_notifications(job_id))

    except Exception as e:
        error_msg = str(e)
        logger.exception(f"Error occurred while processing import job {job_id}: {error_msg}")

        now_fail = datetime.now(timezone.utc)
        await db.import_jobs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": ImportJobStatus.FAILED,
                    "error_message": error_msg[:1000],
                    "updated_at": now_fail
                }
            }
        )

        try:
            celery_app.send_task("send_notifications", args=[job_id])
        except Exception:
            asyncio.create_task(async_send_notifications(job_id))

@celery_app.task(name="process_import_job")
def process_import_job(job_id: int):
    logger.info(f"Celery task received for job {job_id}.")
    try:
        return asyncio.run(async_process_import_job(job_id))
    except Exception as e:
        logger.error(f"Failed to execute event loop for task {job_id}: {e}")
        raise

async def async_send_notifications(job_id: int):
    db = get_mongo_db()
    job = await db.import_jobs.find_one({"job_id": job_id})
    if not job:
        logger.error(f"Import job {job_id} not found in database.")
        return

    user_email = "unknown_user@example.com"
    user_id = job.get("user_id")
    if user_id:
        user = await db.users.find_one({"_id": user_id})
        if user:
            user_email = user.get("email", user_email)

    job_status = str(job.get("status", "completed")).upper()
    subject = f"Sales Import Job #{job_id} - {job_status}"
    body = (
        f"Dear User,\n\n"
        f"Your import job with ID #{job_id} has completed processing.\n"
        f"Status: {job_status}\n"
        f"Source Type: {job.get('source_type')}\n"
        f"Rows Processed: {job.get('rows_processed', 0)}\n"
    )
    if job.get("error_message"):
        body += f"Error Message: {job.get('error_message')}\n"
    body += f"\nProcessed At: {datetime.now(timezone.utc).isoformat()}\n"

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backend_dir = os.path.dirname(app_dir)
    emails_dir = os.path.join(backend_dir, "emails")
    os.makedirs(emails_dir, exist_ok=True)
    email_filepath = os.path.join(emails_dir, f"job_{job_id}.txt")
    
    with open(email_filepath, "w") as f:
        f.write(f"To: {user_email}\nSubject: {subject}\n\n{body}")
    
    logger.info(f"Mock email written to: {email_filepath}")

    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        payload = {
            "event": "import.completed",
            "job_id": job_id,
            "company_id": job.get("company_id"),
            "source_type": job.get("source_type"),
            "status": job_status,
            "rows_processed": job.get("rows_processed", 0),
            "error_message": job.get("error_message"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook_url, json=payload, timeout=10.0)
                logger.info(f"Webhook response status: {resp.status_code}")
        except Exception as webhook_err:
            logger.error(f"Failed to deliver webhook to {webhook_url}: {webhook_err}")

@celery_app.task(name="send_notifications")
def send_notifications(job_id: int):
    try:
        return asyncio.run(async_send_notifications(job_id))
    except Exception as e:
        logger.error(f"Failed to run notification task {job_id}: {e}")
        raise
