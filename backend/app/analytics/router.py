from datetime import date, datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession
import numpy as np

from database import get_db
from models import SalesRecord, User
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    query = select(
        func.coalesce(func.sum(SalesRecord.revenue), 0.0).label("revenue"),
        func.coalesce(func.sum(SalesRecord.profit), 0.0).label("profit"),
        func.count(SalesRecord.id).label("transactions"),
        func.count(func.distinct(SalesRecord.product)).label("products"),
        func.count(func.distinct(SalesRecord.customer)).label("customers")
    ).filter(SalesRecord.company_id == current_user.company_id)

    if start_date:
        query = query.filter(SalesRecord.date >= start_date)
    if end_date:
        query = query.filter(SalesRecord.date <= end_date)

    res = await db.execute(query)
    row = res.first()

    rev = float(row.revenue)
    prof = float(row.profit)
    margin = (prof / rev * 100.0) if rev > 0.0 else 0.0

    return DashboardKPIs(
        total_revenue=rev,
        total_profit=prof,
        total_transactions=int(row.transactions),
        profit_margin_pct=margin,
        unique_products=int(row.products),
        unique_customers=int(row.customers)
    )

@router.get("/sales/monthly", response_model=List[MonthlyTrendRow])
@cache_response(ttl_seconds=300)
async def get_monthly_sales_trends(
    request: Request,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Dialect-agnostic grouping (SQLite vs Postgres)
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        month_group = func.strftime("%Y-%m", SalesRecord.date)
    else:
        month_group = func.to_char(SalesRecord.date, "YYYY-MM")

    query = select(
        month_group.label("month"),
        func.coalesce(func.sum(SalesRecord.revenue), 0.0).label("revenue"),
        func.coalesce(func.sum(SalesRecord.profit), 0.0).label("profit")
    ).filter(SalesRecord.company_id == current_user.company_id)

    if start_date:
        query = query.filter(SalesRecord.date >= start_date)
    if end_date:
        query = query.filter(SalesRecord.date <= end_date)

    query = query.group_by(month_group).order_by(month_group)
    res = await db.execute(query)

    return [
        MonthlyTrendRow(month=row.month, revenue=float(row.revenue), profit=float(row.profit))
        for row in res.all()
    ]

@router.get("/top-products", response_model=List[ProductSummaryRow])
@cache_response(ttl_seconds=300)
async def get_top_products(
    request: Request,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    query = select(
        SalesRecord.product,
        func.coalesce(func.sum(SalesRecord.revenue), 0.0).label("revenue"),
        func.coalesce(func.sum(SalesRecord.profit), 0.0).label("profit"),
        func.count(SalesRecord.id).label("sales_count")
    ).filter(SalesRecord.company_id == current_user.company_id) \
     .group_by(SalesRecord.product) \
     .order_by(desc("revenue")) \
     .limit(limit)

    res = await db.execute(query)
    return [
        ProductSummaryRow(
            product=row.product,
            revenue=float(row.revenue),
            profit=float(row.profit),
            sales_count=int(row.sales_count)
        )
        for row in res.all()
    ]

@router.get("/top-customers", response_model=List[CustomerSummaryRow])
@cache_response(ttl_seconds=300)
async def get_top_customers(
    request: Request,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    query = select(
        SalesRecord.customer,
        func.coalesce(func.sum(SalesRecord.revenue), 0.0).label("revenue"),
        func.coalesce(func.sum(SalesRecord.profit), 0.0).label("profit"),
        func.count(SalesRecord.id).label("sales_count")
    ).filter(SalesRecord.company_id == current_user.company_id) \
     .group_by(SalesRecord.customer) \
     .order_by(desc("revenue")) \
     .limit(limit)

    res = await db.execute(query)
    return [
        CustomerSummaryRow(
            customer=row.customer,
            revenue=float(row.revenue),
            profit=float(row.profit),
            sales_count=int(row.sales_count)
        )
        for row in res.all()
    ]

@router.get("/forecast", response_model=ForecastResult)
@cache_response(ttl_seconds=300)
async def forecast_sales(
    request: Request,
    steps: int = 3,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 1. Fetch monthly history
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        month_group = func.strftime("%Y-%m", SalesRecord.date)
    else:
        month_group = func.to_char(SalesRecord.date, "YYYY-MM")

    query = select(
        month_group.label("month"),
        func.coalesce(func.sum(SalesRecord.revenue), 0.0).label("revenue"),
        func.coalesce(func.sum(SalesRecord.profit), 0.0).label("profit")
    ).filter(SalesRecord.company_id == current_user.company_id) \
     .group_by(month_group) \
     .order_by(month_group)

    res = await db.execute(query)
    history = [
        MonthlyTrendRow(month=row.month, revenue=float(row.revenue), profit=float(row.profit))
        for row in res.all()
    ]

    forecasted = []
    method = "average_fallback"

    if len(history) < 3:
        # Fallback: Flat forecast using average of history
        avg_revenue = np.mean([h.revenue for h in history]) if history else 0.0
        avg_profit = np.mean([h.profit for h in history]) if history else 0.0
        
        # Forecast steps months
        last_year = datetime.now(timezone.utc).year
        last_month = datetime.now(timezone.utc).month

        for i in range(1, steps + 1):
            next_m = (last_month + i - 1) % 12 + 1
            next_y = last_year + (last_month + i - 1) // 12
            forecasted.append(
                MonthlyTrendRow(month=f"{next_y}-{next_m:02d}", revenue=avg_revenue, profit=avg_profit)
            )
    else:
        # Linear Regression using Numpy Least Squares Polyfit
        method = "linear_regression"
        x = np.arange(len(history))
        y_rev = np.array([h.revenue for h in history])
        y_prof = np.array([h.profit for h in history])

        slope_rev, intercept_rev = np.polyfit(x, y_rev, 1)
        slope_prof, intercept_prof = np.polyfit(x, y_prof, 1)

        # Retrieve year and month from the last recorded month in history
        last_date_str = history[-1].month
        last_date = datetime.strptime(last_date_str, "%Y-%m")
        last_year = last_date.year
        last_month = last_date.month

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
    """Internal webhook endpoint to flush Redis cache keys for a tenant."""
    await invalidate_company_cache(company_id)
    return {"status": "success", "message": f"Cache for company {company_id} flushed."}
