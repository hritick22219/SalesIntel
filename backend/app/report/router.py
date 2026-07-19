from datetime import date
import io
import re
from typing import Optional, Any
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import pandas as pd
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

from database import get_db
from models import UserDoc
from auth.dependencies import get_current_user

router = APIRouter(tags=["Reports"])

async def get_filtered_records(
    db: Any,
    company_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    product: Optional[str] = None,
    customer: Optional[str] = None,
):
    query: dict = {"company_id": company_id}

    if start_date:
        query["date"] = {"$gte": str(start_date)}
    if end_date:
        if "date" in query:
            query["date"]["$lte"] = str(end_date)
        else:
            query["date"] = {"$lte": str(end_date)}

    if product:
        query["product"] = {"$regex": re.escape(product), "$options": "i"}
    if customer:
        query["customer"] = {"$regex": re.escape(customer), "$options": "i"}

    cursor = db.sales_records.find(query).sort("date", -1)
    records = await cursor.to_list(length=10000)

    data = []
    for r in records:
        data.append({
            "Date": r.get("date", ""),
            "Product": r.get("product", ""),
            "Customer": r.get("customer", ""),
            "Category": r.get("category", "") or "",
            "Revenue": float(r.get("revenue", 0.0)),
            "Profit": float(r.get("profit", 0.0))
        })

    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(columns=["Date", "Product", "Customer", "Category", "Revenue", "Profit"])
    return df

@router.get("/csv")
async def export_csv(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    product: Optional[str] = None,
    customer: Optional[str] = None,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    df = await get_filtered_records(db, current_user.company_id, start_date, end_date, product, customer)

    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_report.csv"}
    )

@router.get("/excel")
async def export_excel(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    product: Optional[str] = None,
    customer: Optional[str] = None,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    df = await get_filtered_records(db, current_user.company_id, start_date, end_date, product, customer)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sales Records")

        worksheet = writer.sheets["Sales Records"]

        header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        regular_font = Font(name="Segoe UI", size=10)
        
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        zebra_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
        
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")
        right_align = Alignment(horizontal="right", vertical="center")

        for col_idx in range(1, len(df.columns) + 1):
            cell = worksheet.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align

        currency_format = "$#,##0.00"
        for row_idx in range(2, len(df) + 2):
            worksheet.cell(row=row_idx, column=1).alignment = center_align
            worksheet.cell(row=row_idx, column=2).alignment = left_align
            worksheet.cell(row=row_idx, column=3).alignment = left_align
            worksheet.cell(row=row_idx, column=4).alignment = left_align
            
            rev_cell = worksheet.cell(row=row_idx, column=5)
            rev_cell.number_format = currency_format
            rev_cell.alignment = right_align
            
            prof_cell = worksheet.cell(row=row_idx, column=6)
            prof_cell.number_format = currency_format
            prof_cell.alignment = right_align

            if row_idx % 2 == 0:
                for col_idx in range(1, len(df.columns) + 1):
                    worksheet.cell(row=row_idx, column=col_idx).fill = zebra_fill

            for col_idx in range(1, len(df.columns) + 1):
                worksheet.cell(row=row_idx, column=col_idx).font = regular_font

        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = cell.value
                if isinstance(val, float):
                    val_len = len(f"${val:,.2f}")
                else:
                    val_len = len(str(val or ''))
                if val_len > max_len:
                    max_len = val_len
            worksheet.column_dimensions[col_letter].width = max(max_len + 4, 11)

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sales_report.xlsx"}
    )
