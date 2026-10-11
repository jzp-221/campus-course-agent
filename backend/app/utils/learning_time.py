"""作业与任务使用上海本地墙上时间；数据库保存不带时区的 DATETIME。"""

from datetime import datetime
from zoneinfo import ZoneInfo


def local_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
