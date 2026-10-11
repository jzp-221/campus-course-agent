# P1：DeepSeek + ReAct 课程查询 Agent

当前工具白名单已在 [P3](P3-SSE真流式与学习计划.md) 扩展为课表查询与学习计划保存，下文保留 P1 的阶段说明和验收记录。

本阶段仅接入 `get_my_schedules` 一个工具。没有 RAG、前端、SSE，也不保存服务端会话。Agent 使用 `langchain.agents.create_agent` 的 LangGraph 工具调用循环，DeepSeek 通过 `ChatOpenAI` 的 OpenAI 兼容 Chat Completions 接口调用。

## 配置与依赖

工作目录为 `/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend`。

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'from langchain.agents import create_agent; from langchain_openai import ChatOpenAI; print("imports OK")'
.venv/bin/python -m pip check
```

在现有 `backend/.env` 中自行加入以下配置，保留原来的数据库和 JWT 配置：

```ini
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_TIMEOUT_SECONDS=30
AGENT_MAX_ITERATIONS=6
```

| 配置 | 作用 |
| --- | --- |
| `LLM_API_KEY` | 必须自行填入有效 DeepSeek Key；配置示例和代码不包含真实 Key。 |
| `LLM_BASE_URL` | OpenAI 兼容服务地址，默认使用 DeepSeek。 |
| `LLM_MODEL` | 默认 `deepseek-chat`。 |
| `LLM_TIMEOUT_SECONDS` | 每次模型请求的超时，默认 30 秒；关闭 SDK 自动重试。 |
| `AGENT_MAX_ITERATIONS` | 单次请求最多的模型调用轮数，包含最后生成回答的一轮，默认 6；超过后返回错误，不返回假回答。 |

Key 使用 `SecretStr` 保存；服务不输出 SDK 异常原文，不把 Key、JWT 放入提示词或工具记录。`.gitignore` 覆盖 `.env`、`.venv/`、各层 `__pycache__/`、`*.pyc`、`.DS_Store`、`uploads/`。如果这些文件在已有仓库中曾被跟踪，仅添加忽略规则不会自动取消跟踪，应另行检查索引。

## HTTP 接口

配置 `.env` 后，重新启动你自己的 FastAPI 终端，使新配置生效。MySQL、FastAPI、MCP 的启动命令见 [第07节联调手册](第07节-联调手册.md)。不要重复启动 8000 服务。

新增接口：`POST http://127.0.0.1:8000/api/agent/chat`。先调用已有 `POST /api/auth/login` 登录学生，在 ApiPost 的认证中使用该次登录返回的 Bearer Token，Body 选择 JSON：

```json
{
  "message": "我明天有什么课？",
  "history": []
}
```

成功响应结构：

```json
{
  "code": 200,
  "message": "请求成功",
  "data": {
    "answer": "模型根据实际工具结果生成的回答",
    "tool_calls": [
      {"name": "get_my_schedules", "args": {"date": "YYYY-MM-DD"}}
    ]
  }
}
```

其中示例 `YYYY-MM-DD` 是日期格式说明，实际请求时由系统提示词给出真实日期。服务使用 `Asia/Shanghai`，在每次请求中计算今天和明天并写入系统提示词，模型查询“明天”时直接采用系统给定的明天日期。

后续问答由调用方传入纯文字历史：

```json
{
  "message": "那明天在哪里上课？",
  "history": [
    {"role": "user", "content": "我明天有什么课？"},
    {"role": "assistant", "content": "上一轮收到的最终回答文字"}
  ]
}
```

历史只允许 `user` / `assistant` 及 `content`，不允许传入 `system`、`tool`、`tool_calls` 或 `user_id`。不提供 session ID、checkpointer、数据库会话或跨请求工具缓存。

每次 HTTP 请求都将当次 Authorization 原样传入 `mcp_client_service.get_schedule_tools`，创建新的 MCP 客户端、工具实例和 Agent。只保留名为 `get_my_schedules` 的工具；运行时进一步拒绝非 `date` 参数。用户身份仍由 MCP 验证 JWT 后确定。

错误沿用项目统一响应：业务错误通常为 HTTP 200、JSON 内业务码非 200。Key 缺失时为 `code=503`，提示明确包含 `LLM_API_KEY`，且在模型构造及 MCP 客户端创建之前退出。工具失败时停止生成课表回答，达到循环上限时返回 `code=429`；无有效登录凭证仍由现有鉴权返回 HTTP 401。

## 真实 DeepSeek 冒烟

前提：MySQL 和 FastAPI 已运行；8001 空闲；你已自行在 `.env` 配置有效 Key。该脚本会产生实际模型请求并消耗账户额度。

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python tests/manual/agent_smoke.py
echo $?
```

脚本复用第07节验证的登录、业务接口造数和清理机制，自动启动并关闭独立 MCP。它给 aaa 创建明天和后天的临时课表，并给 bbb 创建一条临时课表；通过新的 Agent 服务提问“我明天有什么课？”，打印最终回答与本轮**实际执行**的工具名和参数。断言工具确实执行、只有 `get_my_schedules`、参数无 `user_id`、查询日期是系统给定的明天，且回答包含真实临时课程名。

冒烟脚本从自己的新进程读取 `.env`，直接调用 `agent_service.run_agent`；Agent 的课表工具仍走真实 **MCP Streamable HTTP → MySQL**，不会在 Python 中直调课表业务服务。这样可以验证真实模型链路而不依赖另一个 FastAPI 进程的 LLM 配置是否重新加载。HTTP 接口本身另由离线 API 测试验证，也可按上面的 ApiPost 步骤验收。

默认登录 admin、aaa、bbb 的演示账号；若改过密码，按第07节手册设置 `E2E_ADMIN_PASSWORD`、`E2E_AAA_PASSWORD`、`E2E_BBB_PASSWORD`。成功或失败都会尝试删除本次临时课表/课程并关闭自己启动的 MCP，最终以非零退出码报告任何失败。Key 缺失则立即退出，不启动或触达 MCP，不造数据。

成功输出最后应为：

```text
最终回答：
<DeepSeek 根据查询结果回答明天的课程、节次和地点>
本轮实际执行的工具调用：
[
  {"name": "get_my_schedules", "args": {"date": "<系统给定的明天日期>"}}
]
[PASS] DeepSeek 实际调用 get_my_schedules；日期正确、无 user_id，回答包含真实课程
[PASS] 临时 3 条课表及 1 门课程已删除并通过回查
[PASS] 脚本启动的 MCP 已关闭，8001 已释放
[PASS] Agent 真实冒烟全部通过（退出码 0）
```

上述是预期格式。2026-10-09 已在取得本次课表外发授权后完成真实 DeepSeek 实测，退出码为 `0`。系统给出的明天为 `2026-10-10`，实际执行记录如下：

```json
[
  {"name": "get_my_schedules", "args": {"date": "2026-10-10"}}
]
```

模型最终回答明天有 1 门课，包含本次临时课程的完整名称、节次 `E2E-A1` 和地点；断言确认实际课程名与造数记录一致。三条临时课表、一门临时课程已通过回查确认删除，MCP 进程已关闭，8001 已释放。没有把真实 Key 或登录 Token 写入本手册。

## 回归命令

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"

# 原有 47 项，离线
.venv/bin/python -m unittest tests.test_mcp_client tests.test_mcp_schedules tests.test_study_plans

# 新增 Agent 离线测试：真实 create_agent 图 + 注入假模型/假 MCP
.venv/bin/python -m unittest tests.test_agent -v

# 第07节真实 HTTP 回归：保持 FastAPI/MySQL 运行，8001 空闲
.venv/bin/python tests/manual/e2e_mcp.py

# DeepSeek 真实冒烟：前三组之后执行，避免两个脚本同时争用 8001
.venv/bin/python tests/manual/agent_smoke.py
```

离线测试覆盖正常工具调用、工具业务错误、传输错误、循环上限、缺 Key 时模型/MCP 零调用、每次 Authorization 独立客户端和工具、身份参数拒绝、系统日期注入、纯文字历史、API 参数校验及明确配置错误。测试不读真实 `.env`，不连接外部模型或 MySQL。

本次验收结果：原有 47 项、新增 13 项单测合并执行共 60 项，全部 `OK`；第07节真实 HTTP 回归退出码 `0`；DeepSeek Agent 真实冒烟退出码 `0`。

配置排查：`JWT_SECRET_KEY` 与 `LLM_API_KEY` 是两种不同凭证。填写模型 Key 时不要清空 JWT 配置；修改 `.env` 后需重启已有 FastAPI，因为仅修改 `.env` 不保证触发 Uvicorn 重载。FastAPI 与独立 MCP 必须读取一致的 JWT 配置，否则刚登录得到的 Token 也会被 MCP 拒绝。

实现依据：[LangChain create_agent](https://reference.langchain.com/python/langchain/agents/factory/create_agent)、[模型调用轮数限制](https://reference.langchain.com/python/langchain/agents/middleware/model_call_limit/ModelCallLimitMiddleware)、[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。
