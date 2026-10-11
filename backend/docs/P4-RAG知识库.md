# P4：文档入库与课程资料检索

当前工具白名单为 `get_my_schedules`、`save_study_plan`、`search_course_docs`。管理员上传或重新入库时抽取、分块、向量化；学生提问时只向量化问题，通过真实 Streamable HTTP MCP 检索持久化片段，保留原有课表、保存与 SSE 行为。

## 配置与启动

在 backend 目录安装依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import chromadb,pypdf,docx,httpx; print("RAG dependencies OK")'
```

自行在本地 `backend/.env` 添加配置。真实 Key 仅放本地，不发送到聊天、不写入代码或 Git；本次实现没有修改 `.env`。

```ini
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=BAAI/bge-m3
CHROMA_DIR=
# 可选，取值 6–8；默认 8，不改变最终最多 3 条的限制。
RAG_CANDIDATE_COUNT=8
```

embedding 使用 `POST /embeddings`，以字符串数组传入 `input`，校验返回向量与顺序，不下载 Chroma 默认本地模型。格式见 [SiliconFlow 官方文档](https://api-docs.siliconflow.cn/docs/api/embeddings-post)。请求超时 30 秒；异常或无效向量明确失败。

MySQL 沿用 [第 07 节联调手册](第07节-联调手册.md) 中的现有实例。分别启动两个服务：

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd "/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend"
.venv/bin/python mcp_server.py
```

已有 8000 服务时复用它；更改 `.env` 后重启原服务，不能另开同端口实例。MCP 加载同一份配置。数据库启动只新增 `documents` 表，不调整其他表结构。

## 私有目录与两个进程共享

`CHROMA_DIR` 留空为 **backend/chroma_data**；相对路径固定按 backend 根目录解析，与进程工作目录无关。FastAPI 和 MCP 导入同一个绝对路径。原始文件保存在 **backend/private_documents**，抽取文本在 `documents.extracted_text`；原文目录不挂载到 `/uploads`。

默认两个目录已加入 `.gitignore`。自定义 Chroma 目录也补充忽略所有内容的规则。`.env`、API Key、原文与 Chroma 数据均不得提交。

使用 [Chroma PersistentClient](https://docs.trychroma.com/reference/python/client) 的磁盘持久化。每次操作在短命子进程中打开最新索引，共享目录的文件锁串行访问；父进程一直持锁至子进程完全退出，子进程继承锁 FD 保持锁的生命周期。FastAPI/MCP 不缓存长期 Chroma 客户端，无需新增常驻服务或端口。检索只允许数据库已提交且 `status=indexed` 的 ID，并在查询后再确认状态；整批尚未提交及重新入库中的片段不会暴露。

同一文档的整个重建/删除生命周期另有文档 ID 锁，包含 embedding、状态提交及失败补偿；等待后开启新 SQL 事务读取，避免旧快照和并发失败补偿误删刚成功的向量。锁繁忙时明确报错请稍后重试。

集合记录 embedding 服务和模型的指纹，配置不一致报检索失败。更换模型时使用独立 Chroma 目录并重新入库，不混用向量。

## 管理员接口

所有接口携带 `Authorization: Bearer <JWT>` 并检查管理员身份。业务响应沿用 `{code,message,data}`；判断 `code`，不能只看 HTTP 200。

| 方法与路径 | 请求 / 返回 data |
| --- | --- |
| `POST /api/documents/upload` | multipart 字段 `files` 可重复；返回本批文档数组 |
| `GET /api/documents?page=1&page_size=10` | `{list,total}`，每页最多 100 |
| `GET /api/documents/{id}/preview` | 文档元数据与 `extracted_text` |
| `POST /api/documents/{id}/reindex` | 重新读取原文入库，返回文档元数据 |
| `DELETE /api/documents/{id}` | 同步清理向量、原文及记录 |

元数据含 `id/filename/chunk_count/status/error_message/uploader_id/create_time/update_time`，不公开服务器路径。状态为 `processing/indexed/failed`。重建先提交 `processing`，成功后 `indexed`；失败保留 `failed`、原文和原因，方便重试。

每批最多 10 份，每份最多 100MB。支持 UTF-8 `.txt/.md`、PDF 文字层、DOCX 段落及表格。扫描 PDF 明确提示没有文字层、本阶段不支持 OCR；空白、损坏或加密文档不入库。现在以行首 `##` / `###` 标题切分，不把相邻章节揉进同一块；章节正文超过 500 字符时才使用 500 字符窗口、50 字符重叠，末块保留剩余内容。无标题文本沿用窗口切分。

存储和返回的每个片段带 `文档名 · 章节标题` 前缀，前缀不计入正文窗口。向量化使用原文章节内容：已有标题不重复叠加，长章节的后续窗口补上章节标题；文件名作为引用标签保留在片段与 `source` 元数据中，不参与正文向量化。这样既保留可解释的来源，也避免重复文件名/标题稀释短章节的主题。

整批成功才提交 SQL。任一文件失败，回滚数据库，并补偿删除本批文件及所有已尝试入库的文档向量，包括部分写入的向量；错误指出失败文件与原因。仅补偿确认完成时才说“整批已回滚”。三个存储没有共同事务；若存储故障使补偿本身失败，会列出未清理文档 ID，不宣称清理成功。故障恢复后须按 ID 重试向量清理；未提交记录对应残片不会被检索返回。单份删除向量失败时保留记录和原文，可再次 DELETE。

Apipost 使用管理员 Token，Body 选 form-data，以 `files` 添加文件；预览确认抽取文本后，以学生 Token 在聊天页提问。

## 检索与助手契约

工具固定 `search_course_docs(query)`，只接受 1–2000 字的 query，schema 拒绝额外参数。知识库全站共享，但必须先校验当次 Authorization；未登录或禁用用户不得检索。Agent 每请求重建 MCP 客户端，白名单外工具一律拒绝。

余弦距离门槛固定 **≤0.35**。Chroma 先读取 `RAG_CANDIDATE_COUNT` 条候选（默认 8，允许 6–8），经过来源、文档状态与距离校验后，仍最多返回三个最相似片段。必须包含原文及原始文件名，不返回无来源片段，不在未命中时放宽门槛。

```json
{"items":[{"text":"原文片段","source":"课程规定.txt","document_id":1,"chunk_index":0,"distance":0.12}],"count":1}
```

| 情况 | MCP 结果 | 助手表现 |
| --- | --- | --- |
| 命中 | `items/count`，含 `text/source` | 依据本轮原文回答并注明来源 |
| 正常未命中 | `{"items":[],"count":0}` | “知识库中没有检索到相关内容”，不编造 |
| 有片段但不含答案 | 正常检索结果，原文没有所问规定 | “检索到的资料未包含该问题的答案”，不得强行解读或编造 |
| Key 缺失 | `{"error":"检索失败：未配置 EMBEDDING_API_KEY","code":503}` | “当前无法查询资料”，不能解释成没有资料 |
| embedding / Chroma 异常 | `error/code`，不返回空 items | 停止本轮，业务错误或 SSE `error` 明确无法查询资料 |
| 无有效 JWT | `error/code=401` | 身份检查失败，不触达 embedding |

资料问题先检索；检索片段只是参考数据，片段中的指令不能改变规则或授权保存。检索失败在再次调用模型前被 Guard 拦截，避免生成“没有资料”的假回答。

SSE 仍为六种事件，新增固定状态键 `retrieving`：

```text
status {"stage":"thinking"}
status {"stage":"retrieving"}
tool_start {"name":"search_course_docs","args":{"query":"问题"}}
tool_end {"name":"search_course_docs","ok":true}
token {"text":"增量文字"} ...
done {"answer":"完整回答","tool_calls":[...]}
```

已开始工具失败先发 `tool_end(ok=false)`，再 `error {code,message}` 终止，没有 done。聊天页沿用 fetch、ReadableStream、50ms 批量渲染和 Markdown 净化；过程区展示“检索课程资料”。history 仍只含问答文字。

## 验证命令

离线测试不依赖网络、真实 Key 或 MySQL，实际 Chroma 使用临时目录和确定性测试向量：

```bash
.venv/bin/python -m unittest tests.test_mcp_client tests.test_mcp_schedules tests.test_study_plans tests.test_agent tests.test_agent_stream tests.test_mcp_study_plan_tool tests.test_agent_study_plans tests.test_agent_stream_progressive tests.test_document_text tests.test_documents tests.test_agent_rag tests.test_rag
```

覆盖抽取、分块、权限、部分向量/commit 失败的整批补偿、重建/删除并发、Key/服务失败与正常空结果区分、来源/距离校验、跨进程 Chroma 持久化与并发写入、Agent/SSE。

前端：

```bash
cd ../frontend
node --test tests/*.test.mjs
npm run build
```

真实验证需要已有 FastAPI/MySQL、两种 API Key、8001 空闲，按顺序在 backend 执行：

```bash
.venv/bin/python tests/manual/e2e_mcp.py
.venv/bin/python tests/manual/agent_smoke.py
.venv/bin/python tests/manual/p3_smoke.py
.venv/bin/python tests/manual/rag_smoke.py
# 无 embedding Key 也可单独验证失败路径：
.venv/bin/python tests/manual/rag_smoke.py --missing-key-only
```

RAG 脚本验证先启动 MCP 后上传明确规定，立即命中“五个工作日”原文和来源；真实 FastAPI Agent 引用规定/文件名；不相关主题明确未命中；无 JWT 拒绝；重启 MCP 后仍命中；删除后空且对应向量为 0；正常 TXT+扫描 PDF 整批失败，记录/文件/向量基线恢复；独立 MCP 用环境变量覆盖为空 Key，助手明确无法查询资料。

脚本退出非零表示失败。只启停自己的 MCP，不启动 8000；唯一标记的临时记录在 finally 清理，不删除既有资料。共享库若恰好命中无关问题，会明确失败提示使用隔离测试资料，不降低门槛。完整脚本预期末尾：

```text
[PASS] valid TXT + 扫描 PDF 整批失败：文件名和原因明确，SQL/磁盘/向量均无残留
[PASS] 缺 Key 的真实 MCP 明确检索失败；助手明确无法查询资料，并非没有相关内容
[PASS] 本次临时文档及对应向量、原文均已清理；未删除既有资料
[PASS] P4 RAG 真实冒烟全部通过（退出码 0）
```

## 本次实测

2026-10-10 在本机现有 MySQL / FastAPI 上完成，原有 107 项后端与 12 项前端测试全部保留通过：

| 验证 | 实测结果 |
| --- | --- |
| 后端离线回归 | **183 项通过**（107 原有 + 16 抽取 + 21 文档事务/并发 + 17 Agent RAG + 22 embedding/Chroma） |
| 前端离线回归 | **15 项通过**（12 原有 + 3 检索契约） |
| 生产构建 / pip check | 成功；依赖无冲突 |
| `e2e_mcp.py` / `agent_smoke.py` | 均退出 0，临时课表与课程清理完成 |
| `p3_smoke.py` | 退出 0；规划不保存、明确保存、身份/重复保存防护均通过 |
| `rag_smoke.py` | **退出 0**，真实 SiliconFlow embedding / Chroma / MCP / DeepSeek 全链路通过 |

RAG 真实文档为合成的“实训课程缺勤三次，须在五个工作日内提交补课申请”规则。先启动 MCP，再通过管理员上传接口入库；检索立即命中该规定，助手回答：

```text
实训课程缺勤达到三次时，必须在五个工作日内提交补课申请，导师批准后安排补课；逾期未申请者不安排补课。
来源文件：P4DOC-ba0b4fd713704d27a6fcdb067aea8b89-实训课程规则.txt
```

不相关的南极火山/鲸鱼问题回答“知识库中没有检索到相关内容。”；未登录拒绝；MCP 重启后仍命中。删除本次文档后检索为空、对应 Chroma 分块数为 0。正常 TXT 先实际入库、扫描 PDF 随后失败的批次验证通过，数据库 ID 集合、私有目录文件集合及向量总数恢复至测试前基线。

缺 Key 的独立 MCP 通过进程环境变量显式覆盖为空，没有修改 `.env`。真实 FastAPI 助手返回业务错误“当前无法查询资料（检索失败：未配置 EMBEDDING_API_KEY）”，没有说“没有相关内容”。所有本次临时文档、原文、向量、计划、课程与课表已清理，所有脚本创建的 MCP 进程均已关闭。

P3 真流式再次实测 **510 个原生 token 帧**：首 token +1.975 秒、末 token +4.823 秒、done +4.827 秒，首 token 到 done **2.852 秒**，工具事件均在 token 前。没有同一时刻批量伪流式，也没有人为延时。

## P7：标题分块与召回标定（2026-10-10）

原先四份演示资料合计 11 块，八个探针只有 5/8 正常：数学迟交与计算器规定的答案被 0.35 阈值挡住，英语作文手写要求未进入前三名。重新按标题分块后，四份资料各 7 块，合计 **28 块**。

一次中间测量发现，文件名与重复标题同时参与 embedding 时，英语作文答案仍约为 0.359。保留来源前缀用于存储/引用、用原文章节正文向量化后解决了这一问题。没有修改演示原文，也没有提高准入阈值。

对四份实际入库资料使用真实 `BAAI/bge-m3` embedding，生产检索与八条原始候选分开验证，实测如下。答案距离指正确来源中、包含实际规定的最近答案块；原始候选仅用于标定，不能冒充正常检索命中。

| 探针问题 | 正确答案块距离 | 验收 |
| --- | ---: | --- |
| 实验课前需要预习吗？ | 0.268306 | 正确来源，必须完成预习报告 |
| 实验报告最晚什么时候交？ | 0.272584 | 正确来源，第 3 天 24:00 前 |
| 数学作业迟交扣几分？ | 0.319413 | 正确来源，扣 2 分 |
| 考试可以带计算器吗？ | 0.267730 | 正确来源，不允许任何形式的计算器 |
| 期中考试占多少分？ | 0.337759 | 正确来源，20% |
| 英语作文可以交打印稿吗？ | 0.331661 | 正确来源，手写且不接受打印稿 |
| 请假超过三天由谁审批？ | 0.312624 | 正确来源，教务处审批 |
| 学校食堂几点开门？ | 最近候选 0.416237 | 正常检索无命中 |

七个有答案问题的答案块距离最大值 **0.337759**，食堂问题最近候选距离 **0.416237**；`0.337759 < 0.35 < 0.416237`。全部答案已在原门槛内，无关问题仍在门槛外，因此 **保留 0.35**，不进行放宽。本表为本次真实服务的测量值，重新运行时末尾小数可能略有变化。

### 现有文档重新入库

复用已有 FastAPI 8000，用管理员通过 `POST /api/auth/login` 登录，在 Apipost 的认证栏填写返回的 Bearer Token，然后依次发送以下请求，无需请求体：

```text
POST http://127.0.0.1:8000/api/documents/17/reindex
POST http://127.0.0.1:8000/api/documents/18/reindex
POST http://127.0.0.1:8000/api/documents/19/reindex
POST http://127.0.0.1:8000/api/documents/20/reindex
```

检查业务 `code=200`，各项 `status=indexed`、`chunk_count=7`。重新入库从 `private_documents/` 原始文件重新抽取与分块，替换同文档旧向量；不要重新上传或删除演示资产。已有 MCP 无需重启即可读取更新，后续新 MCP 进程也会读取同一持久化目录。

### 只读诊断与 SSE 三问

在 backend 运行：

```bash
.venv/bin/python tests/manual/rag_quality_diagnose.py
# 展示八条原始候选，额外保存不含正文的距离报告：
.venv/bin/python tests/manual/rag_quality_diagnose.py --top 8 --dump
```

诊断只读数据库与向量库，使用已配置的 embedding API；不调整阈值，不重新入库，不修改课程/课表。正常检索最多返回三条，原始八候选另行打印。只有七个问题同时命中答案关键词、实际规定及正确来源，且食堂无命中，才输出 `诊断验收：8/8 通过` 并退出 0；否则退出非零。`--dump` 在 backend 生成 `rag_quality_report.json`，只含距离、来源、ID 与判定，没有文档正文或 Key。

真实 Agent 三问走 `POST /api/agent/chat/stream`，用已有演示文档，不造资料：

```bash
# 8000 / MySQL 已运行，8001 必须空闲；脚本自行启停自己的 MCP。
.venv/bin/python tests/manual/p7_quality_smoke.py
```

提问依次为“实验课前需要预习吗？”、“考试可以带计算器吗？”、“英语作文可以交打印稿吗？”。脚本检查实际调用 `search_course_docs`、SSE 顺序/最终回答、正确来源及原文规定，并打印首 token / 末 token / done 时间。三份预期来源分别为 `数据结构实验教学规定.md`、`高等数学学习与考核说明.md`、`大学英语学习要求.md`。

### 演示课表种子用法

`tests/manual/seed_demo_schedules.py` 提供 23 门计算机专业及公共课程模板。P7 验收时演示库为 **23 门课程、77 条课表**；P8 第一阶段已按固定学期（2026-09-07 起，共 20 周）重造为 **508 条课表**，`aaa` / `bbb` 各 254 条。每门课固定星期和节次，按周次范围展开，跳过指定假期；周末专门课程仅在 9 月 20 日、10 月 10 日这两个调休日上课。完整策略与逐周核对表见 [P8-周课表第一阶段.md](P8-周课表第一阶段.md)。

```bash
.venv/bin/python tests/manual/seed_demo_schedules.py --help
.venv/bin/python tests/manual/seed_demo_schedules.py --preview
# 以下生成命令会同步演示数据，先执行 P8 字段迁移：
.venv/bin/python scripts/migrate_schedule_weeks.py
.venv/bin/python tests/manual/seed_demo_schedules.py
.venv/bin/python tests/manual/seed_demo_schedules.py --no-courses
```

`--preview` 只读打印固定排课和 20 周核对表；默认生成命令幂等同步两名演示学生的模板课表，保留匹配记录的 ID 与时间戳，不动其他学生或非模板课程。`--no-courses` 不补课程。旧 `--days` 参数仅兼容完整学期天数（当前为 140），不再按今天生成短期课表。`--clean` 先清理两名学生的模板课表，再删除模板课程；若还有其他课表引用，则以 409 拒绝并回滚。`--reseed` 先清理再生成。只读召回诊断仍使用 `tests/manual/rag_quality_diagnose.py`。

### 相对星期规则

`build_system_prompt()` 按 Asia/Shanghai 的今天注入本周与下周全部具体日期，并给出按未过期规则实际应查询的星期映射。2026-10-10 为周六，本周一到日为 10-05 至 10-11，下周三为 **2026-10-14**；问“我这周三有几门课？”应查 10-14，回答明确标注实际日期。本日和本周尚未到来的星期不顺延；明确指定历史日期或“已经过去的本周三”的查询保留原意。

新增离线测试验证周六映射、当天/未来星期、跨年、实际 Agent 系统消息注入与检索片段不含答案的规则。服务端仍不存会话，身份仍仅由当次 Authorization 决定。

真实 SSE 追加提问“我这周三有几门课？”，实际工具参数为 `get_my_schedules(date="2026-10-14")`；本人课表回查共 2 门，助手回答：

```text
这周三（10-14）你有 2 门课：
1. 数据库系统原理 — 1-2节 — 信息楼 机房402
2. 信息安全基础 — 3-4节 — 信息楼 机房301
```

### P7 回归结果

```bash
.venv/bin/python -m unittest $(ls tests/test_*.py | sed 's|/|.|g; s|\.py$||' | tr '\n' ' ')
.venv/bin/python tests/manual/e2e_mcp.py
.venv/bin/python tests/manual/agent_smoke.py
.venv/bin/python tests/manual/p3_smoke.py
.venv/bin/python tests/manual/sse_contract.py
.venv/bin/python tests/manual/sse_smoke.py
.venv/bin/python tests/manual/rag_smoke.py
.venv/bin/python tests/manual/p5_business_smoke.py
.venv/bin/python tests/manual/p7_quality_smoke.py
```

冒烟必须串行运行，运行前确认 8001 空闲；脚本只关闭自己启动的 MCP，不重复启动 FastAPI 8000。前端运行 `node --test tests/*.test.mjs` 与 `npm run build`。

| 验证 | 本次实测结果 |
| --- | --- |
| 后端离线回归 | **247 项通过**，包含原有 229 项及新增 18 项 |
| 前端离线回归 / 生产构建 | **22 项通过 / 构建成功** |
| 原有七个冒烟脚本 | 全部 **EXIT=0** |
| `rag_quality_diagnose.py` | **8/8、EXIT=0**，阈值保持 0.35 |
| `p7_quality_smoke.py` | **EXIT=0**，三问引用正确来源且与原文一致 |
| 未配置 embedding Key | 真实独立 MCP 明确检索失败，助手返回“当前无法查询资料”；没有退化为“没有相关内容” |
| 相对星期真实 SSE | 周六问“这周三”，查询 **2026-10-14**，回答本人 2 门课并注明日期 |

三问的首 token / done 时间分别为：预习 **+3.437s / +3.839s**，计算器 **+3.348s / +3.559s**，英语打印稿 **+3.417s / +3.506s**；均在工具结束后增量输出。P3 长回答再次验证 578 个 token 帧，首 token **+5.263s**、done **+8.281s**，间隔 **3.018s**，未改为批量伪流式。

回归结束后按原行数据摘要核对：`users=3 / coruses=23 / schedules=77 / documents=4 / study_plans=0`。课程、课表及其他业务原行未变，演示文档 17–20 的 ID、文件名、上传人、原文件字节和抽取原文均保留，只更新入库状态时间与分块/向量。`.env` 文件字节未变；所有脚本自己的 MCP 均已退出，8001 已释放。
