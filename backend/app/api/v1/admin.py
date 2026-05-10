from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy import and_
from sqlalchemy.orm import aliased

from app.api.v1.consultant import (
    ConsultantBadgeOut,
    ProductOut,
    TagOut,
    _build_consultants,
    _build_products,
    _build_tags,
)
from app.core.deps import get_db, require_role
from datetime import date

from sqlmodel import func

from app.models.consultant_customer import ConsultantCustomer
from app.models.consultation_log import ConsultationLog
from app.models.customer import Customer
from app.models.link_account import LinkAccount
from app.models.order import CustomerProduct
from app.models.order import Order
from app.models.product import Product
from app.models.tag import CustomerTag, Tag, TagCategory
from app.models.user import User
from app.models.customer_course_enrollment import CustomerCourseEnrollment
from app.models.tuition_gift_request import TuitionGiftRequest
from app.models.audit_log import AuditLog
from app.services.accounting import (
    ADMIN_WRITEOFF_ACCOUNTING_TYPE,
    ensure_accounting_type_schema,
    get_customer_admin_writeoff_total,
    get_customer_sales_spent,
    get_sales_spent_in_period,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _ensure_admin_writeoff_enrollment(enrollment: CustomerCourseEnrollment) -> None:
    if enrollment.accounting_type != ADMIN_WRITEOFF_ACCOUNTING_TYPE:
        raise HTTPException(403, "仅允许管理员操作销课记录")


class AdminPoolItemOut(BaseModel):
    pool_id: str | None
    customer_id: str
    customer_name: str
    customer_info: str
    tags: list[TagOut]
    sales_name: str | None
    entered_days: int
    claim_status: str
    pool_entered_at: str


class AdminPoolSummaryOut(BaseModel):
    pending: int
    active: int
    ended: int


class AdminPoolOut(BaseModel):
    summary: AdminPoolSummaryOut
    items: list[AdminPoolItemOut]


class AdminCustomerItemOut(BaseModel):
    customer_id: str
    customer_name: str
    phone: str
    customer_info: str
    added_date: str
    other_contact: str | None
    wechat_name: str | None
    sales_name: str | None
    tags: list[TagOut]
    products: list[ProductOut]
    consultants: list[ConsultantBadgeOut]
    asset_status: str
    created_at: str


class AdminDashboardOut(BaseModel):
    sales_capacity: list[dict]
    source_channels: list[dict]
    product_deals: list[dict]
    consultant_delivery: list[dict]


class AdminCourseStatusUpdateIn(BaseModel):
    status: str
    refund_amount: float | None = None


class TuitionGiftRequestOut(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    sales_user_name: str | None
    amount: float
    sales_note: str | None
    admin_note: str | None
    status: str
    reviewed_by_user_name: str | None = None
    reviewed_at: str | None = None
    created_at: str


class TuitionGiftReviewIn(BaseModel):
    admin_note: str | None = None


class AdminWriteoffCourseIn(BaseModel):
    product_id: str
    amount: float
    status: str
    note: str | None = None


class AdminWriteoffCourseOut(BaseModel):
    enrollment_id: str
    product_name: str
    amount_paid: float
    refunded_amount: float
    status: str
    created_at: str


class AdminTuitionWriteoffCustomerOut(BaseModel):
    customer_id: str
    customer_name: str
    phone: str
    sales_name: str | None
    tags: list[TagOut]
    consult_count: int
    wechat_name: str | None
    sales_note: str | None
    total_spent: float
    gifted_tuition_amount: float
    tuition_balance: float
    pending_gift_request_count: int
    latest_pending_gift_note: str | None
    courses: list[AdminWriteoffCourseOut]


class AdminTuitionWriteoffSummaryOut(BaseModel):
    total_gifted: float
    total_spent: float
    last_month_spent: float
    total_balance: float
    total_pending: int


class AdminAuditLogItemOut(BaseModel):
    id: str
    created_at: str
    action: str
    amount_delta: float | None
    note: str | None
    resource_type: str
    resource_id: str
    changes: str | None
    related_event_id: str | None
    operator_user_id: str | None
    operator_role: str | None
    operator_name: str | None
    customer_id: str | None
    customer_name: str | None


class AdminAuditLogListOut(BaseModel):
    total: int
    items: list[AdminAuditLogItemOut]


ADMIN_COURSE_STATUSES = {
    "admin_marked_completed",
    "admin_marked_completed_refunded",
}

AUDIT_ACTIONS = {
    "admin_writeoff_course_created",
    "admin_writeoff_course_refunded",
    "admin_writeoff_course_refund_reverted",
    "gift_request_approved",
    "gift_request_rejected",
}

AUDIT_ACTION_LABELS = {
    "admin_writeoff_course_created": "新增管理员销课",
    "admin_writeoff_course_refunded": "管理员销课退款",
    "gift_request_approved": "赠送学费通过",
    "gift_request_rejected": "赠送学费驳回",
}


@router.get("/tuition-gift-requests", response_model=list[TuitionGiftRequestOut])
async def list_tuition_gift_requests(
    status: str = Query("pending"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(400, "invalid status")

    reviewer = aliased(User)
    rows = await db.execute(
        select(TuitionGiftRequest, Customer, User.name, reviewer.name)
        .join(Customer, TuitionGiftRequest.customer_id == Customer.id)
        .join(User, TuitionGiftRequest.sales_user_id == User.id, isouter=True)
        .join(reviewer, TuitionGiftRequest.reviewed_by_user_id == reviewer.id, isouter=True)
        .where(TuitionGiftRequest.status == status)
        .order_by(TuitionGiftRequest.created_at.desc())
    )
    return [
        TuitionGiftRequestOut(
            id=req.id,
            customer_id=customer.id,
            customer_name=customer.name,
            sales_user_name=sales_name,
            amount=req.amount,
            sales_note=req.sales_note,
            admin_note=req.admin_note,
            status=req.status,
            reviewed_by_user_name=reviewer_name,
            reviewed_at=req.reviewed_at.isoformat() if req.reviewed_at else None,
            created_at=req.created_at.isoformat(),
        )
        for req, customer, sales_name, reviewer_name in rows.all()
    ]


@router.post("/tuition-gift-requests/{request_id}/approve")
async def approve_tuition_gift_request(
    request_id: str,
    body: TuitionGiftReviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    row = await db.execute(select(TuitionGiftRequest).where(TuitionGiftRequest.id == request_id))
    req = row.scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "申请记录不存在")
    if req.status != "pending":
        raise HTTPException(400, "该申请已处理")
    customer_r = await db.execute(select(Customer).where(Customer.id == req.customer_id))
    customer = customer_r.scalar_one_or_none()
    if customer is None:
        raise HTTPException(404, "客户不存在")
    req.status = "approved"
    req.reviewed_by_user_id = current_user.id
    req.reviewed_at = datetime.utcnow()
    req.updated_at = datetime.utcnow()
    req.admin_note = body.admin_note
    customer.gifted_tuition_amount = max(Decimal("0"), (customer.gifted_tuition_amount or Decimal("0")) + req.amount)
    db.add(
        AuditLog(
            resource_type="tuition_gift_request",
            resource_id=req.id,
            customer_id=customer.id,
            action="gift_request_approved",
            amount_delta=req.amount,
            operator_user_id=current_user.id,
            operator_role="admin",
            note=body.admin_note,
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.post("/tuition-gift-requests/{request_id}/reject")
async def reject_tuition_gift_request(
    request_id: str,
    body: TuitionGiftReviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    row = await db.execute(select(TuitionGiftRequest).where(TuitionGiftRequest.id == request_id))
    req = row.scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "申请记录不存在")
    if req.status != "pending":
        raise HTTPException(400, "该申请已处理")
    admin_note = (body.admin_note or "").strip()
    if not admin_note:
        raise HTTPException(400, "admin_note is required")

    req.status = "rejected"
    req.reviewed_by_user_id = current_user.id
    req.reviewed_at = datetime.utcnow()
    req.updated_at = datetime.utcnow()
    req.admin_note = admin_note
    db.add(
        AuditLog(
            resource_type="tuition_gift_request",
            resource_id=req.id,
            customer_id=req.customer_id,
            action="gift_request_rejected",
            amount_delta=0,
            operator_user_id=current_user.id,
            operator_role="admin",
            note=admin_note,
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.get("/tuition-writeoff/summary", response_model=AdminTuitionWriteoffSummaryOut)
async def tuition_writeoff_summary(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    await ensure_accounting_type_schema()
    customers_r = await db.execute(select(Customer))
    customers = customers_r.scalars().all()

    now = datetime.utcnow()
    this_month_start = datetime(now.year, now.month, 1)
    if now.month == 1:
        last_month_start = datetime(now.year - 1, 12, 1)
    else:
        last_month_start = datetime(now.year, now.month - 1, 1)

    total_gifted = Decimal("0")
    total_spent = Decimal("0")
    total_balance = Decimal("0")
    total_pending = 0
    last_month_spent = Decimal("0")

    for c in customers:
        gifted = Decimal(c.gifted_tuition_amount or 0)
        spent = await get_customer_sales_spent(db, c.id)
        writeoff_total = await get_customer_admin_writeoff_total(db, c.id)
        pending_r = await db.execute(
            select(func.count(TuitionGiftRequest.id)).where(
                TuitionGiftRequest.customer_id == c.id,
                TuitionGiftRequest.status == "pending",
            )
        )
        last_month_spent_for_customer = await get_sales_spent_in_period(
            db,
            c.id,
            last_month_start,
            this_month_start,
        )

        total_gifted += gifted
        total_spent += spent
        total_balance += max(Decimal("0"), gifted - writeoff_total)
        total_pending += int(pending_r.scalar() or 0)
        last_month_spent += last_month_spent_for_customer

    return AdminTuitionWriteoffSummaryOut(
        total_gifted=float(total_gifted),
        total_spent=float(total_spent),
        last_month_spent=float(last_month_spent),
        total_balance=float(total_balance),
        total_pending=total_pending,
    )


@router.get("/tuition-writeoff/customers", response_model=list[AdminTuitionWriteoffCustomerOut])
async def list_tuition_writeoff_customers(
    keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    await ensure_accounting_type_schema()
    customers_r = await db.execute(select(Customer).order_by(Customer.updated_at.desc(), Customer.created_at.desc()))
    customers = customers_r.scalars().all()
    out: list[AdminTuitionWriteoffCustomerOut] = []
    k = (keyword or "").strip().lower()

    for c in customers:
        sales_r = await db.execute(select(User.name).where(User.id == c.entry_user_id))
        sales_name = sales_r.scalar_one_or_none()
        tags = await _build_tags(c.id, db)
        consultant_r = await db.execute(
            select(func.coalesce(func.max(ConsultantCustomer.consultation_count), 0)).where(
                ConsultantCustomer.customer_id == c.id
            )
        )
        consult_count = int(consultant_r.scalar() or 0)
        link_r = await db.execute(select(LinkAccount.account_id).where(LinkAccount.id == c.link_account_id))
        wechat_name = link_r.scalar_one_or_none()
        searchable = [
            c.name.lower(),
            c.phone.lower(),
            (c.sales_note or "").lower(),
            (wechat_name or "").lower(),
            *[t.name.lower() for t in tags],
        ]
        if k and not any(k in text for text in searchable):
            continue

        total_spent = await get_customer_sales_spent(db, c.id)
        gifted = Decimal(c.gifted_tuition_amount or 0)
        writeoff_total = await get_customer_admin_writeoff_total(db, c.id)
        tuition_balance = max(Decimal("0"), gifted - writeoff_total)

        pending_r = await db.execute(
            select(TuitionGiftRequest)
            .where(
                TuitionGiftRequest.customer_id == c.id,
                TuitionGiftRequest.status == "pending",
            )
            .order_by(TuitionGiftRequest.created_at.desc())
        )
        pending_requests = pending_r.scalars().all()
        pending_count = len(pending_requests)
        latest_pending_note = pending_requests[0].sales_note if pending_requests else None

        course_r = await db.execute(
            select(CustomerCourseEnrollment, Product, Order)
            .join(Product, Product.id == CustomerCourseEnrollment.product_id)
            .join(Order, Order.id == CustomerCourseEnrollment.order_id)
            .where(
                CustomerCourseEnrollment.customer_id == c.id,
                CustomerCourseEnrollment.accounting_type == ADMIN_WRITEOFF_ACCOUNTING_TYPE,
            )
            .order_by(CustomerCourseEnrollment.created_at.desc())
        )
        courses = [
            AdminWriteoffCourseOut(
                enrollment_id=e.id,
                product_name=p.name,
                amount_paid=e.amount_paid,
                refunded_amount=float(o.refund_total or 0),
                status=e.status,
                created_at=e.created_at.isoformat(),
            )
            for e, p, o in course_r.all()
        ]

        out.append(
            AdminTuitionWriteoffCustomerOut(
                customer_id=c.id,
                customer_name=c.name,
                phone=c.phone,
                sales_name=sales_name,
                tags=tags,
                consult_count=consult_count,
                wechat_name=wechat_name,
                sales_note=c.sales_note,
                total_spent=float(total_spent),
                gifted_tuition_amount=float(gifted),
                tuition_balance=float(tuition_balance),
                pending_gift_request_count=pending_count,
                latest_pending_gift_note=latest_pending_note,
                courses=courses,
            )
        )

    out.sort(key=lambda i: (i.pending_gift_request_count > 0, i.pending_gift_request_count, i.customer_name), reverse=True)
    return out


@router.get("/audit-logs", response_model=AdminAuditLogListOut)
async def list_admin_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = Query(None),
    operator_user_id: str | None = Query(None),
    days: int | None = Query(None),
    keyword: str | None = Query(None),
    customer_keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    if action and action not in AUDIT_ACTIONS:
        raise HTTPException(400, "invalid action")
    if days is not None and days not in {7, 30}:
        raise HTTPException(400, "days must be 7 or 30")

    filters = [AuditLog.action.in_(AUDIT_ACTIONS)]
    if action:
        filters.append(AuditLog.action == action)
    if operator_user_id:
        filters.append(AuditLog.operator_user_id == operator_user_id)
    if days:
        start_at = datetime.utcnow() - timedelta(days=days)
        filters.append(AuditLog.created_at >= start_at)

    rows = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
    )
    logs = rows.scalars().all()

    operator_ids = sorted({log.operator_user_id for log in logs if log.operator_user_id})
    customer_ids_page = sorted({log.customer_id for log in logs if log.customer_id})

    operator_map: dict[str, str] = {}
    if operator_ids:
        op_rows = await db.execute(select(User.id, User.name).where(User.id.in_(operator_ids)))
        operator_map = {uid: name for uid, name in op_rows.all()}

    customer_map: dict[str, str] = {}
    if customer_ids_page:
        c_rows = await db.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids_page)))
        customer_map = {cid: name for cid, name in c_rows.all()}

    search_text = (keyword or customer_keyword or "").strip().lower()
    items = []
    for log in logs:
        operator_name = operator_map.get(log.operator_user_id or "", None)
        customer_name = customer_map.get(log.customer_id or "", None)
        if search_text:
            action_label = AUDIT_ACTION_LABELS.get(log.action, log.action)
            searchable = [
                log.action.lower(),
                action_label.lower(),
                (log.note or "").lower(),
                (operator_name or "").lower(),
                (customer_name or "").lower(),
            ]
            if not any(search_text in text for text in searchable):
                continue
        items.append(
            AdminAuditLogItemOut(
                id=log.id,
                created_at=log.created_at.isoformat(),
                action=log.action,
                amount_delta=log.amount_delta,
                note=log.note,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                changes=log.changes,
                related_event_id=log.related_event_id,
                operator_user_id=log.operator_user_id,
                operator_role=log.operator_role,
                operator_name=operator_name,
                customer_id=log.customer_id,
                customer_name=customer_name,
            )
        )

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return AdminAuditLogListOut(total=total, items=items[start:end])


@router.post("/customers/{customer_id}/writeoff-courses", status_code=201)
async def create_admin_writeoff_course(
    customer_id: str,
    body: AdminWriteoffCourseIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    await ensure_accounting_type_schema()
    if body.status not in ADMIN_COURSE_STATUSES:
        raise HTTPException(400, "status invalid")
    if body.amount < 0:
        raise HTTPException(400, "amount must be >= 0")

    customer_r = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = customer_r.scalar_one_or_none()
    if customer is None:
        raise HTTPException(404, "客户不存在")

    product_r = await db.execute(select(Product).where(Product.id == body.product_id))
    product = product_r.scalar_one_or_none()
    if product is None:
        raise HTTPException(404, "产品不存在")

    now = datetime.utcnow()
    order = Order(
        customer_id=customer_id,
        product_id=body.product_id,
        sales_user_id=current_user.id,
        amount=body.amount,
        list_price=product.price,
        deal_price=body.amount,
        refund_total=body.amount if body.status == "admin_marked_completed_refunded" else 0,
        status="refunded" if body.status == "admin_marked_completed_refunded" else "active",
        refunded_at=now if body.status == "admin_marked_completed_refunded" else None,
    )
    db.add(order)
    await db.flush()

    db.add(
        CustomerProduct(
            customer_id=customer_id,
            product_id=body.product_id,
            order_id=order.id,
            is_refunded=body.status == "admin_marked_completed_refunded",
        )
    )

    enrollment = CustomerCourseEnrollment(
        customer_id=customer_id,
        order_id=order.id,
        product_id=body.product_id,
        amount_paid=body.amount,
        accounting_type=ADMIN_WRITEOFF_ACCOUNTING_TYPE,
        status=body.status,
        status_updated_by=current_user.id,
        status_updated_role="admin",
        status_updated_at=now,
        refunded_at=now if body.status == "admin_marked_completed_refunded" else None,
    )
    db.add(enrollment)
    db.add(
        AuditLog(
            resource_type="course_enrollment",
            resource_id=order.id,
            customer_id=customer_id,
            action="admin_writeoff_course_created",
            amount_delta=Decimal("0") if body.status == "admin_marked_completed_refunded" else -Decimal(body.amount),
            operator_user_id=current_user.id,
            operator_role="admin",
            note=body.note or product.name,
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.put("/customers/{customer_id}/courses/{enrollment_id}/status")
async def update_admin_course_status(
    customer_id: str,
    enrollment_id: str,
    body: AdminCourseStatusUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    await ensure_accounting_type_schema()
    if body.status not in ADMIN_COURSE_STATUSES:
        raise HTTPException(400, "????????2???")

    row = await db.execute(
        select(CustomerCourseEnrollment).where(
            CustomerCourseEnrollment.id == enrollment_id,
            CustomerCourseEnrollment.customer_id == customer_id,
        )
    )
    enrollment = row.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(404, "???????")
    _ensure_admin_writeoff_enrollment(enrollment)

    order_r = await db.execute(select(Order).where(Order.id == enrollment.order_id))
    order = order_r.scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "?????")

    if body.status == enrollment.status:
        return {"message": "ok"}

    now = datetime.utcnow()
    if body.status == "admin_marked_completed_refunded":
        order_amount = Decimal(order.amount or enrollment.amount_paid or 0)
        current_refund = Decimal(order.refund_total or 0)
        max_refund = max(Decimal("0"), order_amount - current_refund)
        refund_amount = Decimal(body.refund_amount if body.refund_amount is not None else max_refund)
        if refund_amount <= 0:
            raise HTTPException(400, "????????0")
        if refund_amount > max_refund:
            raise HTTPException(400, "????????????")

        enrollment.status = body.status
        enrollment.status_updated_by = current_user.id
        enrollment.status_updated_role = "admin"
        enrollment.status_updated_at = now
        enrollment.refunded_at = now
        enrollment.updated_at = now

        order.status = "refunded"
        order.refunded_at = now
        order.refund_total = current_refund + refund_amount
        order.updated_at = now

        cp_r = await db.execute(
            select(CustomerProduct).where(
                CustomerProduct.order_id == order.id,
                CustomerProduct.customer_id == customer_id,
                CustomerProduct.product_id == enrollment.product_id,
            )
        )
        customer_product = cp_r.scalar_one_or_none()
        if customer_product is not None:
            customer_product.is_refunded = True

        db.add(
            AuditLog(
                resource_type="course_enrollment",
                resource_id=order.id,
                customer_id=customer_id,
                action="admin_writeoff_course_refunded",
                amount_delta=refund_amount,
                operator_user_id=current_user.id,
                operator_role="admin",
                note="???????",
            )
        )
    else:
        revert_amount = Decimal(order.refund_total or 0)

        enrollment.status = "admin_marked_completed"
        enrollment.status_updated_by = current_user.id
        enrollment.status_updated_role = "admin"
        enrollment.status_updated_at = now
        enrollment.refunded_at = None
        enrollment.updated_at = now

        order.status = "active"
        order.refunded_at = None
        order.refund_total = 0
        order.updated_at = now

        cp_r = await db.execute(
            select(CustomerProduct).where(
                CustomerProduct.order_id == order.id,
                CustomerProduct.customer_id == customer_id,
                CustomerProduct.product_id == enrollment.product_id,
            )
        )
        customer_product = cp_r.scalar_one_or_none()
        if customer_product is not None:
            customer_product.is_refunded = False

        db.add(
            AuditLog(
                resource_type="course_enrollment",
                resource_id=order.id,
                customer_id=customer_id,
                action="admin_writeoff_course_refund_reverted",
                amount_delta=-revert_amount,
                operator_user_id=current_user.id,
                operator_role="admin",
                note="?????????",
            )
        )

    await db.commit()
    return {"message": "ok"}


@router.get("/pool", response_model=AdminPoolOut)
async def admin_pool(
    status: str = Query("pending"),
    keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    if status not in {"pending", "active", "ended"}:
        raise HTTPException(400, "invalid status")

    summary_rows = await db.execute(
        select(ConsultantCustomer.status, func.count(ConsultantCustomer.id))
        .where(ConsultantCustomer.status.in_(["pending", "active", "ended"]))
        .group_by(ConsultantCustomer.status)
    )
    summary_map = {s: int(c) for s, c in summary_rows.all()}

    rows = await db.execute(
        select(ConsultantCustomer, Customer, User.name)
        .join(Customer, ConsultantCustomer.customer_id == Customer.id)
        .join(User, Customer.entry_user_id == User.id, isouter=True)
        .where(ConsultantCustomer.status == status)
        .order_by(ConsultantCustomer.created_at.asc())
    )

    out: list[AdminPoolItemOut] = []
    now = datetime.utcnow()
    for rel, customer, sales_name in rows.all():
        tag_rows = await db.execute(
            select(Tag, TagCategory)
            .join(TagCategory, Tag.category_id == TagCategory.id)
            .join(CustomerTag, and_(CustomerTag.tag_id == Tag.id, CustomerTag.customer_id == customer.id))
            .order_by(TagCategory.sort_order.asc(), Tag.name.asc())
        )
        tags = [TagOut(id=t.id, name=t.name, color=tc.color) for t, tc in tag_rows.all()]

        if keyword:
            k = keyword.strip().lower()
            if k:
                if k not in customer.name.lower() and k not in customer.phone.lower() and not any(k in t.name.lower() for t in tags):
                    continue

        entered_days = max(0, (now.date() - rel.created_at.date()).days)
        claim_status = "未认领" if rel.status == "pending" else ("进行中" if rel.status == "active" else "已结束")

        out.append(
            AdminPoolItemOut(
                pool_id=rel.id,
                customer_id=customer.id,
                customer_name=customer.name,
                customer_info=f"{customer.industry or ''} · {customer.region or ''}".strip(" ·"),
                tags=tags,
                sales_name=sales_name,
                entered_days=entered_days,
                claim_status=claim_status,
                pool_entered_at=rel.created_at.isoformat(),
            )
        )

    return AdminPoolOut(
        summary=AdminPoolSummaryOut(
            pending=summary_map.get("pending", 0),
            active=summary_map.get("active", 0),
            ended=summary_map.get("ended", 0),
        ),
        items=out,
    )


@router.get("/customers", response_model=list[AdminCustomerItemOut])
async def admin_customers(
    keyword: str | None = Query(None),
    view: str = Query("all"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    customers_r = await db.execute(select(Customer).order_by(Customer.created_at.desc()))
    out: list[AdminCustomerItemOut] = []

    for c in customers_r.scalars().all():
        tags = await _build_tags(c.id, db)
        if keyword:
            k = keyword.strip().lower()
            if k and k not in c.name.lower() and not any(k in t.name.lower() for t in tags):
                continue

        products, _ = await _build_products(c.id, db)
        consultants = await _build_consultants(c.id, "", db)

        paid_r = await db.execute(
            select(CustomerProduct.id).where(
                CustomerProduct.customer_id == c.id,
                CustomerProduct.is_refunded.is_(False),
            )
        )
        has_deal = paid_r.first() is not None
        in_consulting = len(consultants) > 0

        if in_consulting:
            asset_status = "consulting"
        elif has_deal:
            asset_status = "dealed"
        else:
            asset_status = "normal"

        if view == "dealed" and not has_deal:
            continue
        if view == "consulting" and not in_consulting:
            continue

        sales_r = await db.execute(select(User.name).where(User.id == c.entry_user_id))
        sales_name = sales_r.scalar_one_or_none()
        link_r = await db.execute(select(LinkAccount.account_id).where(LinkAccount.id == c.link_account_id))
        wechat_name = link_r.scalar_one_or_none()

        out.append(
            AdminCustomerItemOut(
                customer_id=c.id,
                customer_name=c.name,
                phone=c.phone,
                customer_info=f"{c.industry or ''}{'-' if c.industry and c.region else ''}{c.region or ''}",
                added_date=c.added_date.isoformat(),
                other_contact=c.other_contact,
                wechat_name=wechat_name,
                sales_name=sales_name,
                tags=tags,
                products=products,
                consultants=consultants,
                asset_status=asset_status,
                created_at=c.created_at.isoformat(),
            )
        )

    return out


@router.get("/dashboard", response_model=AdminDashboardOut)
async def admin_dashboard(
    month: str | None = Query(None, description="YYYY-MM"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    today = date.today()
    if month:
        y, m = month.split("-")
        start = date(int(y), int(m), 1)
    else:
        start = date(today.year, today.month, 1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)

    sales_users_r = await db.execute(select(User).where(User.role == "sales"))
    sales_capacity: list[dict] = []
    for u in sales_users_r.scalars().all():
        new_customers_r = await db.execute(
            select(func.count(Customer.id)).where(
                Customer.entry_user_id == u.id,
                Customer.created_at >= start,
                Customer.created_at < end,
            )
        )
        order_cnt_r = await db.execute(
            select(func.count(Order.id)).where(
                Order.sales_user_id == u.id,
                Order.created_at >= start,
                Order.created_at < end,
                Order.refunded_at.is_(None),
            )
        )
        amount_r = await db.execute(
            select(func.coalesce(func.sum(Order.amount), 0)).where(
                Order.sales_user_id == u.id,
                Order.created_at >= start,
                Order.created_at < end,
                Order.refunded_at.is_(None),
            )
        )
        sales_capacity.append(
            {
                "user_id": u.id,
                "name": u.name,
                "new_customers": new_customers_r.scalar() or 0,
                "order_count": order_cnt_r.scalar() or 0,
                "deal_amount": int(amount_r.scalar() or 0),
            }
        )

    source_rows = await db.execute(
        select(Customer.industry, Customer.region, func.count(Customer.id))
        .where(Customer.created_at >= start, Customer.created_at < end)
        .group_by(Customer.industry, Customer.region)
        .order_by(func.count(Customer.id).desc())
    )
    source_channels = [
        {
            "source": f"{industry or ''}{'-' if industry and region else ''}{region or ''}" or "未标注",
            "count": cnt,
        }
        for industry, region, cnt in source_rows.all()
    ]

    product_rows = await db.execute(
        select(Product.id, Product.name, func.count(Order.id), func.coalesce(func.sum(Order.amount), 0))
        .join(Order, Order.product_id == Product.id)
        .where(
            Order.created_at >= start,
            Order.created_at < end,
            Order.refunded_at.is_(None),
        )
        .group_by(Product.id, Product.name)
        .order_by(func.count(Order.id).desc())
    )
    product_deals = [
        {"product_id": pid, "product_name": name, "order_count": cnt, "deal_amount": int(amount or 0)}
        for pid, name, cnt, amount in product_rows.all()
    ]

    consultant_rows = await db.execute(select(User).where(User.role == "consultant"))
    consultant_delivery: list[dict] = []
    for u in consultant_rows.scalars().all():
        service_cnt_r = await db.execute(
            select(func.count(func.distinct(ConsultantCustomer.customer_id))).where(
                ConsultantCustomer.consultant_id == u.id,
                ConsultantCustomer.status == "active",
            )
        )
        meeting_cnt_r = await db.execute(
            select(func.count(ConsultationLog.id)).where(
                ConsultationLog.consultant_id == u.id,
                ConsultationLog.log_date >= start,
                ConsultationLog.log_date < end,
            )
        )
        consultant_delivery.append(
            {
                "user_id": u.id,
                "name": u.name,
                "service_customers": service_cnt_r.scalar() or 0,
                "meetings_this_month": meeting_cnt_r.scalar() or 0,
            }
        )

    sales_capacity.sort(key=lambda i: (i["deal_amount"], i["order_count"]), reverse=True)
    consultant_delivery.sort(key=lambda i: (i["service_customers"], i["meetings_this_month"]), reverse=True)

    return AdminDashboardOut(
        sales_capacity=sales_capacity,
        source_channels=source_channels,
        product_deals=product_deals,
        consultant_delivery=consultant_delivery,
    )
