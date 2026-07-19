from datetime import date, datetime, timezone
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status, Request
import numpy as np

from database import get_db
from models import UserDoc
from auth.dependencies import get_current_user
from analytics.cache import cache_response, invalidate_company_cache
from analytics.schemas import (
    DashboardKPIs,
    MonthlyTrendRow,
    ProductSummaryRow,
    CustomerSummaryRow,
    ForecastResult,
)

router = APIRouter(tags=["Analytics"])

@router.get("/dashboard", response_model=DashboardKPIs)
@cache_response(ttl_seconds=300)
async def get_dashboard_kpis(
    request: Request,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    match_stage = {"company_id": current_user.company_id}
    if start_date:
        match_stage["date"] = {"$gte": str(start_date)}
    if end_date:
        if "date" in match_stage:
            match_stage["date"]["$lte"] = str(end_date)
        else:
            match_stage["date"] = {"$lte": str(end_date)}

    pipeline = [
        {"$match": match_stage},
        {
            "$group": {
                "_id": None,
                "revenue": {"$sum": "$revenue"},
                "profit": {"$sum": "$profit"},
                "transactions": {"$sum": 1},
                "products": {"$addToSet": "$product"},
                "customers": {"$addToSet": "$customer"},
            }
        }
    ]

    cursor = db.sales_records.aggregate(pipeline)
    results = await cursor.to_list(length=1)

    if not results:
        return DashboardKPIs(
            total_revenue=0.0,
            total_profit=0.0,
            total_transactions=0,
            profit_margin_pct=0.0,
            unique_products=0,
            unique_customers=0
        )

    res = results[0]
    rev = float(res.get("revenue", 0.0))
    prof = float(res.get("profit", 0.0))
    margin = (prof / rev * 100.0) if rev > 0.0 else 0.0

    return DashboardKPIs(
        total_revenue=rev,
        total_profit=prof,
        total_transactions=int(res.get("transactions", 0)),
        profit_margin_pct=margin,
        unique_products=len(res.get("products", [])),
        unique_customers=len(res.get("customers", []))
    )

@router.get("/sales/monthly", response_model=List[MonthlyTrendRow])
@cache_response(ttl_seconds=300)
async def get_monthly_sales_trends(
    request: Request,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    match_stage = {"company_id": current_user.company_id}
    if start_date:
        match_stage["date"] = {"$gte": str(start_date)}
    if end_date:
        if "date" in match_stage:
            match_stage["date"]["$lte"] = str(end_date)
        else:
            match_stage["date"] = {"$lte": str(end_date)}

    pipeline = [
        {"$match": match_stage},
        {
            "$group": {
                "_id": {"$substr": ["$date", 0, 7]},
                "revenue": {"$sum": "$revenue"},
                "profit": {"$sum": "$profit"}
            }
        },
        {"$sort": {"_id": 1}}
    ]

    cursor = db.sales_records.aggregate(pipeline)
    results = await cursor.to_list(length=500)

    return [
        MonthlyTrendRow(
            month=doc["_id"],
            revenue=float(doc.get("revenue", 0.0)),
            profit=float(doc.get("profit", 0.0))
        )
        for doc in results if doc.get("_id")
    ]

@router.get("/top-products", response_model=List[ProductSummaryRow])
@cache_response(ttl_seconds=300)
async def get_top_products(
    request: Request,
    limit: int = 10,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    pipeline = [
        {"$match": {"company_id": current_user.company_id}},
        {
            "$group": {
                "_id": "$product",
                "revenue": {"$sum": "$revenue"},
                "profit": {"$sum": "$profit"},
                "sales_count": {"$sum": 1}
            }
        },
        {"$sort": {"revenue": -1}},
        {"$limit": limit}
    ]

    cursor = db.sales_records.aggregate(pipeline)
    results = await cursor.to_list(length=limit)

    return [
        ProductSummaryRow(
            product=doc["_id"],
            revenue=float(doc.get("revenue", 0.0)),
            profit=float(doc.get("profit", 0.0)),
            sales_count=int(doc.get("sales_count", 0))
        )
        for doc in results if doc.get("_id")
    ]

@router.get("/top-customers", response_model=List[CustomerSummaryRow])
@cache_response(ttl_seconds=300)
async def get_top_customers(
    request: Request,
    limit: int = 10,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    pipeline = [
        {"$match": {"company_id": current_user.company_id}},
        {
            "$group": {
                "_id": "$customer",
                "revenue": {"$sum": "$revenue"},
                "profit": {"$sum": "$profit"},
                "sales_count": {"$sum": 1}
            }
        },
        {"$sort": {"revenue": -1}},
        {"$limit": limit}
    ]

    cursor = db.sales_records.aggregate(pipeline)
    results = await cursor.to_list(length=limit)

    return [
        CustomerSummaryRow(
            customer=doc["_id"],
            revenue=float(doc.get("revenue", 0.0)),
            profit=float(doc.get("profit", 0.0)),
            sales_count=int(doc.get("sales_count", 0))
        )
        for doc in results if doc.get("_id")
    ]

@router.get("/forecast", response_model=ForecastResult)
@cache_response(ttl_seconds=300)
async def forecast_sales(
    request: Request,
    steps: int = 3,
    current_user: UserDoc = Depends(get_current_user),
    db: Any = Depends(get_db)
):
    pipeline = [
        {"$match": {"company_id": current_user.company_id}},
        {
            "$group": {
                "_id": {"$substr": ["$date", 0, 7]},
                "revenue": {"$sum": "$revenue"},
                "profit": {"$sum": "$profit"}
            }
        },
        {"$sort": {"_id": 1}}
    ]

    cursor = db.sales_records.aggregate(pipeline)
    results = await cursor.to_list(length=500)

    history = [
        MonthlyTrendRow(month=doc["_id"], revenue=float(doc.get("revenue", 0.0)), profit=float(doc.get("profit", 0.0)))
        for doc in results if doc.get("_id")
    ]

    forecasted = []
    method = "average_fallback"

    if len(history) < 3:
        avg_revenue = np.mean([h.revenue for h in history]) if history else 0.0
        avg_profit = np.mean([h.profit for h in history]) if history else 0.0
        
        last_year = datetime.now(timezone.utc).year
        last_month = datetime.now(timezone.utc).month

        for i in range(1, steps + 1):
            next_m = (last_month + i - 1) % 12 + 1
            next_y = last_year + (last_month + i - 1) // 12
            forecasted.append(
                MonthlyTrendRow(month=f"{next_y}-{next_m:02d}", revenue=avg_revenue, profit=avg_profit)
            )
    else:
        method = "linear_regression"
        x = np.arange(len(history))
        y_rev = np.array([h.revenue for h in history])
        y_prof = np.array([h.profit for h in history])

        slope_rev, intercept_rev = np.polyfit(x, y_rev, 1)
        slope_prof, intercept_prof = np.polyfit(x, y_prof, 1)

        last_date_str = history[-1].month
        try:
            last_date = datetime.strptime(last_date_str, "%Y-%m")
            last_year = last_date.year
            last_month = last_date.month
        except Exception:
            last_year = datetime.now(timezone.utc).year
            last_month = datetime.now(timezone.utc).month

        for i in range(1, steps + 1):
            next_x = len(history) + i - 1
            pred_rev = max(0.0, float(slope_rev * next_x + intercept_rev))
            pred_prof = max(0.0, float(slope_prof * next_x + intercept_prof))

            next_m = (last_month + i - 1) % 12 + 1
            next_y = last_year + (last_month + i - 1) // 12
            forecasted.append(
                MonthlyTrendRow(month=f"{next_y}-{next_m:02d}", revenue=pred_rev, profit=pred_prof)
            )

    return ForecastResult(historical=history, forecasted=forecasted, method=method)

@router.post("/cache/invalidate/{company_id}", status_code=status.HTTP_200_OK)
async def invalidate_cache(company_id: int):
    await invalidate_company_cache(company_id)
    return {"status": "success", "message": f"Cache for company {company_id} flushed."}
