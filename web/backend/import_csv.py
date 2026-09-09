import asyncio
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.database import async_session, init_db
from app.models import Lead
from app.import_values import parse_csv_list, parse_scraped_at


async def import_csv(csv_path: str):
    await init_db()

    path = Path(csv_path)
    if not path.exists():
        print(f"Файл не найден: {csv_path}")
        return

    imported = 0
    skipped = 0

    async with async_session() as db:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                categories = parse_csv_list(row.get("categories"))
                social_links = parse_csv_list(row.get("social_links"))
                scraped_at = parse_scraped_at(row.get("scraped_at"))

                lead = Lead(
                    name=row.get("name", "Без названия"),
                    categories=categories,
                    address=row.get("address", ""),
                    phone=row.get("phone", ""),
                    email=row.get("email", ""),
                    website=row.get("website", ""),
                    website_status=row.get("website_status", "unknown"),
                    website_platform=row.get("website_platform", ""),
                    social_links=social_links,
                    rating=row.get("rating", ""),
                    reviews=row.get("reviews", ""),
                    hours=row.get("hours", ""),
                    yandex_url=row.get("yandex_url", ""),
                    source=row.get("source", "yandex_maps"),
                    scraped_at=scraped_at,
                )
                db.add(lead)
                imported += 1

                if imported % 100 == 0:
                    await db.flush()
                    print(f"  Импортировано: {imported}...")

        await db.commit()

    print(f"\nГотово! Импортировано: {imported}, пропущено: {skipped}")


async def import_all_no_site():
    out_dir = Path(__file__).parent.parent.parent / "out"

    files_to_import = [
        out_dir / "no_site_leads.csv",
    ]

    for f in files_to_import:
        if f.exists():
            print(f"\nИмпорт: {f}")
            await import_csv(str(f))
        else:
            print(f"Файл не найден: {f}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(import_csv(sys.argv[1]))
    else:
        asyncio.run(import_all_no_site())
