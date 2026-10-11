"""P13 只新增两张表与 user_id 索引；可检查、可重复执行，不改旧表。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from sqlalchemy import Engine, inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def new_tables():
    from app.models.assignment import Assignment
    from app.models.plan_task import PlanTask
    return (Assignment.__table__, PlanTask.__table__)


def migration_structure(engine: Engine) -> dict:
    inspector = inspect(engine)
    return {name: None if not inspector.has_table(name) else {
        "columns": inspector.get_columns(name),
        "indexes": inspector.get_indexes(name),
        "foreign_keys": inspector.get_foreign_keys(name),
    } for name in ("assignments", "plan_tasks")}


def migrate_assignments_and_tasks(engine: Engine) -> list[str]:
    if engine.dialect.name not in {"mysql", "sqlite"}:
        raise RuntimeError("本迁移只支持 MySQL 和 SQLite")
    inspector = inspect(engine)
    for parent in ("users", "coruses"):
        if not inspector.has_table(parent):
            raise RuntimeError(f"{parent} 表不存在，请先完成原有数据库初始化")
    added = []
    for table in new_tables():
        if not inspect(engine).has_table(table.name):
            table.create(engine, checkfirst=True)
            added.append(table.name)
        # Resume safely if a previous run stopped after table creation.
        columns = inspect(engine).get_columns(table.name)
        expected = {column.name for column in table.columns}
        if {column['name'] for column in columns} != expected:
            raise RuntimeError(f"{table.name} 已存在但字段不符合模型；未重建或删除数据")
        indexes = inspect(engine).get_indexes(table.name)
        for index in table.indexes:
            names = [column.name for column in index.columns]
            if not any(item['column_names'] == names for item in indexes):
                index.create(engine, checkfirst=True)
                added.append(index.name)
    return added


def print_structure(engine: Engine):
    for name, structure in migration_structure(engine).items():
        if structure is None:
            print(f"{name}：不存在（待迁移）")
            continue
        print(f"{name} 表结构：")
        for column in structure['columns']:
            print(f"  {column['name']}: {column['type']} "
                  f"{'NULL' if column['nullable'] else 'NOT NULL'} "
                  f"DEFAULT {column.get('default')!r}")
        print("  索引：" + repr(structure['indexes']))
        if engine.dialect.name == "mysql":
            with engine.connect() as connection:
                print(connection.execute(text(f"SHOW CREATE TABLE `{name}`")).one()[1])


def main():
    parser = argparse.ArgumentParser(description="P13 作业考试与计划任务幂等迁移")
    parser.add_argument("--check", action="store_true", help="只读检查，不创建表或索引")
    args = parser.parse_args()
    from app.database import engine
    try:
        if not args.check:
            added = migrate_assignments_and_tasks(engine)
            print("新增：" + ("、".join(added) if added else "无（已完成迁移）"))
        print_structure(engine)
        return 0
    except RuntimeError as error:
        print(f"迁移失败：{error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"迁移失败（{type(error).__name__}），请检查数据库连接和 DDL 权限", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
