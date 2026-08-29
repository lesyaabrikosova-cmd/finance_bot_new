"""Создаёт согласованную резервную копию SQLite через официальный backup API."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(source: Path, destination_dir: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"База не найдена: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"allocator-{timestamp}.db"
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
        result = backup_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Проверка резервной копии не пройдена: {result}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/data/allocator.db")
    parser.add_argument("--destination", default="/data/backups")
    args = parser.parse_args()
    destination = backup_database(Path(args.source), Path(args.destination))
    print(destination)


if __name__ == "__main__":
    main()
