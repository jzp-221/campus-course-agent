# P3：SSE 真流式与学习计划生成、保存

本文记录 P3 阶段实现与验收；当前三工具与知识库配置见 [P4-RAG知识库](P4-RAG知识库.md)。

当前工具白名单为 `get_my_schedules`、`save_study_plan`，没有 RAG。原 JSON 接口 `POST /api/agent/chat` 和流式接口 `POST /api/agent/chat/stream` 均可生成、保存学习计划；请求仍是 `message/history`，服务端不存会话。

## 运行与使用

配置沿用 [P1](P1-DeepSeek-Agent.md)，本次没有修改 `.env`。复用已有 MySQL、FastAPI、前端；在自己的 MCP 终端重启服务，让它加载保存工具：

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python mcp_server.py
```

登录后访问 `http://localhost:5173/manager/assistant`。示例：

- “我明天有什么课？”：查询本人课表并渐进回答。
- “帮我规划一下明天的学习安排”：根据真实课表生成文字，不保存。
- “帮我生成一份学习计划并保存”：保存成功后在“学习计划”页查看。
- “请保存上面的计划”：使用本页纯文字历史，当前指令授权保存。

历史只含问答文字，过程区、工具参数和结果不进入前端历史。刷新或清空对话会清空本页历史。停止或离开页面会取消请求。

## 真流式与顺序

P2 把整轮文字攒到 `on_chat_model_end` 后发送，已移除。现在最终轮的 `on_chat_model_stream` 收到不含工具增量的原生文字块，立即推送 `token`，不等待轮次结束，不人为切字，也不人为延时。

单看当前 chunk 没有工具增量，不能预测后续 chunk 是否会请求工具。为保证中间推测文字丢弃、工具事件始终在 token 前，实现分成两个阶段：

1. `create_agent` 工具决策阶段允许两个白名单工具，完整识别工具调用。中间文字不推送；工具操作充分完成后只回复 `READY`，避免重复撰写回答。
2. 最终回答阶段明确 `tool_choice=none`，使用真实工具结果和问答历史生成原生文字流。工具轮说明和最后的决策草稿移除；最终轮若仍出现工具增量会拒绝，不再执行工具。

最终生成增加一次模型调用，也计入同一 `AGENT_MAX_ITERATIONS` 上限，不够则返回 `429`。断开会取消决策、生成和 MCP 调用，回收本次独占 HTTP 连接池。没有在 Agent 中直调课表/学习计划业务服务绕过 MCP。

SSE 保持六种事件和固定字段：

| 事件 | JSON 字段 |
| --- | --- |
| `status` | `{"stage":"thinking"}`、`querying` 或新增固定键 `saving` |
| `tool_start` | `{"name":"工具名","args":{...}}`；查询只允许可选 `date`，保存只允许 `content` |
| `tool_end` | `{"name":"工具名","ok":true}`；失败为 false |
| `token` | `{"text":"原生文字块"}` |
| `done` | `{"answer":"完整回答","tool_calls":[{"name":"工具名","args":{...}}]}` |
| `error` | `{"code":500,"message":"业务错误提示"}` |

顺序为 `status(thinking)` → `[status(querying/saving) → tool_start → tool_end]*` → `token*` → `done`。任意失败以 `error` 终止；已开始的失败工具先发 `tool_end(ok=false)`。心跳注释不属于第七种事件。token 拼接必须等于 answer，流前鉴权/Key 判定保持 P2 行为。

前端 `Chat.vue` 使用 `createBufferedAnswer`：首 token 立即进入 `streaming`，每 50ms 批量刷新正文和净化后的 Markdown，收到 done 后进入 `done`。Vue reactive/watch 离线用例证明 `pending → streaming → done`，且完成前已显示部分正文。过程区分别展示“查询我的课表”和“保存学习计划”，保存失败不会显示完成。

## 保存防护

MCP 工具签名仅有 `content`，schema 明确 `additionalProperties=false`。身份只由当次 Authorization 的 JWT 解析，并检查活动用户；复用 `study_plan_service.save_plan`。正文非空、最多 2000 字符；成功返回真实 `id/content/create_time`，失败返回 `error/code`。模型不能传入 `user_id`、`plan_id` 等参数。

提示词要求当前用户明确要求保存/记录/存下来才保存。代码也检查当次文字；历史、引用示例、否定、未来或假设条件不能自动授权本轮。识别采用保守规则，含糊或条件式表达可能被拒绝，可改为直接的保存指令。

每个请求的 Guard 串行执行工具，第一次保存开始前即设置 `save_attempted`，确认成功后设置 `saved`。第二次保存被 `409` 拒绝；即使第一次已提交但响应超时/损坏，也不允许排队的第二次写入。失败终止本轮，不自动重试不确定写入。

未知工具/多余参数 `400`，未明确要求保存 `403`，重复保存 `409`。缺少保存工具 `503`，明确提示重启 MCP。没有真实保存却生成“已保存”的回答会被 `502` 拒绝；流式路径在回答推送前判定。保存结果校验和业务错误传播不会伪造成功，也不暴露 SDK 异常、Key 或 JWT。

## 可重复验证

后端离线测试不读取真实 Key，不依赖网络/MySQL：

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python -m unittest tests.test_mcp_client tests.test_mcp_schedules tests.test_study_plans tests.test_agent tests.test_agent_stream tests.test_mcp_study_plan_tool tests.test_agent_study_plans tests.test_agent_stream_progressive
```

共 107 项：原 71 项、MCP 保存 16 项、Agent 保存防护 15 项、渐进流 5 项。原 MCP schema 用例从单工具改为本阶段两个工具，仍严格验证 date 签名。新增渐进用例用门闩阻止模型轮次结束，此时客户端已经收到首 token，工具事件均已完成；覆盖生成/决策中断、半途失败、最终生成轮数以及成功/失败的保存。并行提交后坏回包用例确认仍只写一次。

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/frontend"
node --test tests/sse.test.mjs tests/bufferedAnswer.test.mjs
npm run build
```

共 12 项：原 6 项解析测试、新增 2 项保存事件/参数、4 项实际批量渲染 helper 用例；生产构建通过。

真实测试保持已有 FastAPI/MySQL 可用、8001 空闲，顺序执行：

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python tests/manual/e2e_mcp.py
.venv/bin/python tests/manual/agent_smoke.py
.venv/bin/python tests/manual/p3_smoke.py
```

P3 脚本通过真实 HTTP 验证只规划不存、明确保存后 GET 回查，aaa 可见而 bbb 不可见。既有 GET schema 不暴露 user_id，因此额外只读查询该记录的数值归属，不修改业务响应。非法 user_id 通过真实 MCP 与 Agent 两层验证；重复保存使用可控攻击模型，但运行真实 Agent 图及真实 MCP HTTP，断言只新增一条。

所有临时计划、课程和课表均通过业务 API/MCP 写入和删除，finally 回查清理并关闭自己启动的 MCP，不重复启动 8000。时间轴客户端用 `time.perf_counter()` 标记每个完整 SSE 帧并打印全部 event/data，断言至少两帧 token、工具事件在 token 前、首 token 到 done 至少 0.1 秒；不靠 sleep 制造间隔。

## 2026-10-10 实测证据

最终版本已通过 107 项后端测试、12 项前端测试、生产构建，旧 MCP/Agent 回归与 P3 真实冒烟均退出 `0`。真实模型只规划时没有新增学习计划；明确保存时成功回查本人记录，bbb 不可见；非法身份参数无落库；重复保存第二次返回 `409`，仅新增一条。

时间轴来自真实 DeepSeek → FastAPI SSE → HTTP 客户端的到达时间。完整帧时间见 [P3-SSE-时间轴.csv](P3-SSE-时间轴.csv)，对应请求查询 aaa 的明天（2026-10-11）临时课表并给出详细安排：

| 事件 | 从请求开始经过 |
| --- | ---: |
| status thinking | 0.067 秒 |
| status querying | 1.083 秒 |
| tool_start get_my_schedules | 1.084 秒 |
| tool_end ok=true | 1.177 秒 |
| 首 token | 2.678 秒 |
| 末 token | 6.618 秒 |
| done | 6.622 秒 |

共 **673 个原生 token 帧**，首 token 到 done 的高精度实测跨度 **3.943 秒**（表中到达时间已舍入至毫秒），不再同一毫秒批量到达。工具事件全部早于 token。脚本尾部输出：

```text
[PASS] 真流式时间轴：首 token +2.678s；末 token +6.618s；done +6.622s；首 token→done 3.943s；共 673 个 token 帧
[PASS] 临时学习计划已通过本人 DELETE 接口删除并回查
[PASS] 临时 3 条课表及 1 门课程已删除并通过回查
[PASS] 脚本启动的 MCP 已关闭，8001 已释放
[PASS] P3 学习计划、越权/重复保存及真流式时间轴全部通过（退出码 0）
```

测试数据已经删除。日常使用聊天页时，请运行 MCP；新的正常对话保存的学习计划会保留在本人列表中。
