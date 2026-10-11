"""Separate public deployment entry point. Never run this with backend/.env."""

import asyncio

from deploy.demo.policy import (
    DemoRejection, Identity, Limits, PublicDemoApp, capped_factory, capped_stream_factory,
)
from deploy.demo.seed import require_demo_target

# Fail closed BEFORE importing app.main (its existing startup creates tables).
require_demo_target()

from fastapi import HTTPException
from app.database import SessionLocal
from app.dependencies.auth import get_current_user
from app.main import app as original_app
from app.services import agent_service, agent_stream_service


limits = Limits.from_environment()


# The deployment wrapper supplies a limit to the same real OpenAI-compatible model.
# Only this process is changed; app.main and the existing local entry point stay intact.
agent_service.build_model = capped_factory(agent_service.build_model, limits.max_output_tokens)
agent_stream_service.build_stream_model = capped_stream_factory(
    agent_stream_service.build_stream_model, limits.max_output_tokens)


def authenticate_sync(authorization):
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise DemoRejection(401, "请先登录演示账号")
    try:
        with SessionLocal() as db:
            user = get_current_user(token=parts[1], db=db)
            return Identity(user.id, user.role)
    except HTTPException:
        raise DemoRejection(401, "登录已失效，请重新登录") from None
    except Exception:
        raise DemoRejection(503, "演示登录服务暂不可用，请稍后重试") from None


async def authenticate(authorization):
    return await asyncio.to_thread(authenticate_sync, authorization)


app = PublicDemoApp(original_app, authenticate=authenticate, limits=limits)
