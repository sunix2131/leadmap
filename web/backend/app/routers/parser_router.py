import asyncio
import csv
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import require_admin
from app.models import User, Lead
from app.config import settings
from app.database import async_session

router = APIRouter(prefix="/api/parser", tags=["parser"])
logger = logging.getLogger(__name__)

tasks = {}
MAX_TASK_OUTPUT_CHARS = 200_000
MAX_TASK_HISTORY = 100
ACTIVE_STATUSES = {"pending", "running"}


class ParserRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=20, ge=1, le=1000)
    mode: Literal["scrape", "run"] = "scrape"


class TaskResponse(BaseModel):
    task_id: str
    status: str
    started_at: str
    mode: Literal["scrape", "run"]
    output: Optional[str] = None


def output_snapshot(parser_dir: Path) -> dict[Path, tuple[int, int]]:
    result = {}
    for path in (parser_dir / "out").glob("*.csv"):
        stat = path.stat()
        result[path] = (stat.st_mtime_ns, stat.st_size)
    return result


async def import_csv_leads(csv_files: list[Path]):
    # One transaction for this run: a failed import must not look completed.
    async with async_session() as db:
        seen_phones = set()
        for csv_file in csv_files:
            with csv_file.open("r", encoding="utf-8-sig", newline="") as source:
                for row in csv.DictReader(source):
                    phone = (row.get("phone") or "").strip()
                    if not phone or phone in seen_phones:
                        continue
                    seen_phones.add(phone)
                    result = await db.execute(select(Lead).where(Lead.phone == phone).limit(1))
                    if result.scalars().first():
                        continue
                    scraped_at = None
                    if row.get("scraped_at"):
                        try:
                            scraped_at = datetime.fromisoformat(row["scraped_at"])
                        except ValueError:
                            pass
                    fields = {key: row.get(key) or "" for key in (
                        "name", "address", "email", "website", "website_platform",
                        "rating", "reviews", "hours", "yandex_url",
                    )}
                    db.add(Lead(
                        **fields, phone=phone,
                        categories=[v.strip() for v in (row.get("categories") or "").split(",") if v.strip()],
                        social_links=[v.strip() for v in (row.get("social_links") or "").split(",") if v.strip()],
                        website_status=row.get("website_status") or "unknown",
                        source="parser", scraped_at=scraped_at,
                    ))
        await db.commit()


async def terminate_process(process):
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def run_parser_task(task_id: str, query: str, location: str, limit: int, mode: str):
    task = tasks[task_id]
    process = None
    try:
        parser_dir = Path(settings.PARSER_DIR).resolve()
        python_path = parser_dir / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not python_path.is_file():
            raise FileNotFoundError(f"Parser interpreter not found: {python_path}")
        previous = output_snapshot(parser_dir)
        task["status"] = "running"
        process = await asyncio.create_subprocess_exec(
            str(python_path), "run.py", mode, "--query", query,
            "--location", location, "--limit", str(limit),
            cwd=str(parser_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        task["process"] = process
        while chunk := await process.stdout.read(8192):
            task["output"] = (task["output"] + chunk.decode("utf-8", errors="replace"))[-MAX_TASK_OUTPUT_CHARS:]
        await process.wait()
        if process.returncode != 0:
            task["status"] = "failed"
            return
        changed = [path for path, signature in output_snapshot(parser_dir).items()
                   if previous.get(path) != signature]
        await import_csv_leads(sorted(changed))
        task["status"] = "completed"
    except asyncio.CancelledError:
        task["status"] = "stopped"
        raise
    except Exception as error:
        logger.exception("Parser task %s failed", task_id)
        task["status"] = "error"
        task["output"] = (task["output"] + f"\nTask failed: {error}")[-MAX_TASK_OUTPUT_CHARS:]
    finally:
        await terminate_process(process)
        task["process"] = None


async def cancel_parser_tasks():
    workers = [task["worker"] for task in tasks.values()
               if task.get("worker") and not task["worker"].done()]
    for task in tasks.values():
        if task["status"] in ACTIVE_STATUSES:
            task["status"] = "stopped"
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


@router.post("/run", response_model=TaskResponse)
async def run_parser(request: ParserRunRequest, current_user: User = Depends(require_admin)):
    # The CLI shares its out directory and registry; concurrent runs would mix data.
    if any(task["status"] in ACTIVE_STATUSES or
           (task.get("worker") and not task["worker"].done()) for task in tasks.values()):
        raise HTTPException(status_code=409, detail="Another parser task is active")
    while len(tasks) >= MAX_TASK_HISTORY:
        del tasks[next(iter(tasks))]
    task_id = str(uuid.uuid4())
    task = tasks[task_id] = {
        "status": "pending", "output": "", "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": request.mode, "process": None,
    }
    task["worker"] = asyncio.create_task(
        run_parser_task(task_id, request.query, request.location, request.limit, request.mode)
    )
    return TaskResponse(task_id=task_id, status="pending", started_at=task["started_at"], mode=request.mode)


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(current_user: User = Depends(require_admin)):
    return [TaskResponse(task_id=task_id, status=task["status"], started_at=task["started_at"],
                         mode=task["mode"]) for task_id, task in tasks.items()]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, current_user: User = Depends(require_admin)):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    return TaskResponse(task_id=task_id, status=task["status"], started_at=task["started_at"],
                        mode=task["mode"], output=task["output"])


@router.post("/stop/{task_id}")
async def stop_task(task_id: str, current_user: User = Depends(require_admin)):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks[task_id]
    worker = task.get("worker")
    if not worker or worker.done() or task["status"] not in ACTIVE_STATUSES:
        raise HTTPException(status_code=400, detail="Task is not running")
    task["status"] = "stopped"
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    return {"message": "Task stopped"}
