import json
from datetime import datetime, timezone


def parse_scraped_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    # Existing PostgreSQL columns contain UTC without a timezone.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_csv_list(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    if value.lstrip().startswith("["):
        items = json.loads(value)
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError("Expected a JSON array of strings")
        return [item.strip() for item in items if item.strip()]
    # Maps Parser writes semicolon-separated values; older exports used commas.
    separator = ";" if ";" in value else ","
    return [item.strip() for item in value.split(separator) if item.strip()]
