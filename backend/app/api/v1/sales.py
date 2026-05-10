import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func, and_, or_
from sqlmodel import SQLModel
from pydantic import BaseModel, Field

from app.core.deps import get_db, require_role
from app.db import engine
from app.models.user import User
from app.models.customer import Customer
from app.models.link_account import LinkAccount
from app.models.product import Product
from app.models.order import Order, CustomerProduct
from app.models.consultant_customer import ConsultantCustomer
from app.models.consultation_log import ConsultationLog
from app.models.customer_course_enrollment import CustomerCourseEnrollment
from app.models.audit_log import AuditLog
from app.models.tuition_gift_request import TuitionGiftRequest
from app.models.tag import Tag, CustomerTag, TagCategory
from app.services.accounting import (
    SALES_SPENT_ACCOUNTING_TYPE,
    ensure_accounting_type_schema,
    get_customer_admin_writeoff_total,
    get_customer_sales_spent,
)

router = APIRouter(prefix="/sales", tags=["sales"])


# ===================== Schemas =====================

class LinkAccountOut(BaseModel):
    id: str
    account_id: str
    customer_count: int
    is_active: bool

class CustomerTagOut(BaseModel):
    id: str
    name: str
    color: str

class CustomerProductOut(BaseModel):
    product_id: str
    product_name: str
    price: float
    is_refunded: bool
    order_id: str | None = None


class CourseEnrollmentOut(BaseModel):
    enrollment_id: str
    product_id: str
    product_name: str
    amount_paid: float
    refunded_amount: float
    status: str

class CustomerOut(BaseModel):
    id: str
    name: str
    phone: str | None
    industry: str | None
    region: str | None
    added_date: str
    client_wechat_name: str | None
    other_contact: str | None
    link_account_id: str
    link_account_name: str | None
    tags: list[CustomerTagOut]
    products: list[CustomerProductOut]
    note: str | None
    next_follow_up: str | None
    follow_up_overdue: bool
    next_follow_up_status: str
    in_consultation_pool: bool
    consultation_count: int | None
    courses: list[CourseEnrollmentOut]
    total_spent: float
    gifted_tuition_amount: float
    tuition_balance: float

class CustomerCreate(BaseModel):
    name: str = Field(max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    client_wechat_name: str = Field(max_length=100)
    industry: str | None = None
    region: str | None = None
    added_date: str | None = None
    other_contact: str | None = Field(default=None, max_length=200)
    link_account_id: str

class CustomerUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    client_wechat_name: str | None = Field(default=None, max_length=100)
    industry: str | None = None
    region: str | None = None
    added_date: str | None = None
    other_contact: str | None = Field(default=None, max_length=200)
    note: str | None = None
    next_follow_up: str | None = None
    consultation_count: int | None = None
    gifted_tuition_amount: float | None = None


class CustomerDuplicateCheckIn(BaseModel):
    phone: str | None = None
    client_wechat_name: str | None = None
    exclude_customer_id: str | None = None


class CustomerDuplicateMatchOut(BaseModel):
    customer_id: str
    customer_name: str
    phone: str | None
    client_wechat_name: str | None
    owner_id: str
    owner_name: str | None
    link_account_name: str | None
    consultant_names: list[str]
    matched_fields: list[str]


class CustomerDuplicateCheckOut(BaseModel):
    exists: bool
    matches: list[CustomerDuplicateMatchOut]

class PurchaseRequest(BaseModel):
    product_id: str
    amount: float = Field(ge=0)

class TagRequest(BaseModel):
    tag_id: str


class LinkAccountCreateIn(BaseModel):
    account_id: str = Field(min_length=2, max_length=200)


class SalesCourseStatusUpdateIn(BaseModel):
    status: str


class SalesCreateCourseIn(BaseModel):
    product_id: str
    amount: float | None = None


class SalesCourseRefundIn(BaseModel):
    refund_amount: float = Field(ge=0)


class SalesCoursePriceUpdateIn(BaseModel):
    amount_paid: float = Field(ge=0)


class SalesCourseRefundRevertIn(BaseModel):
    pass


class TuitionGiftRequestIn(BaseModel):
    customer_id: str
    amount: float = Field(ge=0.01)
    sales_note: str | None = None


class SalesTuitionGiftRequestOut(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    amount: float
    sales_note: str | None
    admin_note: str | None
    status: str
    reviewed_at: str | None
    created_at: str

SALES_COURSE_STATUSES = {
    "purchased_not_started",
    "sales_marked_completed",
    "purchased_not_started_refunded",
    "sales_marked_completed_refunded",
}


# ===================== Helpers =====================

async def _get_my_customer(customer_id: str, current_user: User, db: AsyncSession) -> Customer:
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "客户不存在")
    la_result = await db.execute(
        select(LinkAccount).where(
            LinkAccount.id == customer.link_account_id,
            LinkAccount.owner_id == current_user.id,
        )
    )
    if not la_result.scalar_one_or_none():
        raise HTTPException(403, "无权操作该客户")
    return customer


async def _get_owned_account_ids(current_user: User, db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(LinkAccount.id).where(LinkAccount.owner_id == current_user.id)
    )
    return [row[0] for row in result.all()]

async def _ensure_tuition_gift_request_schema() -> None:
    async with engine.begin() as conn:
        import app.models  # noqa: F401
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(text("ALTER TABLE tuition_gift_requests ADD COLUMN IF NOT EXISTS admin_note TEXT"))
        await conn.execute(text("ALTER TABLE tuition_gift_requests ADD COLUMN IF NOT EXISTS reviewed_by_user_id VARCHAR(36)"))
        await conn.execute(text("ALTER TABLE tuition_gift_requests ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP"))


async def _ensure_customer_added_date_schema() -> None:
    async with engine.begin() as conn:
        import app.models  # noqa: F401
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS client_wechat_name VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS added_date DATE"))
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS other_contact VARCHAR(200)"))
        await conn.execute(text("ALTER TABLE customers ALTER COLUMN phone DROP NOT NULL"))
        await conn.execute(text("UPDATE customers SET added_date = CURRENT_DATE WHERE added_date IS NULL"))
        await conn.execute(text("ALTER TABLE customers ALTER COLUMN added_date SET DEFAULT CURRENT_DATE"))
    await ensure_accounting_type_schema()


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _require_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(400, f"{field_name} is required")
    return cleaned


def _normalize_wechat(value: str | None) -> str | None:
    cleaned = _clean_optional_text(value)
    return cleaned.lower() if cleaned else None


def _normalize_phone(value: str | None) -> str | None:
    cleaned = _clean_optional_text(value)
    if not cleaned:
        return None
    compact = re.sub(r"[\s\-()]+", "", cleaned)
    if compact.startswith("+86"):
        compact = compact[3:]
    elif compact.startswith("86") and len(compact) > 11:
        compact = compact[2:]
    return compact


def _parse_iso_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def _find_duplicate_customers(
    *,
    db: AsyncSession,
    phone: str | None,
    client_wechat_name: str | None,
    exclude_customer_id: str | None = None,
) -> list[CustomerDuplicateMatchOut]:
    normalized_phone = _normalize_phone(phone)
    normalized_wechat = _normalize_wechat(client_wechat_name)
    if not normalized_phone and not normalized_wechat:
        return []

    conditions = []
    if normalized_phone:
        phone_expr = func.regexp_replace(
            func.regexp_replace(
                func.regexp_replace(func.coalesce(Customer.phone, ""), r"[\s\-()]+", "", "g"),
                "^0086",
                "",
                "g",
            ),
            "^86",
            "",
            "g",
        )
        conditions.append(phone_expr == normalized_phone)
    if normalized_wechat:
        conditions.append(func.lower(func.trim(Customer.client_wechat_name)) == normalized_wechat)

    query = select(Customer).where(or_(*conditions))
    if exclude_customer_id:
        query = query.where(Customer.id != exclude_customer_id)

    rows = await db.execute(query)
    matches: list[CustomerDuplicateMatchOut] = []
    for customer in rows.scalars().all():
        hit_fields: list[str] = []
        if normalized_phone and _normalize_phone(customer.phone) == normalized_phone:
            hit_fields.append("phone")
        if normalized_wechat and _normalize_wechat(customer.client_wechat_name) == normalized_wechat:
            hit_fields.append("client_wechat_name")
        if not hit_fields:
            continue

        la_r = await db.execute(select(LinkAccount).where(LinkAccount.id == customer.link_account_id))
        link_account = la_r.scalar_one_or_none()
        owner_id = link_account.owner_id if link_account else ""
        owner_name = None
        if owner_id:
            owner_r = await db.execute(select(User.name).where(User.id == owner_id))
            owner_name = owner_r.scalar_one_or_none()
        consultant_rows = await db.execute(
            select(User.name)
            .join(ConsultantCustomer, ConsultantCustomer.consultant_id == User.id)
            .where(
                ConsultantCustomer.customer_id == customer.id,
                ConsultantCustomer.status.in_(["active", "pending"]),
                User.role == "consultant",
            )
            .order_by(User.name)
        )
        consultant_names = [name for (name,) in consultant_rows.all()]

        matches.append(
            CustomerDuplicateMatchOut(
                customer_id=customer.id,
                customer_name=customer.name,
                phone=customer.phone,
                client_wechat_name=customer.client_wechat_name,
                owner_id=owner_id,
                owner_name=owner_name,
                link_account_name=link_account.account_id if link_account else None,
                consultant_names=consultant_names,
                matched_fields=hit_fields,
            )
        )
    return matches


async def _build_customer_out(customer: Customer, db: AsyncSession) -> CustomerOut:
    # link_account name
    la_result = await db.execute(select(LinkAccount.account_id).where(LinkAccount.id == customer.link_account_id))
    la_name = la_result.scalar_one_or_none()

    # tags with category colors
    tag_rows = await db.execute(
        select(Tag, TagCategory)
        .join(TagCategory, Tag.category_id == TagCategory.id)
        .join(CustomerTag, and_(CustomerTag.tag_id == Tag.id, CustomerTag.customer_id == customer.id))
    )
    tags = [CustomerTagOut(id=t.id, name=t.name, color=tc.color) for t, tc in tag_rows.all()]

    # purchased products
    cp_rows = await db.execute(
        select(CustomerProduct, Product)
        .join(Product, CustomerProduct.product_id == Product.id)
        .where(CustomerProduct.customer_id == customer.id)
    )
    products = [
        CustomerProductOut(
            product_id=p.id, product_name=p.name, price=p.price,
            is_refunded=cp.is_refunded, order_id=cp.order_id,
        )
        for cp, p in cp_rows.all()
    ]

    # course enrollments
    enrollment_rows = await db.execute(
        select(CustomerCourseEnrollment, Product, Order)
        .join(Product, Product.id == CustomerCourseEnrollment.product_id)
        .join(Order, Order.id == CustomerCourseEnrollment.order_id)
        .where(CustomerCourseEnrollment.customer_id == customer.id)
        .order_by(CustomerCourseEnrollment.created_at.desc())
    )
    courses = [
        CourseEnrollmentOut(
            enrollment_id=e.id,
            product_id=e.product_id,
            product_name=p.name,
            amount_paid=e.amount_paid,
            refunded_amount=o.refund_total,
            status=e.status,
        )
        for e, p, o in enrollment_rows.all()
    ]

    total_spent = await get_customer_sales_spent(db, customer.id)
    gifted = Decimal(customer.gifted_tuition_amount or 0)
    admin_writeoff_total = await get_customer_admin_writeoff_total(db, customer.id)
    tuition_balance = max(Decimal("0"), gifted - admin_writeoff_total)

    # sales note / next follow-up stored on customers
    note = customer.sales_note
    next_fu = customer.next_follow_up.isoformat() if customer.next_follow_up else None
    overdue = False
    status = "unset"
    if customer.next_follow_up:
        now = datetime.utcnow()
        target = customer.next_follow_up.replace(tzinfo=None)
        overdue = target < now
        if target.date() < now.date():
            status = "overdue"
        elif target.date() == now.date():
            status = "today"
        else:
            status = "future"

    # consultation pool / count
    pending_r = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer.id,
            ConsultantCustomer.status == "pending",
        )
    )
    in_pool = pending_r.scalar_one_or_none() is not None

    consultation_count_r = await db.execute(
        select(func.count(ConsultationLog.id)).where(ConsultationLog.customer_id == customer.id)
    )
    consultation_count = int(consultation_count_r.scalar() or 0)

    return CustomerOut(
        id=customer.id, name=customer.name, phone=customer.phone,
        industry=customer.industry, region=customer.region, added_date=customer.added_date.isoformat(),
        client_wechat_name=customer.client_wechat_name,
        other_contact=customer.other_contact,
        link_account_id=customer.link_account_id, link_account_name=la_name,
        tags=tags, products=products,
        note=note, next_follow_up=next_fu, follow_up_overdue=overdue,
        next_follow_up_status=status,
        in_consultation_pool=in_pool,
        consultation_count=consultation_count,
        courses=courses,
        total_spent=float(total_spent),
        gifted_tuition_amount=float(gifted),
        tuition_balance=float(tuition_balance),
    )


async def _write_audit_log(
    db: AsyncSession,
    *,
    resource_type: str,
    resource_id: str,
    action: str,
    customer_id: str | None = None,
    changes: str | None = None,
    amount_delta: float | None = None,
    operator_user_id: str | None = None,
    operator_role: str | None = None,
    note: str | None = None,
    related_event_id: str | None = None,
) -> None:
    db.add(
        AuditLog(
            resource_type=resource_type,
            resource_id=resource_id,
            customer_id=customer_id,
            action=action,
            changes=changes,
            amount_delta=amount_delta,
            operator_user_id=operator_user_id,
            operator_role=operator_role,
            note=note,
            related_event_id=related_event_id,
        )
    )


async def _get_enrollment_or_404(
    db: AsyncSession,
    customer_id: str,
    enrollment_id: str,
) -> CustomerCourseEnrollment:
    await ensure_accounting_type_schema()
    row = await db.execute(
        select(CustomerCourseEnrollment).where(
            CustomerCourseEnrollment.id == enrollment_id,
            CustomerCourseEnrollment.customer_id == customer_id,
        )
    )
    enrollment = row.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(404, "课程记录不存在")
    return enrollment


def _ensure_sales_accounting_enrollment(enrollment: CustomerCourseEnrollment) -> None:
    if enrollment.accounting_type != SALES_SPENT_ACCOUNTING_TYPE:
        raise HTTPException(403, "管理员销课记录仅允许管理员操作")


@router.post("/tuition-gift-requests", status_code=201)
async def create_tuition_gift_request(
    body: TuitionGiftRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _ensure_tuition_gift_request_schema()
    customer = await _get_my_customer(body.customer_id, current_user, db)
    req = TuitionGiftRequest(
        customer_id=customer.id,
        sales_user_id=current_user.id,
        amount=body.amount,
        sales_note=body.sales_note,
        status="pending",
    )
    db.add(req)
    db.add(
        AuditLog(
            resource_type="tuition_gift_request",
            resource_id=req.id,
            customer_id=customer.id,
            action="gift_request_created",
            amount_delta=body.amount,
            operator_user_id=current_user.id,
            operator_role="sales",
            note=body.sales_note,
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.get("/tuition-gift-requests", response_model=list[SalesTuitionGiftRequestOut])
async def list_tuition_gift_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _ensure_tuition_gift_request_schema()
    owned_ids = await _get_owned_account_ids(current_user, db)
    if not owned_ids:
        return []

    rows = await db.execute(
        select(TuitionGiftRequest, Customer)
        .join(Customer, TuitionGiftRequest.customer_id == Customer.id)
        .where(
            TuitionGiftRequest.sales_user_id == current_user.id,
            Customer.link_account_id.in_(owned_ids),
        )
        .order_by(TuitionGiftRequest.created_at.desc())
    )
    return [
        SalesTuitionGiftRequestOut(
            id=req.id,
            customer_id=customer.id,
            customer_name=customer.name,
            amount=req.amount,
            sales_note=req.sales_note,
            admin_note=req.admin_note,
            status=req.status,
            reviewed_at=req.reviewed_at.isoformat() if req.reviewed_at else None,
            created_at=req.created_at.isoformat(),
        )
        for req, customer in rows.all()
    ]
# ===================== Link Accounts =====================

@router.get("/link-accounts", response_model=list[LinkAccountOut])
async def list_link_accounts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    result = await db.execute(
        select(LinkAccount).where(LinkAccount.owner_id == current_user.id)
    )
    accounts = result.scalars().all()

    cutoff = datetime.utcnow() - timedelta(days=30)
    out = []
    for a in accounts:
        cnt_result = await db.execute(
            select(func.count(Customer.id)).where(Customer.link_account_id == a.id)
        )
        cnt = cnt_result.scalar() or 0

        act_result = await db.execute(
            select(func.count(Customer.id)).where(
                Customer.link_account_id == a.id,
                Customer.last_active_at >= cutoff,
            )
        )
        is_active = (act_result.scalar() or 0) > 0

        out.append(LinkAccountOut(
            id=a.id, account_id=a.account_id,
            customer_count=cnt, is_active=is_active,
        ))
    return out


@router.post("/link-accounts", status_code=201)
async def create_link_account_by_sales(
    body: LinkAccountCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    account_id = body.account_id.strip()
    if not account_id:
        raise HTTPException(400, "account_id is required")

    exists = await db.execute(select(LinkAccount).where(LinkAccount.account_id == account_id))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(400, "该微信号已存在")

    db.add(LinkAccount(account_id=account_id, owner_id=current_user.id))
    await db.commit()
    return {"message": "新增成功"}


# ===================== Customers =====================

@router.get("/customers", response_model=list[CustomerOut])
async def list_customers(
    link_account_id: str | None = Query(None),
    keyword: str | None = Query(None),
    in_pool: bool | None = Query(None),
    dealed: bool | None = Query(None),
    overdue: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _ensure_customer_added_date_schema()
    owned = await _get_owned_account_ids(current_user, db)
    if not owned:
        return []

    query = select(Customer).where(Customer.link_account_id.in_(owned))
    if link_account_id and link_account_id in owned:
        query = query.where(Customer.link_account_id == link_account_id)
    query = query.order_by(Customer.created_at.desc())

    result = await db.execute(query)
    out = []
    for c in result.scalars().all():
        item = await _build_customer_out(c, db)
        if keyword:
            k = keyword.strip().lower()
            if k:
                in_name = k in item.name.lower()
                in_phone = k in (item.phone or "").lower()
                in_client_wechat = k in (item.client_wechat_name or "").lower()
                in_tags = any(k in t.name.lower() for t in item.tags)
                if not (in_name or in_phone or in_client_wechat or in_tags):
                    continue
        if in_pool is not None and item.in_consultation_pool != in_pool:
            continue
        if overdue is not None and item.follow_up_overdue != overdue:
            continue
        if dealed is not None:
            has_dealed = any(not p.is_refunded for p in item.products)
            if has_dealed != dealed:
                continue
        out.append(item)
    return out


@router.post("/customers", response_model=CustomerOut, status_code=201)
async def create_customer(
    body: CustomerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _ensure_customer_added_date_schema()
    la_result = await db.execute(
        select(LinkAccount).where(
            LinkAccount.id == body.link_account_id,
            LinkAccount.owner_id == current_user.id,
        )
    )
    if not la_result.scalar_one_or_none():
        raise HTTPException(400, "该账号不属于你")

    duplicates = await _find_duplicate_customers(
        db=db,
        phone=body.phone,
        client_wechat_name=body.client_wechat_name,
    )
    if duplicates:
        raise HTTPException(409, "客户微信号或手机号已存在")

    added_date = date.today()
    if body.added_date:
        try:
            added_date = date.fromisoformat(body.added_date)
        except ValueError as e:
            raise HTTPException(400, "added_date must be YYYY-MM-DD") from e

    customer = Customer(
        name=_require_text(body.name, "name"),
        phone=_clean_optional_text(body.phone),
        client_wechat_name=_require_text(body.client_wechat_name, "client_wechat_name"),
        industry=body.industry,
        region=body.region,
        added_date=added_date,
        other_contact=body.other_contact.strip() if body.other_contact else None,
        link_account_id=body.link_account_id,
        entry_user_id=current_user.id,
    )
    db.add(customer)
    await db.flush()

    # Sales-side follow-up fields remain on customers.
    await db.commit()
    await db.refresh(customer)
    return await _build_customer_out(customer, db)


@router.post("/customers/check-duplicate", response_model=CustomerDuplicateCheckOut)
async def check_customer_duplicate(
    body: CustomerDuplicateCheckIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("sales")),
):
    await _ensure_customer_added_date_schema()
    matches = await _find_duplicate_customers(
        db=db,
        phone=body.phone,
        client_wechat_name=body.client_wechat_name,
        exclude_customer_id=body.exclude_customer_id,
    )
    return CustomerDuplicateCheckOut(exists=bool(matches), matches=matches)


@router.put("/customers/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: str,
    body: CustomerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _ensure_customer_added_date_schema()
    customer = await _get_my_customer(customer_id, current_user, db)

    if body.phone is not None or body.client_wechat_name is not None:
        duplicates = await _find_duplicate_customers(
            db=db,
            phone=customer.phone if body.phone is None else body.phone,
            client_wechat_name=customer.client_wechat_name if body.client_wechat_name is None else body.client_wechat_name,
            exclude_customer_id=customer.id,
        )
        if duplicates:
            raise HTTPException(409, "客户微信号或手机号已存在")

    if body.name is not None: customer.name = body.name
    if body.phone is not None: customer.phone = _clean_optional_text(body.phone)
    if body.client_wechat_name is not None:
        customer.client_wechat_name = _clean_optional_text(body.client_wechat_name)
    if body.industry is not None: customer.industry = body.industry
    if body.region is not None: customer.region = body.region
    if body.other_contact is not None:
        customer.other_contact = body.other_contact.strip() or None
    if body.added_date is not None:
        try:
            customer.added_date = date.fromisoformat(body.added_date)
        except ValueError as e:
            raise HTTPException(400, "added_date must be YYYY-MM-DD") from e

    if body.note is not None:
        customer.sales_note = body.note
    if body.next_follow_up is not None:
        customer.next_follow_up = _parse_iso_datetime(body.next_follow_up) if body.next_follow_up else None
    if body.consultation_count is not None:
        if body.consultation_count < 0 or body.consultation_count > 20:
            raise HTTPException(400, "consultation_count must be between 0 and 20")
        cc_result = await db.execute(
            select(ConsultantCustomer).where(
                ConsultantCustomer.customer_id == customer_id,
                ConsultantCustomer.status.in_(["active", "ended"]),
            ).order_by(ConsultantCustomer.updated_at.desc())
        )
        cc = cc_result.scalars().first()
        if cc is not None:
            cc.consultation_count = body.consultation_count
    if body.gifted_tuition_amount is not None:
        if body.gifted_tuition_amount < 0:
            raise HTTPException(400, "gifted_tuition_amount must be >= 0")
        customer.gifted_tuition_amount = body.gifted_tuition_amount

    await db.commit()
    await db.refresh(customer)
    return await _build_customer_out(customer, db)


@router.post("/customers/{customer_id}/tags", status_code=201)
async def add_customer_tag(
    customer_id: str,
    body: TagRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    customer = await _get_my_customer(customer_id, current_user, db)
    exist = await db.execute(
        select(CustomerTag).where(
            CustomerTag.customer_id == customer_id,
            CustomerTag.tag_id == body.tag_id,
        )
    )
    if exist.scalar_one_or_none():
        raise HTTPException(400, "标签已存在")
    db.add(CustomerTag(customer_id=customer_id, tag_id=body.tag_id))
    customer.last_active_at = datetime.utcnow()
    await db.commit()
    return {"message": "ok"}


@router.delete("/customers/{customer_id}/tags/{tag_id}", status_code=204)
async def remove_customer_tag(
    customer_id: str, tag_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    customer = await _get_my_customer(customer_id, current_user, db)
    result = await db.execute(
        select(CustomerTag).where(
            CustomerTag.customer_id == customer_id,
            CustomerTag.tag_id == tag_id,
        )
    )
    ct = result.scalar_one_or_none()
    if ct:
        await db.delete(ct)
        await db.commit()


@router.post("/customers/{customer_id}/purchase")
async def purchase_product(
    customer_id: str,
    body: PurchaseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await ensure_accounting_type_schema()
    customer = await _get_my_customer(customer_id, current_user, db)

    product_result = await db.execute(select(Product).where(Product.id == body.product_id))
    product = product_result.scalar_one_or_none()
    if not product:
        raise HTTPException(404, "产品不存在")

    now = datetime.utcnow()
    order = Order(
        customer_id=customer_id,
        product_id=body.product_id,
        sales_user_id=current_user.id,
        amount=body.amount,
        list_price=product.price,
        deal_price=body.amount,
        refund_total=0,
        status="active",
    )
    db.add(order)
    await db.flush()

    cp_result = await db.execute(
        select(CustomerProduct).where(
            CustomerProduct.customer_id == customer_id,
            CustomerProduct.product_id == body.product_id,
        )
    )
    cp = cp_result.scalar_one_or_none()
    if cp:
        cp.order_id = order.id
        cp.is_refunded = False
    else:
        db.add(CustomerProduct(
            customer_id=customer_id, product_id=body.product_id,
            order_id=order.id, is_refunded=False,
        ))

    db.add(
        CustomerCourseEnrollment(
            customer_id=customer_id,
            order_id=order.id,
            product_id=body.product_id,
            amount_paid=body.amount,
            accounting_type=SALES_SPENT_ACCOUNTING_TYPE,
            status="purchased_not_started",
            status_updated_by=current_user.id,
            status_updated_role="sales",
            status_updated_at=now,
        )
    )

    if product.is_consultation:
        exist = await db.execute(
            select(ConsultantCustomer).where(
                ConsultantCustomer.customer_id == customer_id,
                ConsultantCustomer.status == "pending",
            )
        )
        if not exist.scalar_one_or_none():
            db.add(ConsultantCustomer(
                consultant_id=None, customer_id=customer_id, status="pending",
            ))

    customer.last_active_at = now
    db.add(
        AuditLog(
            resource_type="order",
            resource_id=order.id,
            customer_id=customer_id,
            action="sales_purchase_course",
            amount_delta=body.amount,
            operator_user_id=current_user.id,
            operator_role="sales",
            note=product.name,
        )
    )
    await db.commit()
    return {"order_id": order.id, "message": "成交成功"}


@router.post("/orders/{order_id}/refund")
async def refund_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await ensure_accounting_type_schema()
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "订单不存在")

    customer = await _get_my_customer(order.customer_id, current_user, db)
    enroll_r = await db.execute(
        select(CustomerCourseEnrollment)
        .where(
            CustomerCourseEnrollment.customer_id == order.customer_id,
            CustomerCourseEnrollment.order_id == order.id,
        )
        .order_by(CustomerCourseEnrollment.created_at.desc())
    )
    enrollment = enroll_r.scalars().first()
    if enrollment is not None:
        _ensure_sales_accounting_enrollment(enrollment)

    if order.refunded_at:
        raise HTTPException(400, "已退款，不能重复操作")

    now = datetime.utcnow()
    order.refunded_at = now
    order.refund_total = order.deal_price or order.amount
    order.status = "refunded"
    order.updated_at = now

    cp_result = await db.execute(
        select(CustomerProduct).where(
            CustomerProduct.customer_id == order.customer_id,
            CustomerProduct.product_id == order.product_id,
        )
    )
    cp = cp_result.scalar_one_or_none()
    if cp:
        cp.is_refunded = True

    if enrollment is not None:
        if enrollment.status == "sales_marked_completed":
            enrollment.status = "sales_marked_completed_refunded"
        elif enrollment.status == "admin_marked_completed":
            enrollment.status = "admin_marked_completed_refunded"
        else:
            enrollment.status = "purchased_not_started_refunded"
        enrollment.status_updated_by = current_user.id
        enrollment.status_updated_role = "sales"
        enrollment.status_updated_at = now
        enrollment.updated_at = now
        enrollment.refunded_at = now

    db.add(
        AuditLog(
            resource_type="order",
            resource_id=order.id,
            customer_id=order.customer_id,
            action="sales_refund_course",
            amount_delta=-order.refund_total,
            operator_user_id=current_user.id,
            operator_role="sales",
        )
    )
    await db.commit()

    om, oy = order.created_at.month, order.created_at.year
    rm, ry = now.month, now.year
    if ry == oy and rm == om:
        impact = "当月退费，扣减本月和双月数据"
    elif (ry == oy and (rm == om + 1 or rm == om - 1)) or \
         (ry == oy + 1 and om == 12 and rm == 1) or \
         (ry == oy - 1 and om == 1 and rm == 12):
        impact = "双月内非当月退费，仅扣减双月数据"
    else:
        impact = "双月外退费，不影响任何数据"

    return {"amount": order.amount, "refunded_at": now.isoformat(), "impact": impact}


@router.post("/customers/{customer_id}/courses/{enrollment_id}/refund")
async def refund_sales_course(
    customer_id: str,
    enrollment_id: str,
    body: SalesCourseRefundIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _get_my_customer(customer_id, current_user, db)
    enrollment = await _get_enrollment_or_404(db, customer_id, enrollment_id)
    _ensure_sales_accounting_enrollment(enrollment)
    order_r = await db.execute(select(Order).where(Order.id == enrollment.order_id))
    order = order_r.scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "订单不存在")
    if order.refunded_at:
        raise HTTPException(400, "已退款，不能重复操作")
    refund_amount = min(body.refund_amount, order.deal_price or order.amount)
    if refund_amount <= 0:
        raise HTTPException(400, "refund_amount must be > 0")
    now = datetime.utcnow()
    order.refund_total = min((order.refund_total or 0) + refund_amount, order.deal_price or order.amount)
    order.status = "refunded" if order.refund_total >= (order.deal_price or order.amount) else "partially_refunded"
    order.updated_at = now
    if order.refund_total >= (order.deal_price or order.amount):
        order.refunded_at = now
    enrollment.status = "sales_marked_completed_refunded" if enrollment.status == "sales_marked_completed" else "purchased_not_started_refunded"
    enrollment.status_updated_by = current_user.id
    enrollment.status_updated_role = "sales"
    enrollment.status_updated_at = now
    enrollment.updated_at = now
    enrollment.refunded_at = now
    db.add(
        AuditLog(
            resource_type="order",
            resource_id=order.id,
            customer_id=customer_id,
            action="sales_partial_refund_course",
            amount_delta=-refund_amount,
            operator_user_id=current_user.id,
            operator_role="sales",
            note=enrollment.product_id,
        )
    )
    await db.commit()
    return {"message": "ok", "refund_amount": refund_amount, "refund_total": order.refund_total}


@router.post("/customers/{customer_id}/courses/{enrollment_id}/refund/revert")
async def revert_sales_course_refund(
    customer_id: str,
    enrollment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _get_my_customer(customer_id, current_user, db)
    enrollment = await _get_enrollment_or_404(db, customer_id, enrollment_id)
    _ensure_sales_accounting_enrollment(enrollment)
    order_r = await db.execute(select(Order).where(Order.id == enrollment.order_id))
    order = order_r.scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "订单不存在")
    if not order.refund_total:
        raise HTTPException(400, "当前没有可撤销的退款")
    now = datetime.utcnow()
    revert_amount = order.refund_total
    order.refund_total = 0
    order.refunded_at = None
    order.status = "active"
    order.updated_at = now
    if enrollment.status == "sales_marked_completed_refunded":
        enrollment.status = "sales_marked_completed"
    else:
        enrollment.status = "purchased_not_started"
    enrollment.status_updated_by = current_user.id
    enrollment.status_updated_role = "sales"
    enrollment.status_updated_at = now
    enrollment.updated_at = now
    enrollment.refunded_at = None
    db.add(
        AuditLog(
            resource_type="order",
            resource_id=order.id,
            customer_id=customer_id,
            action="sales_revert_refund_course",
            amount_delta=revert_amount,
            operator_user_id=current_user.id,
            operator_role="sales",
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.put("/customers/{customer_id}/courses/{enrollment_id}/amount")
async def update_sales_course_amount(
    customer_id: str,
    enrollment_id: str,
    body: SalesCoursePriceUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await _get_my_customer(customer_id, current_user, db)
    enrollment = await _get_enrollment_or_404(db, customer_id, enrollment_id)
    _ensure_sales_accounting_enrollment(enrollment)
    order_r = await db.execute(select(Order).where(Order.id == enrollment.order_id))
    order = order_r.scalar_one_or_none()
    if order is None:
        raise HTTPException(404, "订单不存在")
    if order.refunded_at:
        raise HTTPException(400, "已退款课程不能改价")
    now = datetime.utcnow()
    order.amount = body.amount_paid
    order.deal_price = body.amount_paid
    order.updated_at = now
    enrollment.amount_paid = body.amount_paid
    enrollment.updated_at = now
    db.add(
        AuditLog(
            resource_type="order",
            resource_id=order.id,
            customer_id=customer_id,
            action="sales_update_course_amount",
            amount_delta=body.amount_paid,
            operator_user_id=current_user.id,
            operator_role="sales",
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.put("/customers/{customer_id}/courses/{enrollment_id}/status")
async def update_sales_course_status(
    customer_id: str,
    enrollment_id: str,
    body: SalesCourseStatusUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    if body.status not in SALES_COURSE_STATUSES:
        raise HTTPException(400, "销售仅可设置前4种状态")

    await _get_my_customer(customer_id, current_user, db)
    enrollment = await _get_enrollment_or_404(db, customer_id, enrollment_id)
    _ensure_sales_accounting_enrollment(enrollment)

    enrollment.status = body.status
    enrollment.status_updated_by = current_user.id
    enrollment.status_updated_role = "sales"
    enrollment.status_updated_at = datetime.utcnow()
    enrollment.updated_at = datetime.utcnow()
    db.add(
        AuditLog(
            resource_type="course_enrollment",
            resource_id=enrollment.id,
            customer_id=customer_id,
            action="sales_update_course_status",
            changes=body.status,
            operator_user_id=current_user.id,
            operator_role="sales",
        )
    )
    await db.commit()
    return {"message": "ok"}


@router.post("/customers/{customer_id}/courses", status_code=201)
async def create_sales_course(
    customer_id: str,
    body: SalesCreateCourseIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    await ensure_accounting_type_schema()
    await _get_my_customer(customer_id, current_user, db)

    product_r = await db.execute(select(Product).where(Product.id == body.product_id))
    product = product_r.scalar_one_or_none()
    if product is None:
        raise HTTPException(404, "产品不存在")

    now = datetime.utcnow()
    amount = body.amount if body.amount is not None else product.price
    order = Order(
        customer_id=customer_id,
        product_id=body.product_id,
        sales_user_id=current_user.id,
        amount=amount,
        list_price=product.price,
        deal_price=amount,
        refund_total=0,
        status="active",
    )
    db.add(order)
    await db.flush()

    db.add(
        CustomerProduct(
            customer_id=customer_id,
            product_id=body.product_id,
            order_id=order.id,
            is_refunded=False,
        )
    )

    db.add(
        CustomerCourseEnrollment(
            customer_id=customer_id,
            order_id=order.id,
            product_id=body.product_id,
            amount_paid=amount,
            accounting_type=SALES_SPENT_ACCOUNTING_TYPE,
            status="purchased_not_started",
            status_updated_by=current_user.id,
            status_updated_role="sales",
            status_updated_at=now,
        )
    )
    db.add(
        AuditLog(
            resource_type="course_enrollment",
            resource_id=order.id,
            customer_id=customer_id,
            action="sales_add_course",
            amount_delta=amount,
            operator_user_id=current_user.id,
            operator_role="sales",
            note=product.name,
        )
    )
    await db.commit()
    return {"message": "ok"}


# ===================== Products (sales view) =====================

@router.get("/products")
async def list_sales_products(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    """销售可见的产品列表（仅上架产品）。"""
    result = await db.execute(
        select(Product).where(Product.status == "active").order_by(Product.name)
    )
    products = result.scalars().all()
    return [{"id": p.id, "name": p.name, "price": p.price, "is_consultation": p.is_consultation, "status": p.status} for p in products]


# ===================== Tags (sales view) =====================

@router.get("/tags")
async def list_sales_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    """销售可用标签列表（含分类颜色）。"""
    from app.models.tag import TagCategory, Tag as TagModel

    result = await db.execute(
        select(TagModel, TagCategory).join(TagCategory, TagModel.category_id == TagCategory.id).order_by(TagCategory.sort_order, TagModel.name)
    )
    tags = []
    for t, tc in result.all():
        tags.append({
            "id": t.id,
            "name": t.name,
            "color": tc.color,
            "category_name": tc.name,
        })
    return tags


# ===================== Dashboard =====================

@router.get("/dashboard")
async def sales_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("sales")),
):
    """销售数据复盘：6 个核心指标 + 本月成交清单。"""
    from calendar import monthrange
    from datetime import date as date_type

    now = datetime.utcnow()
    today = date_type.today()
    this_month_start = today.replace(day=1)
    last_month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    yesterday = today - timedelta(days=1)
    _, last_day = monthrange(today.year, today.month)
    this_month_end = today.replace(day=last_day)
    dual_month_start = last_month_start

    owned = await _get_owned_account_ids(current_user, db)

    # 本月新增客户数
    new_this_month = 0
    if owned:
        r = await db.execute(
            select(func.count(Customer.id)).where(
                Customer.link_account_id.in_(owned),
                Customer.created_at >= this_month_start,
            )
        )
        new_this_month = r.scalar() or 0

    # 昨日新增
    new_yesterday = 0
    if owned:
        r = await db.execute(
            select(func.count(Customer.id)).where(
                Customer.link_account_id.in_(owned),
                Customer.created_at >= yesterday,
                Customer.created_at < today,
            )
        )
        new_yesterday = r.scalar() or 0

    # 本月成交数/金额（按净额口径）
    orders_this_month = 0
    amount_this_month = 0
    if owned:
        r = await db.execute(
            select(Order).where(
                Order.sales_user_id == current_user.id,
                Order.created_at >= this_month_start,
                Order.created_at <= this_month_end,
            )
        )
        for o in r.scalars().all():
            net_amount = max(0, (o.amount or 0) - (o.refund_total or 0))
            if o.refunded_at is None:
                orders_this_month += 1
                amount_this_month += net_amount
            elif o.refunded_at.month != o.created_at.month or o.refunded_at.year != o.created_at.year:
                # 退款不在当月，当月成交数保留，金额按净额
                orders_this_month += 1
                amount_this_month += net_amount

    # 双月转化率
    dual_customers = 0
    dual_orders_customers = 0
    if owned:
        r = await db.execute(
            select(func.count(Customer.id)).where(
                Customer.link_account_id.in_(owned),
                Customer.created_at >= dual_month_start,
            )
        )
        dual_customers = r.scalar() or 0
        r = await db.execute(
            select(func.count(func.distinct(Order.customer_id))).where(
                Order.sales_user_id == current_user.id,
                Order.created_at >= dual_month_start,
            )
        )
        dual_orders_customers = r.scalar() or 0

    conversion_rate = round((dual_orders_customers / dual_customers * 100), 1) if dual_customers > 0 else 0

    # 客户总数
    total_customers = 0
    if owned:
        r = await db.execute(
            select(func.count(Customer.id)).where(Customer.link_account_id.in_(owned))
        )
        total_customers = r.scalar() or 0

    # 本月成交清单
    monthly_orders = []
    if owned:
        r = await db.execute(
            select(Order).where(
                Order.sales_user_id == current_user.id,
                Order.created_at >= this_month_start,
                Order.created_at <= this_month_end,
            ).order_by(Order.created_at.desc())
        )
        for o in r.scalars().all():
            # Customer info
            c_result = await db.execute(select(Customer).where(Customer.id == o.customer_id))
            cust = c_result.scalar_one_or_none()
            if not cust:
                continue

            # Product info
            p_result = await db.execute(select(Product).where(Product.id == o.product_id))
            prod = p_result.scalar_one_or_none()

            # LinkAccount name
            la_result = await db.execute(select(LinkAccount.account_id).where(LinkAccount.id == cust.link_account_id))
            la_name = la_result.scalar_one_or_none()

            # Tags
            tag_rows = await db.execute(
                select(Tag, TagCategory).join(TagCategory, Tag.category_id == TagCategory.id).join(
                    CustomerTag, and_(CustomerTag.tag_id == Tag.id, CustomerTag.customer_id == cust.id)
                )
            )
            tags = [{"name": t.name, "color": tc.color} for t, tc in tag_rows.all()]

            # Is this the first purchase of this product for this customer this month?
            is_first = True
            first_result = await db.execute(
                select(func.count(Order.id)).where(
                    Order.customer_id == o.customer_id,
                    Order.product_id == o.product_id,
                    Order.refunded_at == None,
                    Order.created_at < this_month_start,
                )
            )
            if (first_result.scalar() or 0) > 0:
                is_first = False

            # Check if refunded
            is_refunded = o.refunded_at is not None
            display_amount = o.amount
            if is_refunded and o.refunded_at.date() <= this_month_end and o.refunded_at.month == o.created_at.month:
                display_amount = 0  # 褰撴湀閫€娆剧殑褰撴湀璁㈠崟涓嶆樉绀洪噾棰?

            monthly_orders.append({
                "order_id": o.id,
                "order_date": o.created_at.isoformat()[:10],
                "customer_name": cust.name,
                "customer_info": (cust.industry or "") + ("路" + cust.region if cust.region else ""),
                "product_name": prod.name if prod else "已删除",
                "product_price": o.amount,
                "amount": max(0, (o.amount or 0) - (o.refund_total or 0)),
                "is_refunded": is_refunded,
                "is_first_purchase": is_first,
                "link_account_name": la_name,
                "tags": tags,
            })

    return {
        "stats": {
            "new_this_month": new_this_month,
            "new_yesterday": new_yesterday,
            "orders_this_month": orders_this_month,
            "amount_this_month": amount_this_month,
            "conversion_rate": conversion_rate,
            "total_customers": total_customers,
        },
        "monthly_orders": monthly_orders,
    }
