from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from app.core.deps import get_db, require_role
from app.models.link_account import LinkAccount
from app.models.customer import Customer
from app.models.user import User
from app.schemas.link_account import LinkAccountCreate, LinkAccountOut, LinkAccountTransfer

router = APIRouter(prefix="/link-accounts", tags=["link_accounts"])


@router.get("/", response_model=list[LinkAccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db), _=Depends(require_role("admin"))):
    result = await db.execute(select(LinkAccount).order_by(LinkAccount.account_id))
    accounts = result.scalars().all()
    out = []
    for a in accounts:
        owner_result = await db.execute(select(User.name).where(User.id == a.owner_id))
        owner_name = owner_result.scalar_one_or_none()
        c_result = await db.execute(select(func.count()).select_from(Customer).where(Customer.link_account_id == a.id))
        c_count = c_result.scalar() or 0
        out.append(LinkAccountOut(
            id=a.id, account_id=a.account_id, owner_id=a.owner_id,
            owner_name=owner_name, customer_count=c_count,
            created_at=a.created_at.isoformat() if a.created_at else None,
            last_transfer_at=a.last_transfer_at.isoformat() if a.last_transfer_at else None,
            last_transfer_from_owner_name=a.last_transfer_from_owner_name,
        ))
    return out


@router.post("/", response_model=LinkAccountOut, status_code=201)
async def create_account(body: LinkAccountCreate, db: AsyncSession = Depends(get_db), _=Depends(require_role("admin"))):
    existing = await db.execute(select(LinkAccount).where(LinkAccount.account_id == body.account_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Account ID already exists")
    account = LinkAccount(account_id=body.account_id, owner_id=body.owner_id)
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return LinkAccountOut(id=account.id, account_id=account.account_id, owner_id=account.owner_id)


@router.post("/{account_id}/transfer")
async def transfer_account(account_id: str, body: LinkAccountTransfer, db: AsyncSession = Depends(get_db), _=Depends(require_role("admin"))):
    result = await db.execute(select(LinkAccount).where(LinkAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    target = await db.execute(select(User).where(User.id == body.target_user_id))
    if not target.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Target user not found")

    old_owner_name_result = await db.execute(select(User.name).where(User.id == account.owner_id))
    old_owner_name = old_owner_name_result.scalar_one_or_none()
    account.owner_id = body.target_user_id
    account.last_transfer_at = datetime.utcnow()
    account.last_transfer_from_owner_name = old_owner_name

    # Update all customers under this account to new owner
    await db.execute(
        select(Customer).where(Customer.link_account_id == account_id)
    )
    customers = (await db.execute(select(Customer).where(Customer.link_account_id == account_id))).scalars().all()
    for c in customers:
        c.entry_user_id = body.target_user_id

    await db.commit()
    return {"message": "Transfer successful"}


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_role("admin"))):
    result = await db.execute(select(LinkAccount).where(LinkAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    customer_count_result = await db.execute(
        select(func.count()).select_from(Customer).where(Customer.link_account_id == account_id)
    )
    customer_count = int(customer_count_result.scalar() or 0)
    if customer_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该工作账号下仍有 {customer_count} 个客户，无法删除，请先流转或处理客户",
        )

    await db.delete(account)
    await db.commit()
