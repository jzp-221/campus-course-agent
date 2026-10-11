"""SSE 事件契约与取消生命周期；不改变 P1 的非流式运行路径。"""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
import json
import inspect

import anyio
import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langchain_openai import ChatOpenAI

from app.common.exceptions import BusinessException
from app.config import settings
from app.schemas.agent import AgentChatRequest
from app.services import agent_service, mcp_client_service


EVENT_NAMES = {"status", "tool_start", "tool_end", "token", "done", "error"}


def event(name, data):
    return {"event": name, "data": json.dumps(data, ensure_ascii=False)}


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block["text"] for block in content if isinstance(block, dict)
                       and block.get("type") == "text" and isinstance(block.get("text"), str))
    return ""


async def build_stream_model(config):
    # LangChain 默认 HTTP 客户端被缓存。显式提供本次请求独占的客户端，
    # 避免断开/结束时关闭缓存连接池，影响另一个 SSE 或原有非流式请求。
    sync_client = httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS)
    async_client = httpx.AsyncClient(timeout=config.LLM_TIMEOUT_SECONDS)
    try:
        return ChatOpenAI(
            api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL,
            model=config.LLM_MODEL, timeout=config.LLM_TIMEOUT_SECONDS,
            max_retries=0, temperature=0, use_responses_api=False,
            http_client=sync_client, http_async_client=async_client,
        )
    except BaseException:
        sync_client.close()
        await async_client.aclose()
        raise


class StreamingToolGuard(agent_service.ScheduleToolGuard):
    def __init__(self, calls, emit, *, save_authorized=False):
        super().__init__(calls, save_authorized=save_authorized)
        self.emit = emit

    async def awrap_tool_call(self, request, handler):
        # 串行化并行工具调用，保证每个 start/end 都是一对，不产生交叉。
        async with self.execution_lock:
            name, args = self.validate_call(request)
            stage = {"get_my_schedules": "querying", "save_study_plan": "saving",
                     "search_course_docs": "retrieving"}[name]
            await self.emit("status", {"stage": stage})
            await self.emit("tool_start", {"name": name, "args": dict(args)})
            try:
                result = await self.execute(request, handler)
            except Exception:
                await self.emit("tool_end", {"name": name, "ok": False})
                raise
            await self.emit("tool_end", {"name": name, "ok": True})
            return result


class ModelCallsCounter(AgentMiddleware):
    """最终生成也计入同一个请求的模型调用上限。"""

    def __init__(self):
        super().__init__()
        self.count = 0

    async def awrap_model_call(self, request, handler):
        self.count += 1
        return await handler(request)


async def close_model(model):
    # 此模型属于本次请求；关闭 SDK 根客户端，也关闭正在使用的 HTTP 连接池。
    async def close():
        root_async = getattr(model, "root_async_client", None)
        root_sync = getattr(model, "root_client", None)
        if root_async is not None:
            await root_async.close()
        if root_sync is not None:
            root_sync.close()
    closing = asyncio.create_task(close())
    try:
        with anyio.CancelScope(shield=True):
            await asyncio.shield(closing)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            await closing
        raise


@dataclass
class PreparedStream:
    authorization: str
    request: AgentChatRequest
    config: object
    model_factory: object
    tool_loader: object
    today_provider: object

    async def _produce(self, queue):
        model = None

        async def emit(name, data):
            await queue.put(event(name, data))

        try:
            await emit("status", {"stage": "thinking"})
            tools = await self.tool_loader(self.authorization)
            save_authorized = agent_service.has_save_intent(self.request.message)
            agent_service.validate_tools(tools, requires_save=save_authorized)
            calls = []
            model = self.model_factory(self.config)
            if inspect.isawaitable(model):
                model = await model
            counter = ModelCallsCounter()
            prompt = agent_service.build_system_prompt(self.today_provider())
            guard = StreamingToolGuard(calls, emit, save_authorized=save_authorized)
            agent = create_agent(
                model=model, tools=tools,
                system_prompt=prompt + "\n当前是工具决策阶段：需要工具时只发起工具调用；"
                    "工具操作已充分完成或不需要工具时只回复 READY，勿在此阶段撰写最终回答。"
                    "需要保存时仍必须先完成真实保存调用，不能用 READY 代替写入。",
                middleware=[
                    ModelCallLimitMiddleware(run_limit=self.config.AGENT_MAX_ITERATIONS, exit_behavior="error"),
                    counter,
                    guard,
                ],
            )
            messages = [HumanMessage(content=item.content) if item.role == "user"
                        else AIMessage(content=item.content) for item in self.request.history]
            messages.append(HumanMessage(content=self.request.message))
            # 当前 chunk 不含工具增量，并不能证明后续 chunk 不会请求工具。
            # 决策阶段允许工具、禁用文字流，完整识别并丢弃工具轮推测文字。
            # 回答阶段明确 tool_choice=none，因而可以安全逐块立即推送。
            original_disable_streaming = model.disable_streaming
            try:
                model.disable_streaming = True
                result = await agent.ainvoke(
                    {"messages": messages},
                    config={"recursion_limit": 8 * self.config.AGENT_MAX_ITERATIONS + 8},
                )
            finally:
                model.disable_streaming = original_disable_streaming
            final = result["messages"][-1]
            if not isinstance(final, AIMessage) or final.tool_calls:
                raise BusinessException("Agent 未完成工具决策", code=502)
            if save_authorized and not guard.saved:
                raise BusinessException("未确认学习计划已保存，不能报告保存成功，请重试", code=502)
            if counter.count >= self.config.AGENT_MAX_ITERATIONS:
                raise BusinessException(
                    f"Agent 已达到最大循环轮数（{self.config.AGENT_MAX_ITERATIONS}），请简化问题后重试", code=429)
            counter.count += 1
            context = [item.model_copy(update={"content": ""})
                       if isinstance(item, AIMessage) and item.tool_calls else item
                       for item in result["messages"][:-1]]
            final_messages = [SystemMessage(content=prompt + "\n现在只生成最终回答，禁止任何工具调用。"
                "依据以上本轮真实工具结果回答，不输出决策草稿，不得声称尚未成功的保存已完成。"),
                *context]
            stream = model.bind(tool_choice="none").astream_events(final_messages, version="v2")
            answer = None
            emitted = []
            async with aclosing(stream):
                async for update in stream:
                    kind = update["event"]
                    if kind == "on_chat_model_stream":
                        chunk = update["data"]["chunk"]
                        if getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None):
                            raise BusinessException("最终回答轮禁止工具调用", code=400)
                        text = text_content(chunk.content)
                        if text:
                            emitted.append(text)
                            await emit("token", {"text": text})
                    elif kind == "on_chat_model_end":
                        output = update["data"]["output"]
                        if getattr(output, "tool_calls", None):
                            raise BusinessException("最终回答轮禁止工具调用", code=400)
                        answer = text_content(output.content)
            if answer is None or not answer.strip():
                raise BusinessException("大模型返回了空回答", code=502)
            if "".join(emitted) != answer:
                raise BusinessException("流式回答文字与最终结果不一致", code=502)
            await emit("done", {"answer": answer, "tool_calls": calls})
        except asyncio.CancelledError:
            raise
        except BusinessException as exc:
            await emit("error", {"code": exc.code, "message": exc.message})
        except (ModelCallLimitExceededError, GraphRecursionError):
            await emit("error", {"code": 429, "message":
                f"Agent 已达到最大循环轮数（{self.config.AGENT_MAX_ITERATIONS}），请简化问题后重试"})
        except Exception:
            await emit("error", {"code": 502, "message":
                "大模型调用失败，请检查 DeepSeek 配置、额度或网络后重试"})
        finally:
            if model is not None:
                await close_model(model)

    async def events(self):
        queue = asyncio.Queue(maxsize=32)
        producer = asyncio.create_task(self._produce(queue), name="agent-sse-producer")
        terminal = False
        try:
            while True:
                item = await queue.get()
                yield item
                if item["event"] in {"done", "error"}:
                    terminal = True
                    break
        finally:
            if not terminal and not producer.done():
                producer.cancel()
            # ASGI 断开时外层 CancelScope 也处于取消状态，需要 shield 完成回收。
            with anyio.CancelScope(shield=True):
                try:
                    await producer
                except asyncio.CancelledError:
                    pass


def prepare_stream(authorization, message, history=None, *, config=None,
                   model_factory=None, tool_loader=None, today_provider=None):
    config = settings if config is None else config
    if not config.LLM_API_KEY.get_secret_value().strip():
        raise BusinessException("未配置 LLM_API_KEY，请在 backend/.env 中配置 DeepSeek Key 后重启后端", code=503)
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise BusinessException("缺少有效的 Authorization 登录凭证", code=401)
    request = AgentChatRequest(message=message, history=history or [])
    return PreparedStream(authorization, request, config, model_factory or build_stream_model,
                          tool_loader or mcp_client_service.get_schedule_tools,
                          today_provider or agent_service.shanghai_today)
