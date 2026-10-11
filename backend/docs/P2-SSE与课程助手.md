# P2：SSE 流式接口与课程助手页面

当前实现已由 [P3](P3-SSE真流式与学习计划.md) 修复为即时原生文字流，并增加学习计划保存工具。下文的 P2 缓冲策略属于当时的实现记录；当前行为、时间轴及回归命令以 P3 手册为准。

新增 `POST /api/agent/chat/stream` 和前端 `/manager/assistant`。原有 `POST /api/agent/chat` 的 JSON 响应、调用路径和测试保持不变。两条路径均只挂载 `get_my_schedules`，配置沿用 [P1 手册](P1-DeepSeek-Agent.md)。

## 启动与访问

MySQL、FastAPI 的启动方式见 [第07节联调手册](第07节-联调手册.md)。先复用已有服务，避免重复占用端口。只有缺少服务时，分别在独立终端执行：

```bash
# FastAPI，8000
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

```bash
# MCP，8001；使用页面时需要持续运行
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python mcp_server.py
```

```bash
# Vite 前端
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/frontend"
npm run dev
```

打开 `http://localhost:5173`，登录后点击侧栏“课程助手”，或直接访问 `http://localhost:5173/manager/assistant`。本机 Vite 当前监听 IPv6 localhost，使用这个地址。填写 `.env` 后需重启已有 FastAPI 才能读取新的模型配置；本次实现未改动 `.env`。

## SSE 契约

请求与非流式接口共用 `AgentChatRequest`：

```http
POST /api/agent/chat/stream
Authorization: Bearer <本次登录 Token>
Content-Type: application/json
Accept: text/event-stream
```

```json
{"message":"我明天有什么课？","history":[{"role":"user","content":"之前的问题"},{"role":"assistant","content":"之前的最终回答"}]}
```

`history` 可省略，只允许问答文字，不能传入系统消息、工具消息或 `user_id`。服务端不保存会话。成功建立流后的响应为 HTTP 200、`Content-Type: text/event-stream`，带 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。每个事件是 `event:`、JSON `data:` 加空行：

```text
event: tool_start
data: {"name":"get_my_schedules","args":{"date":"2026-10-10"}}

```

| 事件名 | 固定 JSON 字段 | 含义 |
| --- | --- | --- |
| `status` | `{"stage":"thinking"}` 或 `{"stage":"querying"}` | 后端固定状态键，前端映射中文，不由模型生成。 |
| `tool_start` | `{"name":"get_my_schedules","args":{"date":"YYYY-MM-DD"}}` | 本轮实际工具开始；`args` 只能有可选 `date`，允许省略或为 null。 |
| `tool_end` | `{"name":"get_my_schedules","ok":true}` | 工具结束；失败时 `ok=false`，随后 `error`。 |
| `token` | `{"text":"增量文字"}` | 最终回答的原生文本块。 |
| `done` | `{"answer":"完整回答","tool_calls":[{"name":"get_my_schedules","args":{"date":"YYYY-MM-DD"}}]}` | 非空最终回答及实际执行记录；结束流。 |
| `error` | `{"code":500,"message":"业务错误提示"}` | 固定业务码/提示字段；结束流，不再发送 `done`。 |

顺序为 `status(thinking)` → `[status(querying) → tool_start → tool_end]*` → `token*` → `done`。工具执行串行化，开始/结束成对，不交叉；没有工具时可直接进入回答。任意阶段失败以 `error` 终止；已开始的失败工具先发 `tool_end(ok=false)`。全部 `token.text` 拼接与 `done.answer` 一致。SSE 心跳是以 `:` 开头的注释，前端忽略，不属于第七种事件。

模型可能先说一句话再请求工具。为严格满足上述顺序，当前实现按模型轮次暂存原生文字块，确认最终回答轮后才发送；工具轮的前置文字不会出现在气泡中。因此处理过程会及时出现，回答文字会等最终轮结束后成批到达。没有调用非流式服务再人为逐字拆分。若注入的离线模型不提供文本流，则发送其原生完整文字块。

## 错误与回收

鉴权依赖在建立流前运行：未登录、过期或伪造 Token 返回现有 HTTP 401 JSON。Key 缺失在构造模型、获取 MCP 工具之前判定，返回现有业务 JSON `code=503`，不会推送 SSE 或触达 MCP。请求格式错误沿用现有校验响应。

建立流后仅通过 `error` 报错，不能更换 HTTP 状态。工具/传输错误沿用 P1 业务码，轮数上限 `429`，模型调用异常 `502`；不回传 SDK 异常原文或密钥。每次请求重新获取带当次 Authorization 的 MCP 工具，参数仍只允许 `date`，身份由 MCP 校验 JWT 后确定。

页面“停止回答”、离开页面、浏览器关闭连接会取消 fetch。ASGI 断开检测随后取消请求的生产任务、模型事件迭代器与正在执行的 MCP 调用；最后关闭本次模型 SDK 的同步/异步连接池。流式模型显式提供请求独占的 HTTP 客户端，避免关闭 LangChain 默认缓存的连接池而影响下一次对话或非流式请求。回收阶段使用取消屏蔽，防止连接尚未释放就退出。取消不会伪装成成功 `done`，也不保存失败或未完成的回答到历史。

## 前端对接

`src/api/agent.js` 使用 `fetch` POST + `ReadableStream.getReader()`，从 `utils/auth.js` 获取 Token，沿用 `utils/request.js` 的 `VITE_API_BASE_URL` 和 Bearer 约定；流前 JSON 错误和流内 `error` 均处理，401 清登录并跳转。没有使用 EventSource。

`src/utils/sse.js` 使用增量 TextDecoder 后的字符串解析帧，兼容 UTF-8 分块、CRLF、多行 data 和心跳，校验六种事件的固定字段。流提前结束且没有 `done/error` 会提示重试。

`Chat.vue` 的聊天气泡和处理过程各自维护。工具过程显示“查询我的课表”、日期和成功/失败状态，不写进历史。仅在收到 `done` 后保存 user/assistant 文字对，下一请求发送最近 50 条文字记录；刷新或清空对话即清空本页历史。文字块累积后每 50ms 批量刷新，完成时立即刷新；用 `marked.parse` 转 Markdown，再用 `DOMPurify.sanitize` 净化，净化后的 HTML 才传给 `v-html`。

## 可重复验证

离线测试不读取真实 Key，不调用网络：

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python -m unittest tests.test_mcp_client tests.test_mcp_schedules tests.test_study_plans tests.test_agent tests.test_agent_stream
```

原有 60 项加新增 11 项，共 71 项。新增覆盖正常顺序/原生文字块、工具失败、循环上限、缺 Key、越权参数、未登录流前拒绝、取消模型、取消 MCP，真实 ASGI `http.disconnect` 回收，以及请求连接池独立创建/关闭。

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/frontend"
node --test tests/sse.test.mjs
npm run build
```

解析器有 6 项离线测试，包括多字节中文/emoji、分块换行、心跳、多行 data、固定字段与不完整帧。

真实验证需要已有 FastAPI/MySQL、配置好的 DeepSeek Key，以及空闲 8001。先在手工 MCP 终端 Ctrl+C，然后依次运行，不能同时执行以免争用 8001：

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python tests/manual/e2e_mcp.py
.venv/bin/python tests/manual/agent_smoke.py
.venv/bin/python tests/manual/sse_smoke.py
```

三个脚本分别启动并关闭自己的 MCP，业务接口造唯一临时数据，并在 finally 删除及回查；不启动 FastAPI、不修改 `.env`。SSE 脚本通过真实 `curl -N` POST 打印完整帧，Token 只经 curl stdin 配置传入，不出现在进程 argv、文件或输出中。第一条流验证实际工具调用、明天日期、真实课程名、非空答案、完整帧顺序；连续第二条成功流验证连接池独立回收后仍可对话。随后关闭自己启动的 MCP，第三条流验证 `status → error`，这条错误流不调用模型。三条流合计覆盖全部六种类型，失败退出非零。

2026-10-09 本机实测：71 项后端离线测试、6 项前端解析测试及生产构建通过；三个真实回归脚本均退出 `0`。SSE 正常流实际查询 `date=2026-10-10`，输出完整临时课程名，错误流返回 `code=500` 与“MCP 服务不可用，请确认已启动 mcp_server.py”。末尾输出：

```text
[PASS] SSE 六种事件、固定字段、顺序、非空最终回答及 error 终止均通过
[PASS] 临时 3 条课表及 1 门课程已删除并通过回查
```

手工页面验收时重新启动 MCP，登录学生，发送“我明天有什么课？”，观察独立过程区与 Markdown 回答；继续提问验证文字历史，生成时点停止或切换页面验证中断。无课时以实际查询结果回答，不需要手工伪造课程。

本次浏览器已用 aaa 验证侧栏入口、真实查询和后续问答：页面回答“明天（2026-10-10）暂无课程”，过程区单独显示“查询我的课表：完成”及日期；追问地点得到无课程所以无上课地点的回答。页面测试使用的临时 MCP 已关闭，后续使用聊天页需按启动命令运行 MCP。
