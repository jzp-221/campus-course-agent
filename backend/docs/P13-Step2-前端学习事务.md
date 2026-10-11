# P13-Step2：作业考试、课程详情、计划任务与站内提醒

本阶段在 P13-Step1 已有数据层之上补齐前端。作业与考试、可勾选任务及原文本学习计划分别使用各自的数据表与接口，文本计划继续保留原新增、查看、编辑、删除及助手保存路径。

验收完成：后端448/448、前端137/137、生产构建与真实HTTP验收均通过。真实截图与数据指纹见本文末尾。

## 页面入口与路由

| 页面/区块 | 路由 | 入口与行为 |
| --- | --- | --- |
| 作业与考试 | `/manager/assignment` | 侧栏“作业与考试”，位于学习计划之后、管理员课程管理之前；学生显示本人未完成数量角标。没有学生选择器。 |
| 课程详情 | `/manager/course/:id` | 从周课表卡片、本人课表列表课程名、课程管理列表进入；不另设侧栏菜单。 |
| 文本计划与计划任务 | `/manager/study-plan` | 保留“学习计划”入口，上方原文本学习计划，下方独立“计划任务”区块。 |
| 首页近期截止/站内提醒 | `/manager/home` | 保留今日课程、明日预告、结课提醒、学期进度、快捷入口、管理员概览与知识库告警；学生额外展示逾期提醒与近期截止。 |

作业列表支持类型（作业/考试）、状态（未完成/已完成）、到期范围（全部/3 天内/7 天内/已逾期）和课程详情带入的 `course_id`。列表先分页获取当前账号全部记录，再在前端筛选、排序与分页，避免只筛当前一页遗漏事项；现有接口没有“已逾期”过滤项，因此没有修改旧列表契约。考试标签为醒目色，逾期未完成为红色，3 天内为橙色，已完成行弱化显示。

课程详情聚合课程信息、本人上课安排、来源明确的资料片段和本人作业。管理员详情明确显示“管理员视角不展示学生作业”，其上课安排也仅属于管理员本账号，不沿用周课表管理员查看学生的权限。

计划任务支持新增、编辑、删除、勾选完成/取消完成，以及默认 1 天、可选 1～30 天的延期；显示“已延期 N 次”，未设截止时显示“未设截止”。延期后的时间与计数使用后端真实返回，不由前端擅自递增。

## 运行与查看

沿用现有三项服务：FastAPI 8000、MCP 8001、Vite 5173。课程资料区需要MCP；其它新页面通过FastAPI读取与管理本人数据。

```bash
# backend，确认8000空闲后启动；已有服务直接复用
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

```bash
# backend，确认8001空闲后启动
.venv/bin/python mcp_server.py
```

```bash
# frontend：使用已有Vite /api代理；这是进程环境参数，不修改.env
VITE_API_BASE_URL='' npm run dev -- --host localhost --port 5173 --strictPort
```

访问 `http://localhost:5173`，登录学生后从侧栏进入作业与考试、学习计划和周课表。课程详情入口支持鼠标点击、Enter与空格键；首页逾期链接带 `?due=overdue`，课程详情管理链接带 `?course_id=N`，两种过滤可以同时生效。

## 为什么新增三个只读接口

原课程列表是管理员接口，学生无法据此填课程下拉或课程详情；原分页课表只提供条目，缺少面向任意截止日期的完整假期与学期配置；课程资料的查询能力在 MCP 工具中，不能让浏览器直接持有工具客户端或绕过 JWT。因此新增同一个 `learning-context` 只读路由，统一提供学生可读的课程、配置日历、本人课程安排以及经过当前凭证的 MCP 检索。

三个接口全部使用 `get_current_user` 验证 JWT，只提供 GET，不接受可控的 `user_id` 或任意检索 `query`，不写课程、课表、计划或文档。已有作业/考试与计划任务的 9 个写读接口及相应业务服务没有改动。

### 1. `GET /api/learning-context`

所有已登录用户可读。响应 `data`：

```json
{
  "courses": [{"id": 2, "name": "数据结构", "code": "DS101", "description": "课程简介", "status": 1}],
  "semester": {
    "start_date": "2026-09-07", "end_date": "2027-01-24", "total_weeks": 20,
    "current_week": 5, "today": "2026-10-11"
  },
  "holidays": [{"name": "元旦", "start_date": "2027-01-01", "end_date": "2027-01-03", "note": "2027 年正式安排尚未发布，按惯例推定"}],
  "makeup_workdays": ["2026-09-20", "2026-10-10"]
}
```

这是字段契约示例，真实响应包含所有现有课程及配置中的全部假期。学期外 `current_week` 为 `null`；学期首尾由现有 `week_date_range` 与配置计算，不另行复制学期规则。

### 2. `GET /api/learning-context/courses/{id}`

课程不存在返回统一业务码 `404`、“课程不存在”。响应 `data`：

```json
{
  "course": {"id": 2, "name": "数据结构", "code": "DS101", "description": "课程简介", "status": 1},
  "semester": {"start_date": "2026-09-07", "end_date": "2027-01-24", "total_weeks": 20, "current_week": 5, "today": "2026-10-11"},
  "schedules": [{"id": 100, "class_date": "2026-10-14", "weekday": 3, "period": "7-8节", "teacher": "李老师", "location": "A101", "start_week": 3, "end_week": 8}],
  "start_week": 3,
  "end_week": 8
}
```

示例中的课表仅说明字段，不代替真实数据证据。实际按 `course_id` 和当前 JWT 的 `user_id` 同时过滤；多条本人安排的整体范围是最小 `start_week` 至最大 `end_week`。没有本人安排时 `schedules=[]`、`start_week=null`、`end_week=null`。

### 3. `GET /api/learning-context/courses/{id}/docs`

先确认课程存在，再以该课程真实名称构造 `{"query":"数据结构"}`，每次请求重新获取 MCP 工具，转发本次完整 Authorization，经 Streamable-HTTP 调用 `search_course_docs`。接口不直连 `rag_service`，不跨用户缓存工具，不允许浏览器指定身份或任意查询。

成功响应 `data` 保留原工具契约：

```json
{
  "items": [{"text": "来源原文片段", "source": "数据结构实验教学规定.md", "document_id": 17, "chunk_index": 0, "distance": 0.2}],
  "count": 1
}
```

正常无命中为 `code=200`、`data={"items":[],"count":0}`，页面显示“知识库中暂无该课程资料”。未配置 embedding Key 返回业务码 `503` 及“当前无法查询资料（检索失败：未配置 EMBEDDING_API_KEY）”；MCP 发现/调用失败、缺少或重复搜索工具、无来源/错误格式返回 `502` 与明确“检索失败”；MCP 已有业务错误保留其业务码及失败语义。页面使用独立错误态，失败不会显示成“暂无资料”。

所有接口沿用 `Response` 包装与既有业务异常处理：成功 `{"code":200,"message":"请求成功","data":...}`；业务失败 `data=null`。未登录/伪造 JWT 在读取及检索之前 HTTP `401` 拒绝。

## 本地时间与假期提醒

`due_at` 沿用无时区后缀的本地 ISO 字符串。日期控件使用 `YYYY-MM-DDTHH:mm:ss`，显示与校验按照年月日时分秒处理，不追加 `Z`，不调用 `toISOString()` 转为 UTC。剩余天数按本地日历日期差计算，支持当天、跨月与逾期；是否已经过期仍比较具体截止时刻。3/7 天内筛选使用当前时刻向后 3/7 天的范围。

假期方案选择新的只读学期上下文，而不是拼接多周课表响应：截止时间可以超出当前周，首页、作业弹窗与任务都需要同一份完整日期配置。`holidays` 与 `makeup_workdays` 仅从后端 `app/common/holiday_config.py` 读取；前端代码没有写死任何假期或调休日期。

前端拿到服务器配置后按以下优先级判定截止日：

1. 先匹配服务器法定假期区间，显示“该截止日在放假期间（元旦 1/1-1/3）”。假期优先，不能误标成普通周末。
2. 未命中假期但命中服务器调休日时，显示“调休上班日，截止日照常”。
3. 其余普通周六/周日显示“周末不放假，截止日照常”。
4. 普通工作日没有额外日历警告。

以上文案均已在真实界面及服务器日历取数后核对；记录见下文。日历加载失败保留明确错误，不使用硬编码假期兜底，也不把未知日期冒充正常工作日。

提醒仅在进入系统/加载页面时计算与展示，没有后台推送、浏览器通知、定时任务或通知授权。首页文案明确“进入系统时提醒 · 近 3 天到期，不提供后台推送”。未完成角标只在学生视角显示，管理员首页不展示学生提醒。切换账号时清空共享待办状态，旧账号未结束的请求不能覆盖新账号结果。

## 演示数据状态与幂等性

保留原有 15 条 `assignments`、5 条 `plan_tasks`、每条固定演示标记及关联课程。版本为 `P13-v2`，相比 Step1 只调整 aaa 的三条既有作业：

| 演示标记 | 新状态 | 截止日期 | 用途 |
| --- | --- | --- | --- |
| `aaa-a01` | `done` | 基准日期前 2 天 | 已完成灰化演示 |
| `aaa-a02` | `pending` | 基准日期前 1 天 | 已逾期高亮演示 |
| `aaa-a03` | `pending` | 基准日期后 2 天 | 3 天内到期演示 |

bbb 的 3 条记录、元旦假期待办与 5 项计划任务不变。相同本地基准日期下，纯离线内存库模拟旧版迁移得到：

```text
纯离线内存核对，未访问真实MySQL：
数量：assignments=15 / plan_tasks=5
旧版→新版变化： [('[P13演示:aaa-a01]', ['due_at', 'status']), ('[P13演示:aaa-a02]', ['due_at']), ('[P13演示:aaa-a03]', ['due_at'])]
首次： {'created': 20, 'updated': 0, 'unchanged': 0}
修正： {'created': 0, 'updated': 3, 'unchanged': 17}
重复： {'created': 0, 'updated': 0, 'unchanged': 20}
bbb、假期待办与5项plan_tasks：全部保持原数据
```

真实数据库执行（基准日2026-10-11）：首次 `created=0, updated=3, unchanged=17`，重复 `created=0, updated=0, unchanged=20`。更新仅为表中三项；最终15条作业（aaa12/bbb3）、5条任务保持，任务逐字段未变化。更新演示状态无需 `--clean`；不删除重建任何课表、课程、资料或文本学习计划。脚本仍允许专属标记清理，但本阶段更新不需要执行它。

## 离线只读桥接测试

```bash
cd /Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend
.venv/bin/python -m unittest tests.test_learning_context tests.test_demo_learning_seed
```

已执行结果：`Ran 31 tests ... OK`，其中新增只读桥接 23 项、原演示种子 8 项。测试初始化禁用 `.env` 读取，使用内存 SQLite、测试 JWT 与假 MCP 运输，不依赖真实 Key、网络或业务数据库。新增用例：

```text
test_missing_token_rejects_all_readonly_routes_before_mcp
test_forged_token_is_rejected_before_mcp
test_context_exposes_existing_courses_and_semester_dates
test_holidays_and_makeup_days_read_exact_backend_configuration
test_semester_outside_range_reports_no_current_week
test_course_detail_contains_only_own_schedule_and_ignores_user_id
test_other_student_receives_own_schedule
test_admin_is_also_restricted_to_own_course_schedules
test_course_without_own_schedule_returns_empty_and_null_range
test_missing_course_is_404_on_detail_and_docs_without_mcp
test_context_and_detail_do_not_change_database_rows
test_docs_pass_only_course_name_and_current_authorization
test_docs_each_request_reloads_tools_with_its_own_jwt
test_docs_discovery_uses_streamable_http_with_current_authorization
test_docs_normal_empty_result_is_success_not_failure
test_missing_embedding_key_fails_before_mcp
test_mcp_discovery_failure_returns_safe_502
test_missing_or_duplicate_search_tool_returns_502
test_tool_runtime_failure_is_not_empty_and_hides_internal_detail
test_tool_error_preserves_existing_business_code_and_failure_wording
test_source_less_or_malformed_tool_result_is_rejected
test_readonly_database_failure_uses_existing_safe_business_envelope
test_openapi_is_readonly_and_has_no_user_id_or_query_input
```

## 完整回归与真实验收证据

| 项目 | 结果/证据 |
| --- | --- |
| 全量后端 | **448/448 PASS**（原425＋23个只读桥接测试） · [日志](P13-Step2-验收记录/backend-tests.log) |
| 全量前端 | **137/137 PASS**（原81＋56） · [全部新增用例名与结果](P13-Step2-验收记录/新增前端测试.md) · [日志](P13-Step2-验收记录/frontend-tests.log) |
| 生产构建 | **EXIT=0** · [日志](P13-Step2-验收记录/build.log)；仍有原Element Plus主包大于500KB的提示，没有新增依赖 |
| 真实HTTP验收 | **EXIT=0** · [完整输出](P13-Step2-验收记录/real-api-smoke.txt) · [精简JSON](P13-Step2-验收记录/真实接口.json) |
| 受保护资产 | **PASS** · [核对记录](P13-Step2-验收记录/protected-assets.txt) |

真实验收脚本（前端目录）：

```bash
node tests/manual/learning_ui_smoke.mjs
```

需要既有FastAPI 8000与MCP 8001运行；脚本复用服务、不启停服务。用真实登录为aaa、bbb、admin分别核对本人列表，创建带UUID的新测试记录后只修改与清理这些新ID；超时或中断也在finally回收。作业、任务及文本计划前后全量JSON逐字段相等，失败或清理未完成退出非0。不打印凭证或Key。

本轮真实操作已验证：

- 临时作业新增 → 修改标题并完成 → 取消完成 → 类型/状态/课程组合筛选 → 删除；bbb改/删该aaa临时记录均返回404。
- 临时任务新增 → 不带body默认延期1天（次数1）→ 再延期2天（次数2）→ 清空课程及截止、标记完成 → 删除；bbb改/删/延期均返回404。
- aaa与bbb的课程详情安排ID分别等于各自列表接口结果，额外传别人的user_id没有影响；管理员课程详情不返回学生安排。前端管理员详情不发作业列表请求且提示“管理员视角不展示学生作业”。
- 课程id2由真实FastAPI → MCP Streamable-HTTP → search_course_docs检索，返回1个片段，来源为《数据结构实验教学规定.md》。浏览器从第6周“数据结构”卡片点入详情成功。
- 真实界面：aaa共12项（未完成11），逾期1、3天内1、完成1、假期截止1。首页显示逾期提醒与近期截止；管理员不计算/展示学生提醒。

实际提示文本：

```text
普通周末（2026-10-17或10-18）：周末不放假，截止日照常
法定假期（2027-01-01）：该截止日在放假期间（元旦 1/1-1/3）
调休上班日（2026-10-10）：调休上班日，截止日照常
首页：进入系统时提醒 · 近 3 天到期，不提供后台推送
```

演示元旦安排仍保留后端“2027年正式安排尚未发布，按惯例推定”的note；本轮没有修改假期规则。

最终数据：`users3 | coruses22 | schedules314(aaa259/bbb55) | documents4(17/18/19/20各7块，共28向量) | assignments15(aaa12/bbb3) | plan_tasks5 | study_plans0 | RAG阈值0.40`。临时记录全部清理；既有5项任务和文本计划逐字段未改变；.env及RAG/助手业务源文件指纹不变。未修改9个业务接口、表结构、314条课表、4份资料、Chat会话记录或Document功能。

## 四张真实界面截图

均为实际浏览器页面，截图时间使用Asia/Shanghai；数据版本P13-v2，基准日2026-10-11。

| 文件 | 截图时间（上海） | 账号/版本 | 状态 |
| --- | --- | --- | --- |
| [P13-assignment-management.jpg](screenshots/P13-assignment-management.jpg) | 2026-10-11 16:14:37 | aaa / P13-v2 | 12条本人事项：完成、逾期、近期、元旦截止 |
| P13-course-detail.jpg（保留本地，含课程原文片段） | 2026-10-11 16:15:36 | aaa / P13-v2 | 数据结构(id=2)，本人19条安排，资料来源及1项未完成 |
| [P13-plan-tasks.jpg](screenshots/P13-plan-tasks.jpg) | 2026-10-11 16:16:03 | aaa / P13-v2 | 原5项任务，延期2次、已完成、未设截止并列 |
| [P13-home-deadlines.jpg](screenshots/P13-home-deadlines.jpg) | 2026-10-11 16:16:52 | aaa / P13-v2 | 学期第5周，1项逾期、1项近期，角标11 |

原始时间元数据：[P13-screenshot-metadata.json](screenshots/P13-screenshot-metadata.json)。截图没有模拟UI或替换服务器数据。
