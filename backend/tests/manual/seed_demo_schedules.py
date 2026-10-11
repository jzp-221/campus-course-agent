"""生成固定种子的 20 周演示课表；默认只同步演示账号和模板课程。

用法（在 backend 目录）：
    .venv/bin/python tests/manual/seed_demo_schedules.py                 # 补齐课程并同步整学期
    .venv/bin/python tests/manual/seed_demo_schedules.py --preview       # 只读预览，不写数据库
    .venv/bin/python tests/manual/seed_demo_schedules.py --no-courses    # 只生成课表
    .venv/bin/python tests/manual/seed_demo_schedules.py --clean         # 仅清理本学期演示课表，保留课程
    .venv/bin/python tests/manual/seed_demo_schedules.py --reseed        # 同一事务内先清课表再重建

设计要点：
- 学期日期由 SEMESTER_START_DATE / TOTAL_WEEKS 配置决定，不随运行日期漂移。
- 每门课有固定的星期、节次、教师、地点和周次范围；不同课程不共享格子。
- 放假不排课；周六、周日有专门课程，仅指定调休日展开，不挪其他星期的课。
- 课程与课表都经项目 ORM 写入，create_time/update_time 由模型自动填写。
- 只使用库里真实存在的学生账号；外键 user_id/course_id 不会写出孤儿数据。
- aaa 密集排课，bbb 仅五门课程；按学生 ID 派生独立随机种子。
- 只清理演示学生、本学期、模板课程的课表；课程、资料和学习计划始终保留。
- 写入前备份受管理的原课表；清理与重建在同一事务内，失败整体回滚。
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal  # noqa: E402
from app.common.exceptions import BusinessException  # noqa: E402
from app.config import settings  # noqa: E402
from app.models.coures import Course  # noqa: E402
from app.models.schedule import Schedule  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils import semester  # noqa: E402

# 计算机专业课程库：(课程编码, 课程名称, 简介)
# 显式白名单用于同步和清理，避免影响管理员另外添加的课程。
CS_COURSES = [
    ("CS101", "程序设计基础", "C 语言语法、流程控制、函数与数组，程序设计的入门课"),
    ("CS102", "面向对象程序设计", "类与对象、继承与多态、封装，以 Java 为载体"),
    ("CS201", "数据结构", "线性表、栈与队列、树、图及常用算法的实现与分析"),
    ("CS202", "算法设计与分析", "分治、动态规划、贪心、回溯与复杂度分析"),
    ("CS203", "计算机组成原理", "数的表示、运算器、存储层次、指令系统与流水线"),
    ("CS204", "操作系统", "进程与线程、调度、同步互斥、内存管理与文件系统"),
    ("CS205", "计算机网络", "TCP/IP 协议栈、路由与交换、可靠传输与网络编程"),
    ("CS206", "数据库系统原理", "关系模型、SQL、范式设计、事务与并发控制、索引优化"),
    ("CS207", "软件工程", "需求分析、结构化与面向对象设计、测试、版本管理与团队协作"),
    ("CS208", "编译原理", "词法分析、语法分析、语义分析、中间代码与目标代码生成"),
    ("CS209", "离散数学", "集合与关系、图论、数理逻辑、组合计数，计算机理论的基础"),
    ("CS301", "人工智能导论", "搜索、知识表示、机器学习基础与智能体设计"),
    ("CS302", "机器学习", "监督与无监督学习、回归分类聚类、模型评估与调优"),
    ("CS303", "Web 应用开发", "前后端分离、RESTful 接口、数据库交互与部署"),
    ("CS304", "信息安全基础", "密码学基础、身份认证、常见 Web 漏洞与防护"),
    ("CS305", "计算机图形学", "二维三维变换、光栅化、光照模型与渲染管线"),
    ("CS306", "嵌入式系统", "单片机原理、GPIO 与中断、外设驱动与实时系统"),
    ("CS307", "大数据技术基础", "分布式存储与计算、MapReduce 思想、数据流水线"),
    ("MATH201", "线性代数", "矩阵运算、向量空间、特征值与二次型"),
    ("MATH202", "概率论与数理统计", "随机变量、分布、大数定律与假设检验"),
    # 课程管理页支持任意课程，这里保留三门公共课作为通识搭配。
    ("ENG101", "大学英语", "听说读写综合训练"),
    ("MATH101", "高等数学", "极限、导数与积分"),
    # 历史编码别名：与 CS201 共用同一门“数据结构”，排课时按实际 ID 去重。
    ("DS101", "数据结构", "数据结构与算法基础"),
]

# 实验/上机类课程（排课时更容易安排到实验楼）
LAB_CODES = {"CS101", "CS102", "CS201", "CS202", "CS203", "CS204",
             "CS205", "CS206", "CS208", "CS303", "CS304", "CS305",
             "CS306", "CS307"}

PERIODS = ["1-2节", "3-4节", "5-6节", "7-8节"]
CLASSROOMS = ["教学楼 A201", "教学楼 B305", "教学楼 C402", "信息楼 A301", "信息楼 B202"]
LABS = ["实验楼 D404", "实验楼 D501", "信息楼 机房301", "信息楼 机房402"]
RANDOM_SEED = 20261010
DATA_VERSION = "P10-student-isolation-v1"
DEMO_USERNAMES = ("aaa", "bbb")
TEACHERS = (
    "张文博", "李明远", "王思敏", "赵启航", "陈晓宁", "刘建华", "孙雅静",
    "周志强", "吴嘉诚", "郑子涵", "何书文", "林雨桐", "马振宇", "徐浩然",
    "高云舒", "朱博文", "胡佳宁", "郭景行", "罗清远", "梁知夏", "宋承泽",
    "唐婉清", "许致远",
)


@dataclass(frozen=True)
class CourseAssignment:
    course_id: int
    code: str
    weekday: int  # 实际星期，周一为 0、周日为 6；调休日也不挪动课程格子。
    period: str
    start_week: int
    end_week: int
    teacher: str
    location: str


def _students(db):
    rows = (db.query(User).filter(
                User.username.in_(DEMO_USERNAMES),
                User.role == "student", User.status == 1)
            .order_by(User.id).all())
    missing = sorted(set(DEMO_USERNAMES) - {row.username for row in rows})
    if missing:
        raise BusinessException(
            f"缺少启用的演示学生账号：{'、'.join(missing)}", code=400
        )
    return rows


def ensure_courses(db):
    """编码或名称任一已存在就复用；历史别名不会再生成重名课程。"""
    existing = db.query(Course).order_by(Course.id).all()
    by_code = {course.code: course for course in existing}
    by_name = {}
    for course in existing:
        by_name.setdefault(course.name, course)
    courses = []
    created = 0
    for code, name, description in CS_COURSES:
        course = by_code.get(code) or by_name.get(name)
        if course is None:
            course = Course(code=code, name=name, description=description, status=1)
            db.add(course)
            by_code[code] = course
            by_name[name] = course
            created += 1
        courses.append(course)
    # 留在外层事务中：后续排课失败时，新增课程也一并回滚。
    db.flush()
    return courses, created


def template_courses(courses):
    """复用历史编码，但每个实际课程 ID 只返回一个授课安排。"""
    by_code = {course.code: course for course in courses}
    by_name = {}
    for course in sorted(courses, key=lambda row: row.id):
        by_name.setdefault(course.name, course)
    resolved = []
    for code, name, _ in CS_COURSES:
        course = by_code.get(code) or by_name.get(name)
        if course is None:
            raise BusinessException(
                "演示排课需要完整的 22 门唯一模板课程，请先补齐课程或取消 --no-courses",
                code=400,
            )
        resolved.append((code, course))
    unique = {}
    for code, course in sorted(resolved, key=lambda row: (row[0], row[1].id)):
        unique.setdefault(course.id, (code, course))
    return sorted(unique.values(), key=lambda row: (row[0], row[1].id))


def assign_courses(courses, rng=None, *, sparse=False):
    """独立生成学生的固定格子；稀疏学生五门课，密集学生每课仅占一格。"""
    rng = rng or random.Random(RANDOM_SEED)
    courses = template_courses(courses)
    if len(courses) > 22:
        raise BusinessException("模板存在未合并的重名课程，请先完成课程去重", code=409)
    if sparse:
        # 周一覆盖国庆前半周，周四覆盖国庆后半周；两门贯穿课保底20周。
        by_code = dict(courses)
        mandatory = [by_code["CS201"], by_code["CS303"]]
        others = [(code, course) for code, course in courses if course not in mandatory]
        selected = rng.sample(others, 3)
        courses = [("CS201", mandatory[0]), ("CS303", mandatory[1])] + selected
        third_day = rng.choice([1, 2, 4])
        slots = [(0, rng.choice(PERIODS)), (3, rng.choice(PERIODS)),
                 (third_day, rng.choice(PERIODS)), (5, rng.choice(PERIODS)),
                 (6, rng.choice(PERIODS))]
    else:
        slots = [(weekday, period) for weekday in range(5) for period in PERIODS]
        slots += [(5, rng.choice(PERIODS)), (6, rng.choice(PERIODS))]
        rng.shuffle(slots)
        # 两门核心演示课贯穿全学期、放在国庆后仍上课的星期，便于跨周核对。
        # 节次仍由该学生的独立随机种子决定；每课只占一个固定格。
        for code, weekday in [("CS201", 3), ("CS303", 4)]:
            course_index = next(index for index, (key, _) in enumerate(courses) if key == code)
            target = (weekday, rng.choice(PERIODS))
            slot_index = slots.index(target)
            slots[course_index], slots[slot_index] = slots[slot_index], slots[course_index]
    teachers = list(TEACHERS)
    rng.shuffle(teachers)
    # 每个工作日优先保留一门贯穿课，第 4 周只有周一至周三也仍有课。
    anchor_slots = {
        next(slot for slot in slots if slot[0] == weekday)
        for weekday in {slot[0] for slot in slots if slot[0] < 5}
    }
    if not sparse:
        anchor_slots.update(slots[index] for index, (code, _) in enumerate(courses)
                            if code in {"CS201", "CS303"})
    partial_ranges = [(1, 16), (3, 8), (9, 16), (1, 20)]
    partial_index = 0
    assignments = []
    for index, (template_code, course) in enumerate(courses):
        weekday, period = slots[index]
        if weekday == 5:
            # 10/10 属于第 5 周，专属周六课程覆盖该调休日。
            start_week, end_week = 1, 20
        elif weekday == 6:
            # 9/20 属于第 2 周，周日课程仅在这一个上课日展开。
            start_week, end_week = 1, 8
        elif sparse:
            start_week, end_week = (1, 20) if index < 2 else (1, 16)
        elif (weekday, period) in anchor_slots:
            start_week, end_week = 1, 20
        else:
            # 前三门非贯穿课保证范围多样；之后按固定种子选范围。
            start_week, end_week = (
                partial_ranges[partial_index]
                if partial_index < 3 else rng.choice(partial_ranges)
            )
            partial_index += 1
        pool = LABS if template_code in LAB_CODES or template_code == "DS101" else CLASSROOMS
        assignments.append(CourseAssignment(
            course_id=course.id, code=template_code, weekday=weekday, period=period,
            start_week=start_week, end_week=end_week,
            teacher=teachers[index % len(teachers)], location=rng.choice(pool),
        ))
    return assignments


def _class_weekday(day):
    """按实际星期定位，调休日也不复制其他星期的课程。"""
    return day.weekday() if semester.is_class_day(day) else None


def student_random_seed(student, *, base_seed=RANDOM_SEED):
    """不依赖 Python hash 随机化，也不依赖学生列表顺序。"""
    value = f"{DATA_VERSION}:{base_seed}:{student.id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def build_schedule(students, courses, *, rng=None):
    """固定课程格子按周次和上课日展开；调休日采用实际日期所在周次。"""
    base_seed = RANDOM_SEED if rng is None else hashlib.sha256(
        repr(rng.getstate()).encode("ascii")).hexdigest()
    start, _ = semester.week_date_range(1)
    _, end = semester.week_date_range(settings.TOTAL_WEEKS)
    wanted = []
    for student in sorted(students, key=lambda student: student.id):
        assignments = assign_courses(
            courses, random.Random(student_random_seed(student, base_seed=base_seed)),
            sparse=student.username == "bbb",
        )
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if not semester.is_class_day(day):
                continue
            weekday = _class_weekday(day)
            week = semester.date_to_week(day)
            for assignment in assignments:
                if assignment.weekday != weekday:
                    continue
                if not assignment.start_week <= week <= assignment.end_week:
                    continue
                wanted.append({
                    "user_id": student.id, "class_date": day,
                    "period": assignment.period, "course_id": assignment.course_id,
                    "location": assignment.location, "teacher": assignment.teacher,
                    "start_week": assignment.start_week, "end_week": assignment.end_week,
                })
    cells = {(row["user_id"], row["class_date"], row["period"]) for row in wanted}
    if len(cells) != len(wanted):
        raise BusinessException("演示排课产生同格冲突，已取消写入", code=409)
    for student in students:
        covered = {semester.date_to_week(row["class_date"]) for row in wanted
                   if row["user_id"] == student.id}
        if covered != set(range(1, settings.TOTAL_WEEKS + 1)):
            raise BusinessException(
                f"演示学生 {student.username} 存在整周无课，已取消写入", code=400
            )
    summaries = weekly_difference_summary(students, wanted)
    if any(row["different_ratio"] < 0.8 for row in summaries):
        raise BusinessException("演示课表每周差异不足80%，已取消写入", code=400)
    return wanted


def weekly_difference_summary(students, wanted):
    """仅比较双方至少一人有课的格子；同格且同 course_id 才算相同。"""
    by_username = {student.username: student.id for student in students}
    if not {"aaa", "bbb"} <= by_username.keys():
        raise BusinessException("课表差异摘要需要 aaa 和 bbb 两名演示学生", code=400)
    result = []
    for week in range(1, settings.TOTAL_WEEKS + 1):
        monday, sunday = semester.week_date_range(week)
        grids = {}
        for username in DEMO_USERNAMES:
            grids[username] = {(row["class_date"], row["period"]): row["course_id"]
                               for row in wanted
                               if row["user_id"] == by_username[username]
                               and monday <= row["class_date"] <= sunday}
        cells = grids["aaa"].keys() | grids["bbb"].keys()
        same = sum(cell in grids["aaa"] and cell in grids["bbb"]
                   and grids["aaa"][cell] == grids["bbb"][cell] for cell in cells)
        different = len(cells) - same
        result.append({"week": week, "start_date": monday, "end_date": sunday,
                       "aaa_count": len(grids["aaa"]), "bbb_count": len(grids["bbb"]),
                       "same_cells": same, "different_cells": different,
                       "compared_cells": len(cells),
                       "different_ratio": different / len(cells) if cells else 0.0})
    return result


def _managed_schedules(db, students, courses, *, lock=False):
    start, _ = semester.week_date_range(1)
    _, end = semester.week_date_range(settings.TOTAL_WEEKS)
    query = db.query(Schedule).filter(
        Schedule.user_id.in_([student.id for student in students]),
        Schedule.course_id.in_({course.id for course in courses}),
        Schedule.class_date >= start, Schedule.class_date <= end,
    ).order_by(Schedule.id)
    return (query.with_for_update() if lock else query).all()


def backup_demo_schedules(db, students, courses):
    """删除前保存完整原课表；不包含账户密码、JWT、Key 或其他业务数据。"""
    rows = _managed_schedules(db, students, courses, lock=True)
    folder = BACKEND / "backups" / "demo_schedules"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{stamp}-{uuid4().hex[:8]}.json"
    columns = [column.name for column in Schedule.__table__.columns]
    snapshot = {"data_version": DATA_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "scope": {"student_ids": [student.id for student in students],
                          "course_ids": sorted({course.id for course in courses}),
                          "start_date": str(semester.week_date_range(1)[0]),
                          "end_date": str(semester.week_date_range(settings.TOTAL_WEEKS)[1])},
                "count": len(rows),
                "schedules": [{column: getattr(row, column) for column in columns} for row in rows]}
    with path.open("x", encoding="utf-8") as handle:
        path.chmod(0o600)
        json.dump(snapshot, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
    print(f"演示课表修改前备份：{path}（{len(rows)} 条）")
    return path


def clear_demo_schedules(db, students, courses):
    """仅删除本学期演示账号的模板课表；不删除任何课程。"""
    rows = _managed_schedules(db, students, courses, lock=True)
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


def sync_schedules(db, students, courses, wanted):
    """只同步演示范围；重复执行复用同记录 ID，不清空整张 schedules 表。"""
    student_ids = [student.id for student in students]
    course_ids = [course.id for course in courses]
    managed = _managed_schedules(db, students, courses, lock=True)
    start, _ = semester.week_date_range(1)
    _, end = semester.week_date_range(settings.TOTAL_WEEKS)
    other = db.query(Schedule).filter(
        Schedule.user_id.in_(student_ids), ~Schedule.course_id.in_(course_ids),
        Schedule.class_date >= start, Schedule.class_date <= end,
    ).all()
    other_cells = {(row.user_id, row.class_date, row.period) for row in other}
    if any((row["user_id"], row["class_date"], row["period"]) in other_cells
           for row in wanted):
        raise BusinessException(
            "演示排课与非模板课程冲突，请先调整冲突课表；未改动任何记录", code=409
        )
    desired = {(row["user_id"], row["class_date"], row["period"], row["course_id"]): row
               for row in wanted}
    kept = set()
    deleted = updated = created = 0
    for row in managed:
        key = (row.user_id, row.class_date, row.period, row.course_id)
        if key not in desired or key in kept:
            db.delete(row)
            deleted += 1
            continue
        kept.add(key)
        changed = False
        for field, value in desired[key].items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        updated += int(changed)
    for key, data in desired.items():
        if key not in kept:
            db.add(Schedule(**data))
            created += 1
    db.flush()
    return {"created": created, "updated": updated, "deleted": deleted,
            "unchanged": len(kept) - updated}


def print_weekly_summary(students, courses, wanted):
    start, _ = semester.week_date_range(1)
    _, end = semester.week_date_range(settings.TOTAL_WEEKS)
    print(f"数据版本：{DATA_VERSION}")
    print(f"学期：{start} 至 {end}，{settings.TOTAL_WEEKS} 周；基础随机种子 {RANDOM_SEED}")
    print("固定节次：" + " / ".join(PERIODS))
    for student in students:
        rows = [row for row in wanted if row["user_id"] == student.id]
        print(f"\n{student.username}（{student.name}）：{len(rows)} 条课表")
        print("周次 | 日期区间                  | 上课天数 | 课节数 | 节假日说明")
        for week in range(1, settings.TOTAL_WEEKS + 1):
            monday, sunday = semester.week_date_range(week)
            selected = [row for row in rows if monday <= row["class_date"] <= sunday]
            days = len({row["class_date"] for row in selected})
            notice = semester.week_holiday_info(week)["holiday_notice"] or "—"
            print(f"{week:>2}   | {monday} ~ {sunday} | {days:>8} | {len(selected):>6} | {notice}")
    names = {course.id: course.name for course in courses}
    for student in sorted(students, key=lambda student: student.id):
        personal_seed = student_random_seed(student)
        print(f"\n{student.username} 固定课程安排（学生随机种子 {personal_seed}）：")
        for assignment in assign_courses(courses, random.Random(personal_seed),
                                         sparse=student.username == "bbb"):
            print(f"{assignment.code:<7} {names[assignment.course_id]:<12} "
                  f"星期{assignment.weekday + 1} {assignment.period} "
                  f"第{assignment.start_week}-{assignment.end_week}周 "
                  f"{assignment.teacher} {assignment.location}")
    summary = weekly_difference_summary(students, wanted)
    print("\n两名学生逐周差异（仅比较至少一人有课的格子，双空格不参与计算）：")
    print("周次 | aaa课节 | bbb课节 | 相同格子 | 不同格子 | 差异比例")
    for row in summary:
        print(f"{row['week']:>2}   | {row['aaa_count']:>7} | {row['bbb_count']:>7} | "
              f"{row['same_cells']:>8} | {row['different_cells']:>8} | {row['different_ratio']:.1%}")
    compared = sum(row["compared_cells"] for row in summary)
    different = sum(row["different_cells"] for row in summary)
    print(f"全学期：相同 {compared - different} 格，不同 {different} 格，"
          f"差异 {different / compared:.1%}" if compared else "全学期无课表")


def _existing_template_courses(db):
    codes = [code for code, _, _ in CS_COURSES]
    names = [name for _, name, _ in CS_COURSES]
    return db.query(Course).filter((Course.code.in_(codes)) | (Course.name.in_(names))).all()


def seed(with_courses=True, *, preview=False, reseed=False):
    with SessionLocal() as db:
        with db.begin():
            if with_courses and not preview:
                courses, created = ensure_courses(db)
            else:
                courses, created = _existing_template_courses(db), 0
            students = _students(db)
            # 规划先完成且通过差异/覆盖检查，再备份、清理和写入。
            wanted = build_schedule(students, courses)
            if not preview:
                backup_demo_schedules(db, students, courses)
                removed = clear_demo_schedules(db, students, courses) if reseed else 0
                stats = sync_schedules(db, students, courses, wanted)
        if preview:
            print("只读预览：未新增、删除或修改数据库记录，未生成备份")
        else:
            print(f"唯一课程：{len({course.id for course in courses})} 门（新建 {created} 门）；"
                  f"课表新增 {stats['created']}，更新 {stats['updated']}，"
                  f"移除旧演示记录 {stats['deleted'] + removed}，未变化 {stats['unchanged']}")
        print_weekly_summary(students, courses, wanted)
    return 0


def clean():
    """仅清演示账号本学期模板课表；课程、其他学生及业务资产全部保留。"""
    with SessionLocal() as db:
        with db.begin():
            students = _students(db)
            courses = _existing_template_courses(db)
            backup_demo_schedules(db, students, courses)
            removed = clear_demo_schedules(db, students, courses)
        print(f"已删除本学期演示课表 {removed} 条；未删除任何课程")
        print(f"剩余：课表 {db.query(Schedule).count()} 条，"
              f"课程 {db.query(Course).count()} 门")
    return 0


def main():
    parser = argparse.ArgumentParser(description="演示课程与课表种子数据")
    parser.add_argument("--days", type=int, help="兼容旧参数，仅接受学期总天数 140")
    parser.add_argument("--no-courses", action="store_true", help="不新增课程，只用现有课程")
    parser.add_argument("--preview", action="store_true", help="只读预览整学期安排，不写数据库")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--clean", action="store_true", help="仅清理演示课表，保留所有课程")
    action.add_argument("--reseed", action="store_true", help="同一事务内清理并重建演示课表")
    args = parser.parse_args()

    if args.days is not None and args.days != settings.TOTAL_WEEKS * 7:
        parser.error(f"课表覆盖固定学期 {settings.TOTAL_WEEKS} 周；--days 仅接受 {settings.TOTAL_WEEKS * 7}")
    if args.preview and (args.clean or args.reseed):
        parser.error("--preview 不能与 --clean / --reseed 同时使用")
    if args.clean:
        return clean()
    return seed(not args.no_courses, preview=args.preview, reseed=args.reseed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BusinessException as exc:
        print(f"失败（业务码 {exc.code}）：{exc.message}", file=sys.stderr)
        sys.exit(1)
    except SQLAlchemyError:
        print("数据库操作失败，未完成的事务已回滚；请确认迁移完成且数据库可用", file=sys.stderr)
        sys.exit(1)
