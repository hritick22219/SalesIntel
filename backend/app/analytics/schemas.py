from pydantic import BaseModel
from typing import List

class DashboardKPIs(BaseModel):
    total_revenue: float
    total_profit: float
    total_transactions: int
    profit_margin_pct: float
    unique_products: int
    unique_customers: int

class MonthlyTrendRow(BaseModel):
    month: str
    revenue: float
    profit: float

class ProductSummaryRow(BaseModel):
    product: str
    revenue: float
    profit: float
    sales_count: int

class CustomerSummaryRow(BaseModel):
    customer: str
    revenue: float
    profit: float
    sales_count: int

class ForecastResult(BaseModel):
    historical: List[MonthlyTrendRow]
    forecasted: List[MonthlyTrendRow]
    method: str
