from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from app.config import settings
from app.database import init_db
from app.models import User, UserRole
from app.auth import hash_password
from app.database import async_session
from sqlalchemy import select


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
        admin = result.scalar_one_or_none()
        if not admin:
            admin = User(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                full_name="Администратор",
                role=UserRole.admin,
            )
            db.add(admin)
            await db.commit()

    try:
        yield
    finally:
        from app.routers.parser_router import cancel_parser_tasks
        from app.database import engine
        await cancel_parser_tasks()
        await engine.dispose()


Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
app = FastAPI(title="LeadCRM", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers.auth_router import router as auth_router
from app.routers.admin_router import router as admin_router
from app.routers.leads_router import router as leads_router
from app.routers.me_router import router as me_router
from app.routers.parser_router import router as parser_router
from app.routers.portfolio_router import router as portfolio_router

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(leads_router)
app.include_router(me_router)
app.include_router(parser_router)
app.include_router(portfolio_router)

app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
