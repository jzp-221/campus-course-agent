"""P8 指定的放假、调休日期；日历逻辑统一读取本模块。"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class HolidayPeriod:
    name: str
    start_date: date
    end_date: date
    note: str = ""


HOLIDAY_PERIODS: tuple[HolidayPeriod, ...] = (
    HolidayPeriod("中秋节", date(2026, 9, 25), date(2026, 9, 27)),
    HolidayPeriod("国庆节", date(2026, 10, 1), date(2026, 10, 7)),
    # 2027 年元旦正式安排尚未发布：按本任务指定的惯例日期推定。
    HolidayPeriod(
        "元旦",
        date(2027, 1, 1),
        date(2027, 1, 3),
        note="2027 年正式安排尚未发布，按惯例推定",
    ),
)

# 这里只定义哪些日期为上班日，不推断补上哪个星期的课程。
MAKEUP_WORKDAYS: frozenset[date] = frozenset(
    {date(2026, 9, 20), date(2026, 10, 10)}
)
