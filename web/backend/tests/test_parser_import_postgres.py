import asyncio
import csv
import os
from datetime import datetime

import pytest
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

for key, value in {
    "DATABASE_URL": "postgresql+asyncpg://leadcrm:test@localhost:5432/leadcrm",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef",
    "ADMIN_PASSWORD": "correct-horse-battery-staple",
}.items():
    os.environ.setdefault(key, value)

from app.database import Base
from app.models import Lead
from app.routers import parser_router as parser


def test_import_round_trip_and_rollback_in_postgres(tmp_path, monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database ending in _test")
    assert (make_url(url).database or "").endswith("_test")
    columns = ["name", "phone", "categories", "social_links", "scraped_at"]
    valid = tmp_path / "valid.csv"
    with valid.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        row = {"name": "Audit shop", "phone": "audit-phone", "categories": "shop; coffee",
               "social_links": "https://example.com/a; https://example.com/b",
               "scraped_at": "2026-09-10T09:30:00+03:00"}
        writer.writerow(row)
        writer.writerow(row)
    invalid = tmp_path / "invalid.csv"
    with invalid.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerow({"name": "Would be rolled back", "phone": "audit-rollback"})
        writer.writerow({"name": "x" * 256, "phone": "audit-invalid"})

    async def scenario():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.run_sync(Base.metadata.create_all)
                    sessions = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
                    monkeypatch.setattr(parser, "async_session", sessions)
                    # Reuse a connection already used on the application loop.
                    await connection.execute(select(func.count()).select_from(Lead))
                    await parser.import_csv_leads([valid])
                    await parser.import_csv_leads([valid])
                    async with sessions() as db:
                        leads = (await db.execute(select(Lead).where(Lead.phone == "audit-phone"))).scalars().all()
                        assert len(leads) == 1
                        assert leads[0].categories == ["shop", "coffee"]
                        assert leads[0].social_links == ["https://example.com/a", "https://example.com/b"]
                        assert leads[0].scraped_at == datetime(2026, 9, 10, 6, 30)
                    with pytest.raises(DBAPIError):
                        await parser.import_csv_leads([invalid])
                    async with sessions() as db:
                        assert await db.scalar(select(func.count()).select_from(Lead).where(Lead.phone == "audit-rollback")) == 0
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()
    asyncio.run(scenario())
