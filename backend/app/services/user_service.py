from operator import or_
from app.common.exceptions import BusinessException
from app.common.db_errors import database_business_errors
from app.common.response import PageResponse
from app.models.document import Document
from app.models.schedule import Schedule
from app.models.study_plan import StudyPlan
from app.models.assignment import Assignment
from app.models.plan_task import PlanTask
from app.models.user import User
from app.schemas.user import (
    PasswordUpdateRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from sqlalchemy.orm import Session

from app.utils import password
from app.utils.password import hash_password, verify_password


def get_user_info(user: User) -> UserResponse:
    return UserResponse.model_validate(user)


@database_business_errors
def update_user_info(db: Session, user: User, data: UserUpdateRequest):
    user_dict = data.model_dump(
        exclude_none=True, exclude={"role", "status"}
    )  # pydatic对象转换成字典
    for field, value in user_dict.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@database_business_errors
def update_password(db: Session, user: User, data: PasswordUpdateRequest):
    """修改密码"""
    if not verify_password(data.old_password, user.password):
        raise BusinessException(message="原密码错误")
    if data.old_password == data.new_password:
        raise BusinessException(message="新密码不能跟原密码相同")
    user.password = hash_password(data.new_password)
    db.commit()


def get_user_page_list(
    db: Session, page: int, page_size: int, keywords: str | None = None
):
    """分页模糊查询用户列表"""
    # select * from user where username like '%张%' or name like '%张%';
    query = db.query(User)
    if keywords:
        query = query.filter(
            or_(User.username.ilike(f"%{keywords}%"), User.name.ilike(f"%{keywords}%"))
        )
    total = query.count()
    items = (
        query.order_by(User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PageResponse(
        list=[UserResponse.model_validate(item) for item in items], total=total
    )


@database_business_errors
def create_user(db: Session, data: UserCreateRequest):
    """新增用户"""
    exist = db.query(User).filter(User.username == data.username).first()
    if exist:
        raise BusinessException(message="账号已存在")

    user = User(
        username=data.username,
        password=hash_password(data.password),
        name=data.name or data.username,
        role=data.role,
        email=data.email,
        phone=data.phone,
        avatar=data.avatar,
        status=data.status,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@database_business_errors
def update_user(db: Session, user_id: int, data: UserUpdateRequest):
    """更新用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise BusinessException(message="用户不存在", code=404)
    payload = data.model_dump(exclude_none=True)
    for filed, value in payload.items():
        setattr(user, filed, value)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@database_business_errors
def delete_user(db: Session, user_id: int, currut_user: User):
    """有课表、学习计划或上传文档的用户必须先清理，不级联删除。"""
    if user_id == currut_user.id:
        raise BusinessException(message="不能删除当前登录的账号", code=403)
    # MySQL 外键插入需获取父行共享锁；排他锁避免检查后又插入关联。
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not user:
        raise BusinessException(message="用户不存在", code=404)

    references = []
    for model, foreign_key, label, unit in (
        (Schedule, Schedule.user_id, "课表", "条"),
        (StudyPlan, StudyPlan.user_id, "学习计划", "条"),
        (Document, Document.uploader_id, "上传文档", "份"),
        (Assignment, Assignment.user_id, "作业/考试", "条"),
        (PlanTask, PlanTask.user_id, "计划任务", "条"),
    ):
        # 鉴权查询可能已在 REPEATABLE READ 下打开旧快照；locking read
        # 看当前已提交关联，不能用普通聚合查询漏掉刚提交的记录。
        count = len(db.query(model.id).filter(foreign_key == user_id).with_for_update().all())
        if count:
            references.append(f"{label} {count} {unit}")
    if references:
        raise BusinessException(
            message=f"该用户仍有关联数据：{'、'.join(references)}，请先清理后再删除",
            code=409,
        )
    db.delete(user)
    db.commit()
