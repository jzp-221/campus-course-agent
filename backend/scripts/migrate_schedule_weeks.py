"""P8 additive migration: keep existing schedules and add week/teacher defaults.

Run from backend with its configured database (no secrets in command arguments):
    .venv/bin/python scripts/migrate_schedule_weeks.py

Base.metadata.create_all does not update an existing table. This independent
migration supports MySQL and SQLite and can be rerun after a partial migration.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlalchemy import Engine, inspect, text


# Portable additive DDL. Explicit database defaults populate old rows and also
# protect later inserts that omit these fields, independently of ORM defaults.
SCHEDULE_COLUMNS = (
    ("start_week", "INTEGER NOT NULL DEFAULT 1"),
    ("end_week", "INTEGER NOT NULL DEFAULT 20"),
    ("teacher", "VARCHAR(100) NULL DEFAULT NULL"),
)


def schedule_structure(engine: Engine) -> list[dict]:
    inspector = inspect(engine)
    if not inspector.has_table("schedules"):
        raise RuntimeError("schedules 表不存在，请先完成原有数据库初始化")
    return inspector.get_columns("schedules")


def migrate_schedule_weeks(engine: Engine) -> list[str]:
    """Add only missing columns; never recreate a table or modify existing rows.

    MySQL DDL commits independently. Inspecting existing columns lets a second
    execution resume safely if a previous execution stopped between statements.
    """
    if engine.dialect.name not in {"mysql", "sqlite"}:
        raise RuntimeError("本迁移只支持 MySQL 和 SQLite")
    existing = {column["name"] for column in schedule_structure(engine)}
    added = []
    for name, declaration in SCHEDULE_COLUMNS:
        if name in existing:
            continue
        # Identifiers and declarations come exclusively from constants above.
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE schedules ADD COLUMN {name} {declaration}")
            )
        existing.add(name)
        added.append(name)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="P8 课表周次字段幂等迁移")
    parser.add_argument("--check", action="store_true", help="只读查看表结构，不执行迁移")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # Load the configured engine only when running the command, so offline tests
    # can pass their own engine without reading .env or connecting to MySQL.
    from app.database import engine

    try:
        if not args.check:
            added = migrate_schedule_weeks(engine)
            print("新增字段：" + ("、".join(added) if added else "无（已完成迁移）"))
        print("schedules 表结构：")
        for column in schedule_structure(engine):
            nullable = "NULL" if column["nullable"] else "NOT NULL"
            print(
                f"  {column['name']}: {column['type']} {nullable} "
                f"DEFAULT {column.get('default')!r}"
            )
        return 0
    except RuntimeError as error:
        print(f"迁移失败：{error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Avoid exposing URLs, passwords, or SQL through a driver exception.
        print(f"迁移失败（{type(error).__name__}），请检查数据库连接和 DDL 权限", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
