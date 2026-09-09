import os
from types import SimpleNamespace

import bcrypt
import jwt
import pytest
from pydantic import ValidationError

BASE_SETTINGS = {
    "DATABASE_URL": "postgresql+asyncpg://leadcrm:test@localhost:5432/leadcrm",
    "SECRET_KEY": "0123456789abcdef0123456789abcdef",
    "ADMIN_PASSWORD": "correct-horse-battery-staple",
}
for key, value in BASE_SETTINGS.items():
    os.environ.setdefault(key, value)

from app.config import Settings
from app.auth import create_access_token, hash_password, verify_password
from app.routers import portfolio_router


def make_settings(**overrides: str) -> Settings:
    values = {**BASE_SETTINGS, **overrides}
    return Settings(**values)


def test_schema_does_not_declare_duplicate_indexes() -> None:
    from app.database import Base
    names = [index.name for table in Base.metadata.tables.values() for index in table.indexes]
    assert len(names) == len(set(names))


def test_settings_reject_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY must be replaced"):
        make_settings(SECRET_KEY="generate-a-random-secret-before-starting")


def test_settings_reject_placeholder_admin_password() -> None:
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD must be replaced"):
        make_settings(ADMIN_PASSWORD="replace-with-a-unique-password")


def test_password_hash_round_trip_and_legacy_bcrypt_support() -> None:
    password = "correct-horse-battery-staple"
    current_hash = hash_password(password)
    legacy_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    assert current_hash.startswith("$argon2")
    assert verify_password(password, current_hash)
    assert verify_password(password, legacy_hash)
    assert not verify_password("wrong-password", current_hash)


def test_access_token_uses_string_subject() -> None:
    token = create_access_token({"sub": 42})
    payload = jwt.decode(token, BASE_SETTINGS["SECRET_KEY"], algorithms=["HS256"])

    assert payload["sub"] == "42"


def test_screenshot_payload_exposes_url_not_local_path() -> None:
    screenshot = SimpleNamespace(
        id=7,
        project_id=3,
        filename="preview.webp",
        original_filename="original.webp",
        file_path="/private/server/path/preview.webp",
        sort_order=1,
        created_at=None,
    )

    payload = portfolio_router.screenshot_payload(screenshot, "sites", "coffee")

    assert payload["url"] == "/uploads/sites/coffee/preview.webp"
    assert "file_path" not in payload


def test_delete_upload_file_stays_inside_upload_directory(tmp_path, monkeypatch) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    inside = upload_root / "inside.jpg"
    outside = tmp_path / "outside.jpg"
    inside.write_bytes(b"inside")
    outside.write_bytes(b"outside")
    monkeypatch.setattr(portfolio_router.settings, "UPLOAD_DIR", str(upload_root))

    portfolio_router.delete_upload_file(str(outside))
    assert outside.exists()

    portfolio_router.delete_upload_file(str(inside))
    assert not inside.exists()
