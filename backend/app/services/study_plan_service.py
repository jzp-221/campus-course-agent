from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.db_errors import database_business_errors
from app.common.response import PageResponse
from app.config import STUDY_PLAN_MAX_LENGTH
from app.models.study_plan import StudyPlan
from app.models.user import User
from app.schemas.study_plan import StudyPlanCreateRequest, StudyPlanResponse


def list_page_plans(
    db: Session, current_user: User, page: int = 1, page_size: int = 10
) -> PageResponse:
    """查询当前用户的学习计划，最新计划排在前面。"""
    query = db.query(StudyPlan).filter(StudyPlan.user_id == current_user.id)
    total = query.count()
    rows = (
        query.order_by(StudyPlan.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PageResponse(
        list=[StudyPlanResponse.model_validate(row) for row in rows], total=total
    )


def _validated_content(content: str) -> str:
    """创建与修改共用同一套正文校验；参数类错误统一用 400。"""
    if not content:
        raise BusinessException(message="计划内容不能为空", code=400)
    if len(content) > STUDY_PLAN_MAX_LENGTH:
        raise BusinessException(
            message=f"计划内容不能超过{STUDY_PLAN_MAX_LENGTH}字符", code=400
        )
    return content


@database_business_errors
def save_plan(
    db: Session, current_user: User, data: StudyPlanCreateRequest
) -> StudyPlanResponse:
    """保存本人的学习计划，供接口和后续助手共用。"""
    row = StudyPlan(user_id=current_user.id, content=_validated_content(data.content))
    db.add(row)
    db.commit()
    db.refresh(row)
    return StudyPlanResponse.model_validate(row)


def create_study_plan(
    db: Session, current_user: User, data: StudyPlanCreateRequest
) -> StudyPlanResponse:
    """通过页面创建学习计划。"""
    return save_plan(db, current_user, data)


@database_business_errors
def update_study_plan(
    db: Session, current_user: User, plan_id: int, data: StudyPlanCreateRequest
) -> StudyPlanResponse:
    """仅允许修改当前用户自己的计划；他人的计划一律按不存在处理。"""
    row = (
        db.query(StudyPlan)
        .filter(StudyPlan.id == plan_id, StudyPlan.user_id == current_user.id)
        .first()
    )
    if not row:
        raise BusinessException(message="学习计划不存在", code=404)
    row.content = _validated_content(data.content)
    db.commit()
    db.refresh(row)
    return StudyPlanResponse.model_validate(row)


@database_business_errors
def delete_study_plan(db: Session, current_user: User, plan_id: int) -> None:
    """仅删除当前用户自己的计划。"""
    row = (
        db.query(StudyPlan)
        .filter(StudyPlan.id == plan_id, StudyPlan.user_id == current_user.id)
        .first()
    )
    if not row:
        raise BusinessException(message="学习计划不存在", code=404)
    db.delete(row)
    db.commit()
