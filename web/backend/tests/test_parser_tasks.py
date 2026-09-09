import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

for key, value in {
    "DATABASE_URL": "postgresql+asyncpg://leadcrm:test@localhost:5432/leadcrm",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef",
    "ADMIN_PASSWORD": "correct-horse-battery-staple",
}.items():
    os.environ.setdefault(key, value)

from app.routers import parser_router as parser


@pytest.fixture
def parser_directory(tmp_path, monkeypatch):
    python = tmp_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(parser.settings, "PARSER_DIR", str(tmp_path))
    monkeypatch.setattr(parser, "tasks", {})
    return tmp_path


def process_result(chunks=None, returncode=0):
    return SimpleNamespace(
        stdout=SimpleNamespace(read=AsyncMock(side_effect=chunks or [b"done\n", b""])),
        returncode=returncode, wait=AsyncMock(return_value=returncode),
    )


def add_task():
    parser.tasks["test"] = {"status": "pending", "output": "", "mode": "scrape", "started_at": "now"}


def test_import_uses_server_loop_and_only_this_runs_csv(parser_directory, monkeypatch):
    stale = parser_directory / "out" / "old.csv"
    stale.write_text("phone\nold\n")
    fresh = parser_directory / "out" / "new.csv"
    async def scenario():
        server_loop = asyncio.get_running_loop()
        async def spawn(*args, **kwargs):
            fresh.write_text("phone\nnew\n")
            return process_result()
        async def import_results(files):
            assert asyncio.get_running_loop() is server_loop
            assert files == [fresh]
            assert parser.tasks["test"]["status"] == "running"
        monkeypatch.setattr(parser.asyncio, "create_subprocess_exec", spawn)
        imported = AsyncMock(side_effect=import_results)
        monkeypatch.setattr(parser, "import_csv_leads", imported)
        add_task()
        await parser.run_parser_task("test", "shop", "city", 1, "scrape")
        imported.assert_awaited_once()
        assert parser.tasks["test"]["status"] == "completed"
    asyncio.run(scenario())


def test_import_failure_is_not_reported_as_success(parser_directory, monkeypatch):
    monkeypatch.setattr(parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process_result()))
    monkeypatch.setattr(parser, "import_csv_leads", AsyncMock(side_effect=RuntimeError("database unavailable")))
    add_task()
    asyncio.run(parser.run_parser_task("test", "shop", "city", 1, "scrape"))
    assert parser.tasks["test"]["status"] == "error"
    assert "database unavailable" in parser.tasks["test"]["output"]


def test_failed_process_never_imports_and_output_is_bounded(parser_directory, monkeypatch):
    process = process_result([b"x" * (parser.MAX_TASK_OUTPUT_CHARS + 100), b""], returncode=1)
    monkeypatch.setattr(parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    imported = AsyncMock()
    monkeypatch.setattr(parser, "import_csv_leads", imported)
    add_task()
    asyncio.run(parser.run_parser_task("test", "shop", "city", 1, "scrape"))
    imported.assert_not_awaited()
    assert parser.tasks["test"]["status"] == "failed"
    assert len(parser.tasks["test"]["output"]) == parser.MAX_TASK_OUTPUT_CHARS


def test_pending_task_can_be_stopped_and_prevents_concurrent_run(parser_directory, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(parser.asyncio, "create_subprocess_exec", spawn)
    async def scenario():
        request = parser.ParserRunRequest(query="shop", location="city")
        response = await parser.run_parser(request, None)
        with pytest.raises(HTTPException) as error:
            await parser.run_parser(request, None)
        assert error.value.status_code == 409
        await parser.stop_task(response.task_id, None)
        assert parser.tasks[response.task_id]["status"] == "stopped"
        spawn.assert_not_awaited()
    asyncio.run(scenario())


def test_running_cancellation_terminates_child_and_skips_import(parser_directory, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        process = process_result()
        process.returncode = None
        async def read_chunk(_):
            started.set()
            await asyncio.Event().wait()
        process.stdout.read = read_chunk
        terminated = []
        def terminate():
            terminated.append(True)
            process.returncode = -15
        process.terminate = terminate
        monkeypatch.setattr(parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        imported = AsyncMock()
        monkeypatch.setattr(parser, "import_csv_leads", imported)
        response = await parser.run_parser(parser.ParserRunRequest(query="shop", location="city"), None)
        await asyncio.wait_for(started.wait(), 1)
        await parser.stop_task(response.task_id, None)
        assert terminated == [True]
        assert parser.tasks[response.task_id]["status"] == "stopped"
        imported.assert_not_awaited()
    asyncio.run(scenario())
