import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_, func, select

from app.core.deps import get_db, require_role
from app.models.audit_log import AuditLog
from app.models.consultant_customer import ConsultantCustomer
from app.models.consultation_log import ConsultationLog
from app.models.customer import Customer
from app.models.customer_course_enrollment import CustomerCourseEnrollment
from app.models.link_account import LinkAccount
from app.models.order import CustomerProduct, Order
from app.models.product import Product
from app.models.tag import CustomerTag, Tag, TagCategory
from app.models.user import User
from app.services.accounting import ensure_accounting_type_schema, get_customer_admin_writeoff_total

router = APIRouter(prefix="/consultant", tags=["consultant"])


class TagOut(BaseModel):
    id: str
    name: str
    color: str


class ProductOut(BaseModel):
    product_id: str
    product_name: str
    is_refunded: bool
    status: str | None = None


class ConsultantBadgeOut(BaseModel):
    consultant_id: str
    consultant_name: str
    is_me: bool


class ConsultantCustomerOut(BaseModel):
    relation_id: str
    customer_id: str
    customer_name: str
    customer_info: str
    client_wechat_name: str | None
    tags: list[TagOut]
    products: list[ProductOut]
    note: str | None
    sales_note: str | None
    tuition_balance: float
    next_consultation: str | None
    next_consultation_status: str
    next_consultation_label: str
    period_label: str
    period_status: str
    consultation_count: int
    is_refunded_customer: bool
    row_tone: str
    collaborators: list[ConsultantBadgeOut]


class PoolItemOut(BaseModel):
    pool_id: str
    customer_id: str
    customer_name: str
    phone: str | None
    client_wechat_name: str | None
    wechat_name: str | None
    source_channel: str | None
    deal_product: str | None
    deal_amount: int | None
    pool_entered_at: str
    tags: list[TagOut]
    products: list[ProductOut]
    sales_name: str | None
    consultants: list[ConsultantBadgeOut]
    service_status: str
    consultant_count: int
    can_claim: bool
    can_join: bool


class LogItemOut(BaseModel):
    id: str
    customer_id: str
    consultant_id: str
    consultant_name: str
    is_me: bool
    editable: bool
    log_date: str
    duration: int
    summary: str | None
    created_at: str
    updated_at: str


class LogDetailOut(LogItemOut):
    content: str | None


class ConsultantCustomerDetailOut(BaseModel):
    customer_id: str
    customer_name: str
    phone: str | None
    client_wechat_name: str | None
    customer_info: str
    sales_name: str | None
    wechat_name: str | None
    tags: list[TagOut]
    products: list[ProductOut]
    consultation_count: int
    total_duration: int
    latest_log_at: str | None


class UpdateConsultantCustomerIn(BaseModel):
    note: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    next_consultation: str | None = None
    consultation_count: int | None = None


class UpsertLogIn(BaseModel):
    log_date: str
    duration: int
    content: str | None = None
    summary: str | None = None


class DashboardOut(BaseModel):
    service_customers: int
    co_service_customers: int
    meetings_this_month: int
    meetings_last_month: int
    active_customers_with_meeting_this_month: int
    active_customers_without_meeting_this_month: int
    avg_meeting_per_service_customer: float
    total_meetings: int
    label_distribution: list[dict]


class TagAssignIn(BaseModel):
    tag_id: str


async def _write_audit_log(
    db: AsyncSession,
    *,
    resource_type: str,
    resource_id: str,
    customer_id: str | None,
    action: str,
    operator: User,
    changes: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            resource_type=resource_type,
            resource_id=resource_id,
            customer_id=customer_id,
            action=action,
            operator_user_id=operator.id,
            operator_role=operator.role,
            changes=json.dumps(changes, ensure_ascii=False) if changes is not None else None,
        )
    )


def _customer_info(customer: Customer) -> str:
    industry = customer.industry or ""
    region = customer.region or ""
    if industry and region:
        return f"{industry}-{region}"
    return industry or region


def _dt_status(next_at: datetime | None) -> tuple[str, str, str]:
    if next_at is None:
        return "unset", "未设置", "normal"

    now = datetime.utcnow()
    today = now.date()
    d = next_at.date()
    if d < today:
        days = (today - d).days
        return "overdue", f"已过期\n{days}天", "danger"
    if d == today:
        return "today", f"今天\n{next_at.strftime('%H:%M')}", "info"

    days = (d - today).days
    if days <= 2:
        return "soon", f"{next_at.strftime('%m/%d')}\n{days}天后", "warn"
    return "future", f"{next_at.strftime('%m/%d')}\n{days}天后", "normal"


def _period_status(start: date | None, end: date | None) -> tuple[str, str]:
    if not start or not end:
        return "进行中", "active"

    today = date.today()
    if today > end:
        return "已超期", "overdue"

    remain = (end - today).days
    if remain <= 30:
        return "临近到期", "near_expiry"
    return "进行中", "active"


def _parse_iso_datetime(value: str) -> datetime:
    raw = value.strip()
    # Python fromisoformat does not accept trailing "Z" on some runtimes.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    # Persist as naive UTC for consistency with existing datetime.utcnow() usage.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def _build_tags(customer_id: str, db: AsyncSession) -> list[TagOut]:
    rows = await db.execute(
        select(Tag, TagCategory)
        .join(TagCategory, Tag.category_id == TagCategory.id)
        .join(CustomerTag, and_(CustomerTag.tag_id == Tag.id, CustomerTag.customer_id == customer_id))
        .order_by(Tag.name)
    )
    return [TagOut(id=t.id, name=t.name, color=tc.color) for t, tc in rows.all()]


async def _build_products(customer_id: str, db: AsyncSession) -> tuple[list[ProductOut], bool]:
    await ensure_accounting_type_schema()
    rows = await db.execute(
        select(CustomerCourseEnrollment, Product)
        .join(Product, Product.id == CustomerCourseEnrollment.product_id)
        .where(CustomerCourseEnrollment.customer_id == customer_id)
        .order_by(CustomerCourseEnrollment.created_at.desc())
    )
    items: list[ProductOut] = []
    refunded = False
    for enrollment, p in rows.all():
        is_refunded = "refunded" in (enrollment.status or "")
        if is_refunded:
            refunded = True
        items.append(
            ProductOut(
                product_id=p.id,
                product_name=p.name,
                is_refunded=is_refunded,
                status=enrollment.status,
            )
        )
    return items, refunded


async def _build_consultants(customer_id: str, current_user_id: str, db: AsyncSession) -> list[ConsultantBadgeOut]:
    rows = await db.execute(
        select(ConsultantCustomer, User)
        .join(User, User.id == ConsultantCustomer.consultant_id)
        .where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.status == "active",
            ConsultantCustomer.consultant_id.is_not(None),
            User.role == "consultant",
        )
        .order_by(User.name)
    )
    return [
        ConsultantBadgeOut(
            consultant_id=u.id,
            consultant_name=u.name,
            is_me=(u.id == current_user_id),
        )
        for _, u in rows.all()
    ]


async def _ensure_access(customer_id: str, current_user: User, db: AsyncSession) -> ConsultantCustomer:
    row = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "active",
        )
    )
    relation = row.scalar_one_or_none()
    if relation is None:
        raise HTTPException(403, "你不是该客户的在服务咨询师")
    return relation


async def _ensure_consultant_tag(tag_id: str, db: AsyncSession) -> Tag:
    row = await db.execute(
        select(Tag, TagCategory)
        .join(TagCategory, Tag.category_id == TagCategory.id)
        .where(Tag.id == tag_id)
    )
    pair = row.first()
    if pair is None:
        raise HTTPException(404, "标签不存在")
    tag, category = pair
    if category.group != "consultant":
        raise HTTPException(403, "仅允许操作咨询师标签")
    return tag


@router.get("/customers", response_model=list[ConsultantCustomerOut])
async def consultant_customers(
    keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "sales", "consultant")),
):
    await ensure_accounting_type_schema()
    rows = await db.execute(
        select(ConsultantCustomer, Customer)
        .join(Customer, Customer.id == ConsultantCustomer.customer_id)
        .where(
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "active",
        )
        .order_by(ConsultantCustomer.next_consultation.is_(None), ConsultantCustomer.next_consultation.asc(), Customer.created_at.desc())
    )

    out: list[ConsultantCustomerOut] = []
    for rel, c in rows.all():
        tags = await _build_tags(c.id, db)
        if keyword:
            k = keyword.strip().lower()
            if k and k not in c.name.lower() and not any(k in t.name.lower() for t in tags):
                continue

        products, refunded = await _build_products(c.id, db)
        collaborators = await _build_consultants(c.id, current_user.id, db)
        consultation_count_r = await db.execute(
            select(func.count(ConsultationLog.id)).where(ConsultationLog.customer_id == c.id)
        )
        consultation_count = int(consultation_count_r.scalar() or 0)
        total_spent = await get_customer_admin_writeoff_total(db, c.id)
        gifted = Decimal(c.gifted_tuition_amount or 0)
        tuition_balance = max(Decimal('0'), gifted - total_spent)

        status_key, status_label, row_tone = _dt_status(rel.next_consultation)
        period_state, period_key = _period_status(rel.start_date, rel.end_date)
        period = f"{rel.start_date.strftime('%m/%d') if rel.start_date else '--/--'}-{rel.end_date.strftime('%m/%d') if rel.end_date else '--/--'}"
        period_label = f"{period}\n{period_state}"

        if refunded:
            period_label = f"{period}\n已退款"
            period_key = "refunded"

        out.append(
            ConsultantCustomerOut(
                relation_id=rel.id,
                customer_id=c.id,
                customer_name=c.name,
                customer_info=_customer_info(c),
                client_wechat_name=c.client_wechat_name,
                tags=tags,
                products=products,
                note=rel.note,
                sales_note=c.sales_note,
                tuition_balance=float(tuition_balance),
                next_consultation=rel.next_consultation.isoformat() if rel.next_consultation else None,
                next_consultation_status=status_key,
                next_consultation_label=status_label,
                period_label=period_label,
                period_status=period_key,
                consultation_count=consultation_count,
                is_refunded_customer=refunded,
                row_tone=row_tone if not refunded else "muted",
                collaborators=collaborators,
            )
        )

    return out


@router.get("/tags")
async def consultant_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    rows = await db.execute(
        select(Tag, TagCategory)
        .join(TagCategory, Tag.category_id == TagCategory.id)
        .where(TagCategory.group == "consultant")
        .order_by(TagCategory.sort_order, Tag.name)
    )
    return [
        {"id": t.id, "name": t.name, "color": tc.color, "category_name": tc.name}
        for t, tc in rows.all()
    ]


@router.post("/customers/{customer_id}/tags", status_code=201)
async def add_consultant_tag(
    customer_id: str,
    body: TagAssignIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    await _ensure_access(customer_id, current_user, db)
    await _ensure_consultant_tag(body.tag_id, db)
    exists = await db.execute(
        select(CustomerTag).where(
            CustomerTag.customer_id == customer_id,
            CustomerTag.tag_id == body.tag_id,
        )
    )
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(400, "标签已存在")
    db.add(CustomerTag(customer_id=customer_id, tag_id=body.tag_id))
    await db.commit()
    return {"message": "ok"}


@router.delete("/customers/{customer_id}/tags/{tag_id}", status_code=204)
async def remove_consultant_tag(
    customer_id: str,
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    await _ensure_access(customer_id, current_user, db)
    await _ensure_consultant_tag(tag_id, db)
    row = await db.execute(
        select(CustomerTag).where(
            CustomerTag.customer_id == customer_id,
            CustomerTag.tag_id == tag_id,
        )
    )
    rel = row.scalar_one_or_none()
    if rel is not None:
        await db.delete(rel)
        await db.commit()


@router.put("/customers/{customer_id}")
async def update_consultant_customer(
    customer_id: str,
    body: UpdateConsultantCustomerIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    rel = await _ensure_access(customer_id, current_user, db)

    if body.note is not None:
        rel.note = body.note
    if body.start_date is not None:
        rel.start_date = date.fromisoformat(body.start_date)
    if body.end_date is not None:
        rel.end_date = date.fromisoformat(body.end_date)
    if body.next_consultation is not None:
        try:
            rel.next_consultation = _parse_iso_datetime(body.next_consultation)
        except Exception:
            raise HTTPException(400, "下次咨询时间格式错误")
    if body.consultation_count is not None:
        if body.consultation_count < 0 or body.consultation_count > 20:
            raise HTTPException(400, "咨询次数必须在0到20之间")
        rel.consultation_count = body.consultation_count

    rel.updated_at = datetime.utcnow()
    await db.commit()
    return {"message": "ok"}


@router.post("/customers/{customer_id}/return-to-pool")
async def return_to_pool(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    rel = await _ensure_access(customer_id, current_user, db)
    rel.status = "ended"
    rel.updated_at = datetime.utcnow()

    pending = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.status == "pending",
        )
    )
    if pending.scalar_one_or_none() is None:
        db.add(ConsultantCustomer(customer_id=customer_id, consultant_id=None, status="pending"))

    await db.commit()
    return {"message": "ok"}


@router.get("/pool", response_model=list[PoolItemOut])
async def consultant_pool(
    keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    customers_r = await db.execute(select(Customer).order_by(Customer.created_at.desc()))
    out: list[PoolItemOut] = []

    for c in customers_r.scalars().all():
        tags = await _build_tags(c.id, db)
        if keyword:
            k = keyword.strip().lower()
            if k and k not in c.name.lower() and not any(k in t.name.lower() for t in tags):
                continue

        products, _ = await _build_products(c.id, db)
        consultants = await _build_consultants(c.id, current_user.id, db)

        pending_r = await db.execute(
            select(ConsultantCustomer)
            .where(ConsultantCustomer.customer_id == c.id, ConsultantCustomer.status == "pending")
            .order_by(ConsultantCustomer.created_at.asc())
        )
        pending_rel = pending_r.scalars().first()
        if pending_rel:
            pool_rel = pending_rel
        else:
            any_rel_r = await db.execute(
                select(ConsultantCustomer)
                .where(
                    ConsultantCustomer.customer_id == c.id,
                    ConsultantCustomer.status.in_(["active", "ended"]),
                )
                .order_by(ConsultantCustomer.created_at.asc())
            )
            pool_rel = any_rel_r.scalars().first()

        pool_entered_at = (pool_rel.created_at if pool_rel else c.created_at).isoformat()
        pool_id = pool_rel.id if pool_rel else ""

        consultant_count = len(consultants)
        my_joined = any(it.is_me for it in consultants)
        if consultant_count == 0:
            service_status = "unclaimed"
            can_claim = True
            can_join = False
        elif my_joined:
            service_status = "joined_by_me"
            can_claim = False
            can_join = False
        else:
            service_status = "claimed_by_others"
            can_claim = False
            can_join = True

        sales_name = None
        sales_r = await db.execute(select(User.name).where(User.id == c.entry_user_id))
        sales_name = sales_r.scalar_one_or_none()
        link_r = await db.execute(select(LinkAccount.account_id).where(LinkAccount.id == c.link_account_id))
        wechat_name = link_r.scalar_one_or_none()
        source_channel = c.industry or c.region or None
        deal_product = products[0].product_name if products else None
        deal_amount = None

        out.append(
            PoolItemOut(
                pool_id=pool_id,
                customer_id=c.id,
                customer_name=c.name,
                phone=c.phone,
                client_wechat_name=c.client_wechat_name,
                wechat_name=wechat_name,
                source_channel=source_channel,
                deal_product=deal_product,
                deal_amount=deal_amount,
                pool_entered_at=pool_entered_at,
                tags=tags,
                products=products,
                sales_name=sales_name,
                consultants=consultants,
                service_status=service_status,
                consultant_count=consultant_count,
                can_claim=can_claim,
                can_join=can_join,
            )
        )

    out.sort(
        key=lambda i: (
            0 if i.service_status == "unclaimed" else 1,
            i.pool_entered_at,
        )
    )
    return out


@router.post("/pool/{customer_id}/claim")
async def claim_from_pool(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    exists = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "active",
        )
    )
    if exists.scalar_one_or_none() is not None:
        return {"message": "already_active"}

    ended = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "ended",
        )
    )
    ended_rel = ended.scalar_one_or_none()
    if ended_rel:
        ended_rel.status = "active"
        ended_rel.updated_at = datetime.utcnow()
    else:
        db.add(ConsultantCustomer(customer_id=customer_id, consultant_id=current_user.id, status="active"))

    pendings = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.status == "pending",
        )
    )
    for p in pendings.scalars().all():
        await db.delete(p)

    await db.commit()
    return {"message": "ok"}


@router.post("/pool/{customer_id}/join")
async def join_pool_service(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    exists = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "active",
        )
    )
    if exists.scalar_one_or_none() is not None:
        return {"message": "already_active"}

    ended = await db.execute(
        select(ConsultantCustomer).where(
            ConsultantCustomer.customer_id == customer_id,
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "ended",
        )
    )
    ended_rel = ended.scalar_one_or_none()
    if ended_rel:
        ended_rel.status = "active"
        ended_rel.updated_at = datetime.utcnow()
    else:
        db.add(ConsultantCustomer(customer_id=customer_id, consultant_id=current_user.id, status="active"))

    await db.commit()
    return {"message": "ok"}


@router.get("/customers/{customer_id}/logs", response_model=list[LogItemOut])
@router.get("/customers/{customer_id}/consultation-logs", response_model=list[LogItemOut])
async def list_logs(
    customer_id: str,
    keyword: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    mine_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "sales", "consultant")),
):
    conditions = [ConsultationLog.customer_id == customer_id]
    if mine_only and current_user.role == "consultant":
        conditions.append(ConsultationLog.consultant_id == current_user.id)
    if date_from:
        conditions.append(ConsultationLog.log_date >= date.fromisoformat(date_from))
    if date_to:
        conditions.append(ConsultationLog.log_date <= date.fromisoformat(date_to))
    if keyword and keyword.strip():
        k = f"%{keyword.strip()}%"
        conditions.append(
            ConsultationLog.summary.ilike(k)
        )

    rows = await db.execute(
        select(
            ConsultationLog.id,
            ConsultationLog.customer_id,
            ConsultationLog.consultant_id,
            ConsultationLog.log_date,
            ConsultationLog.duration,
            ConsultationLog.summary,
            ConsultationLog.created_at,
            ConsultationLog.updated_at,
            User.name,
        )
        .join(User, User.id == ConsultationLog.consultant_id)
        .where(*conditions)
        .order_by(ConsultationLog.log_date.desc(), ConsultationLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [
        LogItemOut(
            id=log_id,
            customer_id=log_customer_id,
            consultant_id=log_consultant_id,
            consultant_name=consultant_name,
            is_me=(log_consultant_id == current_user.id),
            editable=(current_user.role == "consultant" and log_consultant_id == current_user.id),
            log_date=log_date.isoformat(),
            duration=log_duration,
            summary=log_summary,
            created_at=log_created_at.isoformat(),
            updated_at=log_updated_at.isoformat(),
        )
        for (
            log_id,
            log_customer_id,
            log_consultant_id,
            log_date,
            log_duration,
            log_summary,
            log_created_at,
            log_updated_at,
            consultant_name,
        ) in rows.all()
    ]


@router.get("/logs/{log_id}", response_model=LogDetailOut)
async def get_log_detail(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "sales", "consultant")),
):
    row = await db.execute(
        select(ConsultationLog, User.name)
        .join(User, User.id == ConsultationLog.consultant_id)
        .where(ConsultationLog.id == log_id)
    )
    pair = row.first()
    if pair is None:
        raise HTTPException(404, "咨询日志不存在")
    log, consultant_name = pair

    if current_user.role == "consultant":
        await _ensure_access(log.customer_id, current_user, db)

    return LogDetailOut(
        id=log.id,
        customer_id=log.customer_id,
        consultant_id=log.consultant_id,
        consultant_name=consultant_name,
        is_me=(log.consultant_id == current_user.id),
        editable=(current_user.role == "consultant" and log.consultant_id == current_user.id),
        log_date=log.log_date.isoformat(),
        duration=log.duration,
        summary=log.summary,
        content=log.content,
        created_at=log.created_at.isoformat(),
        updated_at=log.updated_at.isoformat(),
    )


@router.get("/customers/{customer_id}/detail", response_model=ConsultantCustomerDetailOut)
async def customer_detail(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "sales", "consultant")),
):
    if current_user.role == "consultant":
        await _ensure_access(customer_id, current_user, db)

    customer_r = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = customer_r.scalar_one_or_none()
    if customer is None:
        raise HTTPException(404, "customer not found")

    tags = await _build_tags(customer_id, db)
    products, _ = await _build_products(customer_id, db)

    sales_r = await db.execute(select(User.name).where(User.id == customer.entry_user_id))
    sales_name = sales_r.scalar_one_or_none()
    link_r = await db.execute(select(LinkAccount.account_id).where(LinkAccount.id == customer.link_account_id))
    wechat_name = link_r.scalar_one_or_none()

    count_r = await db.execute(select(func.count(ConsultationLog.id)).where(ConsultationLog.customer_id == customer_id))
    duration_r = await db.execute(
        select(func.coalesce(func.sum(ConsultationLog.duration), 0)).where(ConsultationLog.customer_id == customer_id)
    )
    latest_r = await db.execute(
        select(ConsultationLog.created_at)
        .where(ConsultationLog.customer_id == customer_id)
        .order_by(ConsultationLog.created_at.desc())
        .limit(1)
    )
    latest_at = latest_r.scalar_one_or_none()

    return ConsultantCustomerDetailOut(
        customer_id=customer.id,
        customer_name=customer.name,
        phone=customer.phone,
        client_wechat_name=customer.client_wechat_name,
        customer_info=_customer_info(customer),
        sales_name=sales_name,
        wechat_name=wechat_name,
        tags=tags,
        products=products,
        consultation_count=int(count_r.scalar() or 0),
        total_duration=int(duration_r.scalar() or 0),
        latest_log_at=latest_at.isoformat() if latest_at else None,
    )


@router.post("/customers/{customer_id}/logs", response_model=LogDetailOut)
async def create_log(
    customer_id: str,
    body: UpsertLogIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    await _ensure_access(customer_id, current_user, db)

    log = ConsultationLog(
        customer_id=customer_id,
        consultant_id=current_user.id,
        log_date=date.fromisoformat(body.log_date),
        duration=body.duration,
        content=body.content,
        summary=body.summary,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    await _write_audit_log(
        db,
        resource_type="consultation_log",
        resource_id=log.id,
        customer_id=customer_id,
        action="log_created",
        operator=current_user,
        changes={
            "log_date": log.log_date.isoformat(),
            "duration": log.duration,
            "summary": log.summary,
            "content": log.content,
        },
    )
    await db.commit()

    return LogDetailOut(
        id=log.id,
        customer_id=log.customer_id,
        consultant_id=current_user.id,
        consultant_name=current_user.name,
        is_me=True,
        editable=True,
        log_date=log.log_date.isoformat(),
        duration=log.duration,
        summary=log.summary,
        content=log.content,
        created_at=log.created_at.isoformat(),
        updated_at=log.updated_at.isoformat(),
    )


@router.put("/logs/{log_id}", response_model=LogDetailOut)
async def update_log(
    log_id: str,
    body: UpsertLogIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    row = await db.execute(select(ConsultationLog).where(ConsultationLog.id == log_id))
    log = row.scalar_one_or_none()
    if log is None:
        raise HTTPException(404, "?????")
    if log.consultant_id != current_user.id:
        raise HTTPException(403, "?????????")

    before = {
        "log_date": log.log_date.isoformat(),
        "duration": log.duration,
        "summary": log.summary,
        "content": log.content,
    }

    log.log_date = date.fromisoformat(body.log_date)
    log.duration = body.duration
    log.content = body.content
    log.summary = body.summary
    log.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(log)
    await _write_audit_log(
        db,
        resource_type="consultation_log",
        resource_id=log.id,
        customer_id=log.customer_id,
        action="log_updated",
        operator=current_user,
        changes={
            "before": before,
            "after": {
                "log_date": log.log_date.isoformat(),
                "duration": log.duration,
                "summary": log.summary,
                "content": log.content,
            },
        },
    )
    await db.commit()

    return LogDetailOut(
        id=log.id,
        customer_id=log.customer_id,
        consultant_id=current_user.id,
        consultant_name=current_user.name,
        is_me=True,
        editable=True,
        log_date=log.log_date.isoformat(),
        duration=log.duration,
        summary=log.summary,
        content=log.content,
        created_at=log.created_at.isoformat(),
        updated_at=log.updated_at.isoformat(),
    )

@router.get("/dashboard", response_model=DashboardOut)
async def consultant_dashboard(
    month: str | None = Query(None, description="YYYY-MM"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("consultant")),
):
    today = date.today()
    if month:
        y, m = month.split("-")
        target = date(int(y), int(m), 1)
    else:
        target = date(today.year, today.month, 1)

    if target.month == 12:
        next_month = date(target.year + 1, 1, 1)
    else:
        next_month = date(target.year, target.month + 1, 1)

    if target.month == 1:
        prev_month = date(target.year - 1, 12, 1)
    else:
        prev_month = date(target.year, target.month - 1, 1)

    active_rows = await db.execute(
        select(ConsultantCustomer.customer_id)
        .where(
            ConsultantCustomer.consultant_id == current_user.id,
            ConsultantCustomer.status == "active",
        )
    )
    active_customer_ids = [r[0] for r in active_rows.all()]
    service_customers = len(set(active_customer_ids))

    co_service = 0
    for cid in set(active_customer_ids):
        others = await db.execute(
            select(func.count(ConsultantCustomer.id)).where(
                ConsultantCustomer.customer_id == cid,
                ConsultantCustomer.status == "active",
                ConsultantCustomer.consultant_id.is_not(None),
            )
        )
        if (others.scalar() or 0) > 1:
            co_service += 1

    meetings_this_month_r = await db.execute(
        select(func.count(ConsultationLog.id)).where(
            ConsultationLog.consultant_id == current_user.id,
            ConsultationLog.log_date >= target,
            ConsultationLog.log_date < next_month,
        )
    )
    meetings_this_month = meetings_this_month_r.scalar() or 0

    meetings_last_month_r = await db.execute(
        select(func.count(ConsultationLog.id)).where(
            ConsultationLog.consultant_id == current_user.id,
            ConsultationLog.log_date >= prev_month,
            ConsultationLog.log_date < target,
        )
    )
    meetings_last_month = meetings_last_month_r.scalar() or 0

    this_month_customers_r = await db.execute(
        select(func.count(func.distinct(ConsultationLog.customer_id))).where(
            ConsultationLog.consultant_id == current_user.id,
            ConsultationLog.log_date >= target,
            ConsultationLog.log_date < next_month,
        )
    )
    active_customers_with_meeting_this_month = this_month_customers_r.scalar() or 0
    active_customers_without_meeting_this_month = max(service_customers - active_customers_with_meeting_this_month, 0)

    total_meetings_r = await db.execute(
        select(func.count(ConsultationLog.id)).where(ConsultationLog.consultant_id == current_user.id)
    )
    total_meetings = total_meetings_r.scalar() or 0
    avg_meeting_per_service_customer = round(total_meetings / service_customers, 1) if service_customers else 0.0

    distribution: list[dict] = []
    if active_customer_ids:
        tag_rows = await db.execute(
            select(Tag.id, Tag.name, TagCategory.color, func.count(func.distinct(CustomerTag.customer_id)))
            .join(TagCategory, Tag.category_id == TagCategory.id)
            .join(CustomerTag, CustomerTag.tag_id == Tag.id)
            .where(CustomerTag.customer_id.in_(list(set(active_customer_ids))))
            .group_by(Tag.id, Tag.name, TagCategory.color)
            .order_by(func.count(func.distinct(CustomerTag.customer_id)).desc())
        )
        for tag_id, name, color, cnt in tag_rows.all():
            percent = round((cnt / service_customers) * 100, 1) if service_customers else 0
            distribution.append({
                "tag_id": tag_id,
                "name": name,
                "color": color,
                "count": cnt,
                "percent": percent,
            })

    return DashboardOut(
        service_customers=service_customers,
        co_service_customers=co_service,
        meetings_this_month=meetings_this_month,
        meetings_last_month=meetings_last_month,
        active_customers_with_meeting_this_month=active_customers_with_meeting_this_month,
        active_customers_without_meeting_this_month=active_customers_without_meeting_this_month,
        avg_meeting_per_service_customer=avg_meeting_per_service_customer,
        total_meetings=total_meetings,
        label_distribution=distribution,
    )
