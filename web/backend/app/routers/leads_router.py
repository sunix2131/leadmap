from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from app.database import get_db
from app.models import User, UserRole, Lead, LeadStatus, CallLog
from app.schemas import LeadOut, LeadStatusUpdate, LeadAssign, LeadBatchImport
from app.auth import get_current_user, require_admin
from app.rate_limiter import rate_limiter
from app.import_values import parse_scraped_at

router = APIRouter(prefix="/api/leads", tags=["leads"])


def lead_to_dict(lead: Lead) -> dict:
    d = {c.name: getattr(lead, c.name) for c in Lead.__table__.columns}
    d["assigned_seller_name"] = lead.assigned_seller.full_name if lead.assigned_seller else None
    d["status"] = lead.status.value if isinstance(lead.status, LeadStatus) else lead.status
    return d


@router.get("", response_model=list[LeadOut])
async def list_leads(
    status: Optional[str] = None,
    search: Optional[str] = None,
    assigned_to: Optional[int] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(Lead)

    if user.role == UserRole.seller:
        query = query.where(Lead.assigned_to == user.id)
    elif assigned_to is not None:
        query = query.where(Lead.assigned_to == assigned_to)

    if status:
        query = query.where(Lead.status == LeadStatus(status))

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Lead.name.ilike(search_term),
                Lead.phone.ilike(search_term),
                Lead.address.ilike(search_term),
                Lead.categories.cast(str).ilike(search_term),
            )
        )

    query = query.order_by(Lead.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    leads = result.scalars().all()
    return [lead_to_dict(l) for l in leads]


@router.get("/count")
async def count_leads(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(func.count(Lead.id))

    if user.role == UserRole.seller:
        query = query.where(Lead.assigned_to == user.id)

    if status:
        query = query.where(Lead.status == LeadStatus(status))

    result = await db.execute(query)
    return {"count": result.scalar() or 0}


@router.get("/rate-limit")
async def get_rate_limit(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    info = await rate_limiter.get_info(db, user.id)
    return info


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Лид не найден")

    if user.role == UserRole.seller and lead.assigned_to != user.id:
        raise HTTPException(status_code=403, detail="Этот лид не назначен вам")

    return lead_to_dict(lead)


@router.post("/{lead_id}/assign")
async def assign_lead(
    lead_id: int,
    data: LeadAssign,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Лид не найден")

    seller_result = await db.execute(select(User).where(User.id == data.seller_id, User.role == UserRole.seller))
    seller = seller_result.scalar_one_or_none()
    if not seller:
        raise HTTPException(status_code=400, detail="Продавец не найден")

    lead.assigned_to = data.seller_id
    lead.assigned_at = datetime.utcnow()
    lead.status = LeadStatus.assigned
    lead.updated_at = datetime.utcnow()

    log = CallLog(
        lead_id=lead.id,
        seller_id=admin.id,
        action="assigned",
        new_status="assigned",
        note=f"Назначен продавцу: {seller.full_name}",
    )
    db.add(log)
    await db.flush()
    return {"ok": True}


@router.post("/{lead_id}/status")
async def update_lead_status(
    lead_id: int,
    data: LeadStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Лид не найден")

    if user.role == UserRole.seller and lead.assigned_to != user.id:
        raise HTTPException(status_code=403, detail="Этот лид не назначен вам")

    new_status = LeadStatus(data.status)
    terminal_statuses = {LeadStatus.confirmed, LeadStatus.declined, LeadStatus.followup, LeadStatus.no_answer}

    if new_status in terminal_statuses and user.role == UserRole.seller:
        allowed, info = await rate_limiter.check(db, user.id)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Превышен лимит обновлений. Осталось: {info['actions_remaining']}. "
                       f"Лимит обновится через час.",
            )

    old_status = lead.status.value if isinstance(lead.status, LeadStatus) else lead.status
    lead.status = new_status
    lead.updated_at = datetime.utcnow()
    if data.note:
        lead.notes = (lead.notes + "\n" if lead.notes else "") + f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}] {data.note}"

    action_map = {
        LeadStatus.confirmed: "status_confirmed",
        LeadStatus.declined: "status_declined",
        LeadStatus.followup: "status_followup",
        LeadStatus.no_answer: "status_no_answer",
        LeadStatus.calling: "status_calling",
        LeadStatus.assigned: "status_assigned",
        LeadStatus.waiting: "status_waiting",
    }

    log = CallLog(
        lead_id=lead.id,
        seller_id=user.id,
        action=action_map.get(new_status, "status_change"),
        old_status=old_status,
        new_status=data.status,
        note=data.note,
    )
    db.add(log)
    await db.flush()
    return {"ok": True}


@router.post("/{lead_id}/unassign")
async def unassign_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Лид не найден")

    lead.assigned_to = None
    lead.assigned_at = None
    lead.status = LeadStatus.new
    lead.updated_at = datetime.utcnow()
    await db.flush()
    return {"ok": True}


@router.post("/batch-assign")
async def batch_assign_leads(
    seller_id: int = Query(...),
    count: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    seller_result = await db.execute(select(User).where(User.id == seller_id, User.role == UserRole.seller))
    seller = seller_result.scalar_one_or_none()
    if not seller:
        raise HTTPException(status_code=400, detail="Продавец не найден")

    result = await db.execute(
        select(Lead)
        .where(Lead.status == LeadStatus.new, Lead.assigned_to.is_(None))
        .order_by(Lead.scraped_at.desc())
        .limit(count)
    )
    leads = result.scalars().all()

    now = datetime.utcnow()
    for lead in leads:
        lead.assigned_to = seller_id
        lead.assigned_at = now
        lead.status = LeadStatus.assigned
        lead.updated_at = now

    await db.flush()
    return {"assigned": len(leads)}


@router.post("/import")
async def import_leads(
    data: LeadBatchImport,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    imported = 0
    for item in data.leads:
        existing = None
        if item.get("phone"):
            result = await db.execute(select(Lead).where(Lead.phone == item["phone"], Lead.name == item.get("name", "")))
            existing = result.scalar_one_or_none()

        if existing:
            continue

        lead = Lead(
            name=item.get("name", "Без названия"),
            categories=item.get("categories", []),
            address=item.get("address", ""),
            phone=item.get("phone", ""),
            email=item.get("email", ""),
            website=item.get("website", ""),
            website_status=item.get("website_status", "unknown"),
            website_platform=item.get("website_platform", ""),
            social_links=item.get("social_links", []),
            rating=item.get("rating", ""),
            reviews=item.get("reviews", ""),
            hours=item.get("hours", ""),
            yandex_url=item.get("yandex_url", ""),
            source=item.get("source", "yandex_maps"),
            scraped_at=parse_scraped_at(item.get("scraped_at")),
        )
        db.add(lead)
        imported += 1

    await db.flush()
    return {"imported": imported}


@router.get("/{lead_id}/logs")
async def get_lead_logs(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(CallLog)
        .where(CallLog.lead_id == lead_id)
        .order_by(CallLog.created_at.desc())
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "action": log.action,
            "old_status": log.old_status,
            "new_status": log.new_status,
            "note": log.note,
            "created_at": log.created_at.isoformat(),
            "seller_name": log.seller.full_name if log.seller else None,
        }
        for log in logs
    ]
