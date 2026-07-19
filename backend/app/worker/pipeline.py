from datetime import datetime, date
import os
from typing import List, Dict, Any, Optional

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from models import ImportJob, ImportJobStatus

# Synonym mappings for automatic header detection
COLUMN_SYNONYMS = {
    "date": ["date", "sale_date", "transaction_date", "timestamp", "sold_at", "trans_date"],
    "product": ["product", "item", "product_name", "title", "sku", "product_title"],
    "customer": ["customer", "client", "buyer", "customer_name", "company_name", "client_name"],
    "category": ["category", "type", "group", "product_category", "class", "dept", "department"],
    "revenue": ["revenue", "sales", "amount", "price", "subtotal", "total", "total_sales", "sales_amount"],
    "profit": ["profit", "margin", "gain", "net_profit", "earnings"]
}

class SalesRecordValidator(BaseModel):
    date: date
    product: str = Field(..., min_length=1)
    customer: str = Field(..., min_length=1)
    category: Optional[str] = None
    revenue: float
    profit: float

# --- Pipeline Steps ---

def load_data(job: ImportJob) -> pd.DataFrame:
    """Load raw data into a Pandas DataFrame based on the job type."""
    if job.source_type == "csv":
        if not job.file_path or not os.path.exists(job.file_path):
            raise FileNotFoundError(f"CSV file not found at path: {job.file_path}")
        return pd.read_csv(job.file_path)

    elif job.source_type == "excel":
        if not job.file_path or not os.path.exists(job.file_path):
            raise FileNotFoundError(f"Excel file not found at path: {job.file_path}")
        return pd.read_excel(job.file_path)

    elif job.source_type == "postgres":
        d = job.connection_details
        if not d:
            raise ValueError("Postgres connection details are missing.")
        conn_str = f"postgresql://{d['username']}:{d['password']}@{d['host']}:{d['port']}/{d['database']}"
        query = d.get("query") or f"SELECT * FROM {d['table_name']}"
        return pd.read_sql_query(query, conn_str)

    elif job.source_type == "mysql":
        d = job.connection_details
        if not d:
            raise ValueError("MySQL connection details are missing.")
        conn_str = f"mysql+pymysql://{d['username']}:{d['password']}@{d['host']}:{d['port']}/{d['database']}"
        query = d.get("query") or f"SELECT * FROM {d['table_name']}"
        return pd.read_sql_query(query, conn_str)

    else:
        raise ValueError(f"Unsupported data source type: {job.source_type}")

def clean_and_normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize headers, detect fields using synonyms, clean values, and deduplicate."""
    # 1. Clean column headers: lowercase, trim, replace spaces with underscores
    df.columns = [str(col).lower().strip().replace(" ", "_") for col in df.columns]

    # 2. Map columns to standard fields
    mapped_cols = {}
    for std_field, synonyms in COLUMN_SYNONYMS.items():
        for col in df.columns:
            if col in synonyms:
                mapped_cols[std_field] = col
                break

    # 3. Check for missing required columns
    required_fields = ["date", "product", "customer", "revenue", "profit"]
    missing_fields = [f for f in required_fields if f not in mapped_cols]
    if missing_fields:
        raise ValueError(
            f"Unable to auto-detect columns for required fields: {', '.join(missing_fields)}. "
            f"Ensure headers match expected names (e.g. date, product, customer, revenue, profit)."
        )

    # 4. Construct clean dataframe with standard columns
    clean_df = pd.DataFrame()
    for std_field in ["date", "product", "customer", "category", "revenue", "profit"]:
        source_col = mapped_cols.get(std_field)
        if source_col is not None:
            clean_df[std_field] = df[source_col]
        else:
            clean_df[std_field] = None

    # 5. Normalize data types and clean string whitespace before deduplication
    if "date" in clean_df.columns:
        clean_df["date"] = pd.to_datetime(clean_df["date"], errors="coerce").dt.date

    for col in ["product", "customer", "category"]:
        if col in clean_df.columns:
            clean_df[col] = clean_df[col].apply(lambda x: str(x).strip() if pd.notna(x) else None)

    for col in ["revenue", "profit"]:
        if col in clean_df.columns:
            clean_df[col] = pd.to_numeric(clean_df[col], errors="coerce")

    # 6. Drop rows where critical fields are NaN (which includes rows that failed type conversion)
    clean_df.dropna(subset=required_fields, inplace=True)

    # 7. Deduplicate
    clean_df.drop_duplicates(inplace=True)

    return clean_df

def validate_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Validate records against Pydantic schema."""
    valid_records = []
    
    for idx, row in df.iterrows():
        try:
            validator = SalesRecordValidator(
                date=row["date"],
                product=row["product"],
                customer=row["customer"],
                category=row["category"] if pd.notna(row["category"]) else None,
                revenue=row["revenue"],
                profit=row["profit"]
            )
            valid_records.append(validator.model_dump())
        except (ValidationError, ValueError, TypeError):
            continue

    if not valid_records and len(df) > 0:
        raise ValueError("All rows in the dataset failed validation checks.")
        
    return valid_records
