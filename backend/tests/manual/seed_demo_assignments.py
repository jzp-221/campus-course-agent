"""P13 independent demo seed; never touches schedules, courses or text plans.

Only aaa/bbb records bearing this script's reserved [P13演示] marker are managed.
All writes (including --clean) are transactional. --preview is entirely read-only.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.exc import SQLAlchemyError
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.assignment import Assignment
from app.models.coures import Course
from app.models.plan_task import PlanTask
from app.models.user import User
from app.utils.learning_time import local_now

MARKER = "[P13演示"
TASK_PREFIX = "[P13演示]"
DATA_VERSION = "P13-v2"

ASSIGNMENT_TEMPLATES = (
    ("数据结构", "第3章课后习题", 1),
    ("高等数学", "微积分习题集二", 3),
    ("大学英语", "Unit 3 阅读与作文", 5),
    ("计算机网络", "网络协议分析报告", 7),
    ("数据库系统原理", "SQL 综合练习", 9),
    ("操作系统", "进程调度实验报告", 12),
    ("Web 应用开发", "课程网页制作作业", 15),
    ("软件工程", "需求分析报告", 21),
)
EXAM_TEMPLATES = (
    ("数据结构", "期中考试", 6),
    ("高等数学", "阶段测验", 13),
    ("计算机网络", "期中考试", 20),
)


def build_demo(students, courses, today: date):
    by_name = {course.name: course for course in courses}
    owners = {student.username: student for student in students}
    if not {"aaa", "bbb"} <= owners.keys():
        raise BusinessException("请先创建 aaa 和 bbb 演示学生；脚本不会新增用户", code=404)
    required = {name for name, _, _ in ASSIGNMENT_TEMPLATES + EXAM_TEMPLATES}
    missing = required - by_name.keys()
    if missing:
        raise BusinessException("缺少现有演示课程：" + "、".join(sorted(missing)), code=404)
    wanted = {"assignments": [], "plan_tasks": []}

    def add_assignment(username, key, name, title, due_day, item_type="assignment", status="pending", note=""):
        wanted['assignments'].append({"user_id": owners[username].id, "course_id":by_name[name].id,
            "type":item_type, "title":title, "due_at":datetime.combine(due_day, time(18 if item_type == "assignment" else 9)),
            "status":status, "note":f"[P13演示:{username}-{key}] {note or '课程学习任务演示'}"})

    for index, (name,title,offset) in enumerate(ASSIGNMENT_TEMPLATES, 1):
        # 固定演示标记不变：仅更新三条既有作业，提供完成/逾期/近期三种状态。
        offset = {1: -2, 2: -1, 3: 2}.get(index, offset)
        add_assignment("aaa", f"a{index:02}", name,title,today+timedelta(days=offset),
                       status="done" if index == 1 else "pending")
    for index, (name,title,offset) in enumerate(EXAM_TEMPLATES, 1):
        add_assignment("aaa", f"e{index:02}", name,title,today+timedelta(days=offset), "exam")
    # Approved exception to the 1–21 day window; fixed holiday demonstration.
    add_assignment("aaa", "holiday", "高等数学", "元旦假期复习作业", date(2027,1,1),
                   note="截止日撞上元旦假期；2027 年假期安排按项目既有推定配置")
    add_assignment("bbb", "a01", "数据结构", "栈与队列练习",today+timedelta(days=4))
    add_assignment("bbb", "a02", "大学英语", "Unit 3 单词整理",today+timedelta(days=10))
    add_assignment("bbb", "e01", "高等数学", "阶段测验",today+timedelta(days=17),"exam")

    tasks = (
        ("数据结构", "复习栈与队列", 2, "pending", 0),
        ("高等数学", "整理导数与积分公式", 4, "pending", 2),
        ("大学英语", "完成 Unit 3 单词复习", 1, "done", 0),
        ("计算机网络", "复习 TCP 与 UDP 的区别", 8, "pending", 0),
        (None, "整理本周学习笔记", None, "pending", 0),
    )
    for name,title,offset,status,count in tasks:
        wanted['plan_tasks'].append({"user_id":owners['aaa'].id,
            "course_id":by_name[name].id if name else None,
            "title":TASK_PREFIX + (f"{name}：" if name else "") + title,
            "due_at":datetime.combine(today+timedelta(days=offset),time(20)) if offset else None,
            "status":status, "defer_count":count})
    return wanted


def managed_rows(db, model, student_ids):
    query = db.query(model).filter(model.user_id.in_(student_ids))
    if model is Assignment:
        query = query.filter(Assignment.note.startswith("[P13演示:"))
    else:
        query = query.filter(PlanTask.title.startswith(TASK_PREFIX))
    return query.order_by(model.id).with_for_update().all()


def item_key(model, row):
    data = row if isinstance(row, dict) else {column.name:getattr(row,column.name) for column in model.__table__.columns}
    token = data['note'].split("]",1)[0]+"]" if model is Assignment else data['title']
    return data['user_id'], token


def sync_demo(db, students, wanted):
    stats = {"created":0, "updated":0, "unchanged":0}
    ids = [student.id for student in students]
    for model,name in ((Assignment,"assignments"),(PlanTask,"plan_tasks")):
        existing = {}
        for row in managed_rows(db,model,ids):
            key = item_key(model,row)
            if key in existing:
                raise BusinessException("演示标记记录存在重复，请先人工核对；未写入数据",code=409)
            existing[key] = row
        for data in wanted[name]:
            row = existing.get(item_key(model,data))
            if row is None:
                db.add(model(**data))
                stats['created'] += 1
            else:
                changed = False
                for field,value in data.items():
                    if getattr(row,field) != value:
                        setattr(row,field,value)
                        changed = True
                stats['updated' if changed else 'unchanged'] += 1
    db.flush()
    return stats


def clear_demo(db, students):
    counts = {}
    ids = [student.id for student in students]
    for model in (Assignment,PlanTask):
        rows = managed_rows(db,model,ids)
        counts[model.__tablename__] = len(rows)
        for row in rows:
            db.delete(row)
    db.flush()
    return counts


def print_items(students,courses,wanted,today):
    names = {course.id:course.name for course in courses}
    usernames = {student.id:student.username for student in students}
    print(f"数据版本：{DATA_VERSION}；本地基准日期：{today}；含已完成、已逾期和今天起1～21天近期数据")
    print("元旦假期待办为单独批准的窗口外示例；只管理 aaa/bbb 的专属演示标记记录。")
    for table in ('assignments','plan_tasks'):
        print(f"\n{table} 逐条清单：")
        for row in wanted[table]:
            due = row['due_at'].isoformat(sep=' ') if row['due_at'] else '无截止时间'
            kind = '作业' if row.get('type') == 'assignment' else '考试' if row.get('type') == 'exam' else '计划任务'
            extra = f" | 延期 {row['defer_count']} 次" if table == 'plan_tasks' else ''
            print(f"{usernames[row['user_id']]} | {kind} | {names.get(row['course_id'],'未关联课程')} | "
                  f"{row['title']} | {due} | {row['status']}{extra}")
    for student in students:
        rows = [row for row in wanted['assignments'] if row['user_id'] == student.id]
        tasks = [row for row in wanted['plan_tasks'] if row['user_id'] == student.id]
        holiday = "（含1条假期待办）" if any('-holiday]' in row['note'] for row in rows) else ""
        print(f"{student.username}：作业 {sum(row['type']=='assignment' for row in rows)} 条{holiday}，"
              f"考试 {sum(row['type']=='exam' for row in rows)} 场，计划任务 {len(tasks)} 条")


def seed(db, today, *, preview=False, clean=False):
    students = db.query(User).filter(User.username.in_(['aaa','bbb'])).order_by(User.id).all()
    if {row.username for row in students} != {'aaa','bbb'}:
        raise BusinessException("aaa/bbb 演示学生不存在",code=404)
    if clean:
        counts = clear_demo(db,students)
        print(f"仅清理专属标记：{counts}；未删除课程、课表、资料、文本计划或非演示记录")
        return counts
    courses = db.query(Course).order_by(Course.id).all()
    wanted = build_demo(students,courses,today)
    if preview:
        print("只读预览：未写入数据库")
        stats = None
    else:
        stats = sync_demo(db,students,wanted)
        print(f"写入结果：{stats}")
    print_items(students,courses,wanted,today)
    return stats


def main():
    parser = argparse.ArgumentParser(description="作业考试与计划任务演示数据（独立于课表种子）")
    parser.add_argument('--today',type=date.fromisoformat,help="固定本地基准日期，用于可重复核对；默认上海今天")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--preview',action='store_true',help='只读预览')
    actions.add_argument('--clean',action='store_true',help='仅删除本脚本标记的作业考试与任务')
    args = parser.parse_args()
    try:
        with SessionLocal() as db, db.begin():
            seed(db,args.today or local_now().date(),preview=args.preview,clean=args.clean)
        return 0
    except BusinessException as exc:
        print(f"失败（业务码 {exc.code}）：{exc.message}；事务已回滚",file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("数据库操作失败，事务已回滚；请确认新表迁移完成",file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
