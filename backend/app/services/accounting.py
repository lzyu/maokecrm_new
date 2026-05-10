from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, func, select

from app.db import engine
from app.models.customer_course_enrollment import CustomerCourseEnrollment
from app.models.order import Order

SALES_SPENT_ACCOUNTING_TYPE = "sales_spent"
ADMIN_WRITEOFF_ACCOUNTING_TYPE = "admin_writeoff"


async def ensure_accounting_type_schema() -> None:
    async with engine.begin() as conn:
        import app.models  # noqa: F401

        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(
            text(
                "ALTER TABLE customer_course_enrollments "
                "ADD COLUMN IF NOT EXISTS accounting_type VARCHAR(30)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_customer_course_enrollments_accounting_type "
                "ON customer_course_enrollments (accounting_type)"
            )
        )
        await conn.execute(
            text(
                "UPDATE customer_course_enrollments "
                "SET accounting_type = CASE "
                "WHEN status IN ('admin_marked_completed', 'admin_marked_completed_refunded') "
                f"THEN '{ADMIN_WRITEOFF_ACCOUNTING_TYPE}' "
                f"ELSE '{SALES_SPENT_ACCOUNTING_TYPE}' "
                "END "
                "WHERE accounting_type IS NULL"
            )
        )


async def get_customer_sales_spent(db: AsyncSession, customer_id: str) -> Decimal:
    result = await db.execute(
        select(func.coalesce(func.sum(Order.amount - Order.refund_total), 0))
        .select_from(CustomerCourseEnrollment)
        .join(Order, Order.id == CustomerCourseEnrollment.order_id)
        .where(
            CustomerCourseEnrollment.customer_id == customer_id,
            CustomerCourseEnrollment.accounting_type == SALES_SPENT_ACCOUNTING_TYPE,
        )
    )
    return Decimal(result.scalar() or 0)


async def get_customer_admin_writeoff_total(db: AsyncSession, customer_id: str) -> Decimal:
    result = await db.execute(
        select(func.coalesce(func.sum(Order.amount - Order.refund_total), 0))
        .select_from(CustomerCourseEnrollment)
        .join(Order, Order.id == CustomerCourseEnrollment.order_id)
        .where(
            CustomerCourseEnrollment.customer_id == customer_id,
            CustomerCourseEnrollment.accounting_type == ADMIN_WRITEOFF_ACCOUNTING_TYPE,
        )
    )
    return Decimal(result.scalar() or 0)


async def get_sales_spent_in_period(
    db: AsyncSession,
    customer_id: str,
    created_at_from: datetime,
    created_at_to: datetime,
) -> Decimal:
    result = await db.execute(
        select(func.coalesce(func.sum(Order.amount - Order.refund_total), 0))
        .select_from(CustomerCourseEnrollment)
        .join(Order, Order.id == CustomerCourseEnrollment.order_id)
        .where(
            CustomerCourseEnrollment.customer_id == customer_id,
            CustomerCourseEnrollment.accounting_type == SALES_SPENT_ACCOUNTING_TYPE,
            Order.created_at >= created_at_from,
            Order.created_at < created_at_to,
            Order.refunded_at.is_(None),
        )
    )
    return Decimal(result.scalar() or 0)
