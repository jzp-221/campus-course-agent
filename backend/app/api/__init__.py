from fastapi import APIRouter
from app.api.auth import router as auth_router
from app.api.user import router as user_router
from app.api.files import router as files_router
from app.api.course import router as course_router
from app.api.schedule import router as schedule_router
from app.api.study_plan import router as study_plan_router
from app.api.mcp import router as mcp_router
from app.api.agent import router as agent_router
from app.api.document import router as document_router
from app.api.assignment import router as assignment_router
from app.api.plan_task import router as plan_task_router
from app.api.learning_context import router as learning_context_router

api = APIRouter(prefix="/api")

api.include_router(auth_router)
api.include_router(user_router)
api.include_router(schedule_router)
api.include_router(files_router)
api.include_router(course_router)
api.include_router(study_plan_router)
api.include_router(mcp_router)
api.include_router(agent_router)
api.include_router(document_router)
api.include_router(assignment_router)
api.include_router(plan_task_router)
api.include_router(learning_context_router)
