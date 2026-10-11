# P13-Step1：作业考试与学习计划任务

本轮只交付后端数据层、接口、关联删除保护和演示数据；没有新增前端页面。
作业考试与任务都按当前 JWT 用户隔离，管理员也只能操作本人记录。
`study_plans` 原有文本计划、助手保存工具及单轮保存限制均不修改。

## 迁移

在 backend 执行：

```sh
.venv/bin/python scripts/migrate_assignments_and_tasks.py --check
.venv/bin/python scripts/migrate_assignments_and_tasks.py
.venv/bin/python scripts/migrate_assignments_and_tasks.py --check
```

只创建 `assignments`、`plan_tasks` 和两张表的 `user_id` 索引，保留外键。
新表已存在时不重建、不清数据；缺少用户索引可补建；不兼容的已有字段集合会明确报错。
MySQL DDL 自动提交，因此迁移支持中断后重跑，不声称建两张表是一个可回滚的 DDL 事务。
两张表的 `create_time/update_time` 均 NOT NULL、没有数据库默认值，由 ORM 填上海本地时间。
`status` 有数据库默认值 `pending`，`plan_tasks.defer_count` 默认 0。

注意现有 FastAPI 启动时仍调用 `Base.metadata.create_all`：热重载导入新模型后，也可能先创建这两张新表。
本次真实 `--check` 初查两表不存在，接入后 FastAPI 热重载先建表，随后独立迁移输出“无（已完成迁移）”。
新增表的独立创建、部分迁移续跑、重复执行与旧表记录保留均有离线迁移测试验证；没有为了演示而删除新表。

## 接口契约

所有接口需要 `Authorization: Bearer <JWT>`，使用现有 `Response`。
成功和 BusinessException 沿用项目约定：HTTP 200，正文 `code` 为 200/400/404/409/503；未登录是 HTTP 401。
分页返回 `data: {list: [...], total: N}`。`page` 默认 1，`page_size` 默认 10，范围 1～100。
调用方传入 `user_id` 不参与归属选择；记录的用户只由 JWT 决定。

| 方法 | 路径 | 参数与行为 |
| --- | --- | --- |
| GET | `/api/assignments` | query：page/page_size/status/type/course_id/days |
| POST | `/api/assignments` | body：course_id、type、title、due_at 必填；status 默认 pending；note 可空 |
| PUT | `/api/assignments/{id}` | body：上述字段任选；省略表示不修改；note=null 可清空 |
| DELETE | `/api/assignments/{id}` | 只删除本人记录 |
| GET | `/api/plan-tasks` | query：page/page_size/status |
| POST | `/api/plan-tasks` | body：title 必填；course_id/due_at 可空；status 默认 pending |
| PUT | `/api/plan-tasks/{id}` | body：title/course_id/due_at/status 任选；course_id/due_at=null 可清空 |
| POST | `/api/plan-tasks/{id}/defer` | body 可省略或 `{}`；`{"days":1}` 默认1，范围1～30 |
| DELETE | `/api/plan-tasks/{id}` | 只删除本人记录 |

作业考试按 `due_at ASC, id ASC` 排序；计划任务按 id 倒序。
`days=N` 精确定义为上海本地“现在”到“现在+N天”（含边界），不包含已逾期记录；不会隐式只取 pending。
若需要只看未完成，额外传 `status=pending`。

作业创建示例：

```json
{"course_id":2,"type":"assignment","title":"第3章课后习题","due_at":"2026-10-12T18:00:00","note":"完成教材练习"}
```

返回示例（id 与时间戳以实际请求为准）：

```json
{"code":200,"message":"请求成功","data":{"id":1,"user_id":2,"course_id":2,"course_name":"数据结构","type":"assignment","title":"第3章课后习题","due_at":"2026-10-12T18:00:00","status":"pending","note":"完成教材练习","create_time":"2026-10-11T12:00:00","update_time":"2026-10-11T12:00:00"}}
```

计划任务示例：

```json
{"code":200,"message":"请求成功","data":{"id":1,"user_id":2,"title":"复习栈与队列","status":"pending","course_id":2,"course_name":"数据结构","due_at":"2026-10-13T20:00:00","defer_count":1,"create_time":"2026-10-11T12:00:00","update_time":"2026-10-11T12:01:00"}}
```

这些任务接口不解析、不转换助手的文字计划，也不自动调用 `save_study_plan`。

## 校验、日期与延期

- title 去首尾空白后 1～200 字；type 仅 assignment/exam；status 仅 pending/done；非法返回400。
- 作业考试的 due_at 必填、必须是合法 ISO 日期时间；非法日期、纯日期、数字时间戳返回400。
- 时间按 Asia/Shanghai 本地墙上时间存储，不转 UTC。若输入带 ISO 时区后缀，仍保留所填年月日时分秒并去掉后缀，不做时区换算；前端建议发送不带后缀的本地时间。
- note 最多500字；课程不存在返回404；可选任务关联允许 null。
- 本人不存在记录与他人记录一律404，使用相同消息“作业/考试不存在”或“计划任务不存在”。
- defer 锁定本人记录，再执行 SQL `defer_count = defer_count + 1`，与新 due_at 一起提交；不会在 Python 中读旧计数再写回。
- 没有 due_at 的任务第一次延期从当前上海本地时间开始；已完成任务延期只改时间与计数，状态保持 done。
- 所有新服务入口（包括列表）使用 `@database_business_errors`：外键竞争409，数据错误400，数据库不可用503，回滚且不返回 SQL/连接信息。

## 删除关联保护

沿用 P5“禁止删除，提示先清理”的策略，不级联删除。

- 课程：只有课表关联时原提示保留；同时检查作业考试与任务，多种关联一起列出并返回409，例如“该课程已有 1 条课表安排、1 条作业/考试、1 条计划任务，请先删除或调整这些关联数据”。
- 用户：既有课表、文本计划、文档检查保留，另加作业/考试与计划任务计数，例如“该用户仍有关联数据：计划任务 1 条，请先清理后再删除”。
- 删除当前管理员账号的原403规则不变。
- 新增关联与父记录删除的竞争仍由父行锁、外键和统一数据库异常边界兜底，不能静默返回成功。

## 演示数据

使用独立脚本，不修改 `seed_demo_schedules.py`：

```sh
.venv/bin/python tests/manual/seed_demo_assignments.py --preview
.venv/bin/python tests/manual/seed_demo_assignments.py
.venv/bin/python tests/manual/seed_demo_assignments.py --clean
# 固定日期复核本轮清单：
.venv/bin/python tests/manual/seed_demo_assignments.py --today 2026-10-11
```

默认基准日期是执行当天的上海本地日期，近期截止分布在之后1～21天。
按用户确认的数量：aaa 8条近期作业+3场近期考试，额外1条2027-01-01假期待办；bbb 2条作业+1场考试。
aaa 5条任务，1条done、1条defer_count=2，包含不关联课程、无截止时间的可选字段示例。
元旦是用户批准的窗口外固定示例，2027安排推定说明沿用既有配置；脚本不会改变节假日表。

仅管理 aaa/bbb 专属演示标记：作业备注前缀 `[P13演示:`，任务标题前缀 `[P13演示]`。
请不要将这些保留标记用于真实作业/任务。重复运行复用记录 ID，未变化时不写更新时间；重新运行会恢复这批演示记录的初始状态并按新的基准日期更新截止时间。
清理只删除这两张表中上述标记记录，不删除任何课程、课表、学习计划、资料或无标记真实记录。
新增、同步与清理使用事务，失败全部回滚。

## 回归与真实验证

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python -m unittest tests.test_learning_items tests.test_learning_item_migration tests.test_demo_learning_seed -v
.venv/bin/python tests/manual/learning_items_smoke.py
cd ../frontend
node --test tests/*.test.mjs
npm run build
```

本轮：后端425项（原375项+新增50项）、前端81项通过，生产构建通过。
真实 HTTP/MySQL验证包括 CRUD、筛选分页、管理员与学生自有数据隔离、越权404、参数400、关联409、未登录401，以及8个并发延期请求。
并发后计数1→9且截止时间再推后8天，所有临时测试数据清理完毕。
没有启动重复FastAPI，没有调用模型或修改已有工具链路。

原有数据逐行校验与保护文件哈希校验通过：users3/coruses22/schedules314（aaa259、bbb55）/documents4、每份7块共28向量/study_plans0；.env与前端源码不变。
最终新增 assignments15（aaa12、bbb3）、plan_tasks5（aaa5）。

以下附本轮原始迁移、清单、用例与真实接口记录。

<!-- P13-EVIDENCE -->

## 迁移前 --check 原始输出

```text
assignments：不存在（待迁移）
plan_tasks：不存在（待迁移）
```

## 首次执行迁移与 SHOW CREATE TABLE 原始输出

```text
新增：无（已完成迁移）
assignments 表结构：
  id: INTEGER NOT NULL DEFAULT None
  user_id: INTEGER NOT NULL DEFAULT None
  course_id: INTEGER NOT NULL DEFAULT None
  type: VARCHAR(16) COLLATE "utf8mb4_unicode_ci" NOT NULL DEFAULT None
  title: VARCHAR(200) COLLATE "utf8mb4_unicode_ci" NOT NULL DEFAULT None
  due_at: DATETIME NOT NULL DEFAULT None
  status: VARCHAR(16) COLLATE "utf8mb4_unicode_ci" NOT NULL DEFAULT "'pending'"
  note: VARCHAR(500) COLLATE "utf8mb4_unicode_ci" NULL DEFAULT None
  create_time: DATETIME NOT NULL DEFAULT None
  update_time: DATETIME NOT NULL DEFAULT None
  索引：[{'name': 'course_id', 'column_names': ['course_id'], 'unique': False}, {'name': 'ix_assignments_user_id', 'column_names': ['user_id'], 'unique': False}]
CREATE TABLE `assignments` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` int NOT NULL,
  `course_id` int NOT NULL,
  `type` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `title` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL,
  `due_at` datetime NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `note` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `create_time` datetime NOT NULL,
  `update_time` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `course_id` (`course_id`),
  KEY `ix_assignments_user_id` (`user_id`),
  CONSTRAINT `assignments_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
  CONSTRAINT `assignments_ibfk_2` FOREIGN KEY (`course_id`) REFERENCES `coruses` (`id`),
  CONSTRAINT `ck_assignments_status` CHECK ((`status` in (_utf8mb4'pending',_utf8mb4'done'))),
  CONSTRAINT `ck_assignments_type` CHECK ((`type` in (_utf8mb4'assignment',_utf8mb4'exam')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='作业与考试'
plan_tasks 表结构：
  id: INTEGER NOT NULL DEFAULT None
  user_id: INTEGER NOT NULL DEFAULT None
  title: VARCHAR(200) COLLATE "utf8mb4_unicode_ci" NOT NULL DEFAULT None
  course_id: INTEGER NULL DEFAULT None
  due_at: DATETIME NULL DEFAULT None
  status: VARCHAR(16) COLLATE "utf8mb4_unicode_ci" NOT NULL DEFAULT "'pending'"
  defer_count: INTEGER NOT NULL DEFAULT "'0'"
  create_time: DATETIME NOT NULL DEFAULT None
  update_time: DATETIME NOT NULL DEFAULT None
  索引：[{'name': 'course_id', 'column_names': ['course_id'], 'unique': False}, {'name': 'ix_plan_tasks_user_id', 'column_names': ['user_id'], 'unique': False}]
CREATE TABLE `plan_tasks` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` int NOT NULL,
  `title` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL,
  `course_id` int DEFAULT NULL,
  `due_at` datetime DEFAULT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `defer_count` int NOT NULL DEFAULT '0',
  `create_time` datetime NOT NULL,
  `update_time` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `course_id` (`course_id`),
  KEY `ix_plan_tasks_user_id` (`user_id`),
  CONSTRAINT `plan_tasks_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
  CONSTRAINT `plan_tasks_ibfk_2` FOREIGN KEY (`course_id`) REFERENCES `coruses` (`id`),
  CONSTRAINT `ck_plan_tasks_defer_count` CHECK ((`defer_count` >= 0)),
  CONSTRAINT `ck_plan_tasks_status` CHECK ((`status` in (_utf8mb4'pending',_utf8mb4'done')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='学习计划任务（与文本计划并存）'
```

## 重复执行迁移的幂等结果

```text
新增：无（已完成迁移）
```

## 演示数据最终逐条清单

```text
写入结果：{'created': 20, 'updated': 0, 'unchanged': 0}
数据版本：P13-v1；本地基准日期：2026-10-11；近期数据覆盖今天起1～21天
元旦假期待办为单独批准的窗口外示例；只管理 aaa/bbb 的专属演示标记记录。

assignments 逐条清单：
aaa | 作业 | 数据结构 | 第3章课后习题 | 2026-10-12 18:00:00 | pending
aaa | 作业 | 高等数学 | 微积分习题集二 | 2026-10-14 18:00:00 | pending
aaa | 作业 | 大学英语 | Unit 3 阅读与作文 | 2026-10-16 18:00:00 | pending
aaa | 作业 | 计算机网络 | 网络协议分析报告 | 2026-10-18 18:00:00 | pending
aaa | 作业 | 数据库系统原理 | SQL 综合练习 | 2026-10-20 18:00:00 | pending
aaa | 作业 | 操作系统 | 进程调度实验报告 | 2026-10-23 18:00:00 | pending
aaa | 作业 | Web 应用开发 | 课程网页制作作业 | 2026-10-26 18:00:00 | pending
aaa | 作业 | 软件工程 | 需求分析报告 | 2026-11-01 18:00:00 | pending
aaa | 考试 | 数据结构 | 期中考试 | 2026-10-17 09:00:00 | pending
aaa | 考试 | 高等数学 | 阶段测验 | 2026-10-24 09:00:00 | pending
aaa | 考试 | 计算机网络 | 期中考试 | 2026-10-31 09:00:00 | pending
aaa | 作业 | 高等数学 | 元旦假期复习作业 | 2027-01-01 18:00:00 | pending
bbb | 作业 | 数据结构 | 栈与队列练习 | 2026-10-15 18:00:00 | pending
bbb | 作业 | 大学英语 | Unit 3 单词整理 | 2026-10-21 18:00:00 | pending
bbb | 考试 | 高等数学 | 阶段测验 | 2026-10-28 09:00:00 | pending

plan_tasks 逐条清单：
aaa | 计划任务 | 数据结构 | [P13演示]数据结构：复习栈与队列 | 2026-10-13 20:00:00 | pending | 延期 0 次
aaa | 计划任务 | 高等数学 | [P13演示]高等数学：整理导数与积分公式 | 2026-10-15 20:00:00 | pending | 延期 2 次
aaa | 计划任务 | 大学英语 | [P13演示]大学英语：完成 Unit 3 单词复习 | 2026-10-12 20:00:00 | done | 延期 0 次
aaa | 计划任务 | 计算机网络 | [P13演示]计算机网络：复习 TCP 与 UDP 的区别 | 2026-10-19 20:00:00 | pending | 延期 0 次
aaa | 计划任务 | 未关联课程 | [P13演示]整理本周学习笔记 | 无截止时间 | pending | 延期 0 次
aaa：作业 9 条（含1条假期待办），考试 3 场，计划任务 5 条
bbb：作业 2 条，考试 1 场，计划任务 0 条
```

## 第二次执行种子的幂等结果

```text
写入结果：{'created': 0, 'updated': 0, 'unchanged': 20}
```

## --clean 清理范围实测

```text
仅清理专属标记：{'assignments': 15, 'plan_tasks': 5}；未删除课程、课表、资料、文本计划或非演示记录
```

## 新增50项测试的用例名与结果

```text
test_all_new_endpoints_require_login (tests.test_learning_items.LearningItemsApiTests.test_all_new_endpoints_require_login) ... ok
test_assignment_defaults_owner_course_name_and_orm_timestamps (tests.test_learning_items.LearningItemsApiTests.test_assignment_defaults_owner_course_name_and_orm_timestamps) ... ok
test_assignment_missing_required_fields_returns_400 (tests.test_learning_items.LearningItemsApiTests.test_assignment_missing_required_fields_returns_400) ... ok
test_assignment_pagination_and_combined_filters (tests.test_learning_items.LearningItemsApiTests.test_assignment_pagination_and_combined_filters) ... ok
test_both_resources_trim_titles_and_enforce_200_character_boundary (tests.test_learning_items.LearningItemsApiTests.test_both_resources_trim_titles_and_enforce_200_character_boundary) ... ok
test_course_assignment_reference_returns_409_and_preserves_rows (tests.test_learning_items.LearningItemsApiTests.test_course_assignment_reference_returns_409_and_preserves_rows) ... ok
test_course_task_reference_returns_409_and_preserves_rows (tests.test_learning_items.LearningItemsApiTests.test_course_task_reference_returns_409_and_preserves_rows) ... ok
test_course_with_both_new_references_lists_both_blockers (tests.test_learning_items.LearningItemsApiTests.test_course_with_both_new_references_lists_both_blockers) ... ok
test_database_failure_for_all_new_mutations_is_sanitized_and_rolls_back (tests.test_learning_items.LearningItemsApiTests.test_database_failure_for_all_new_mutations_is_sanitized_and_rolls_back) ... 数据库操作失败（create_assignment）：OperationalError
数据库操作失败（update_assignment）：OperationalError
数据库操作失败（delete_assignment）：OperationalError
数据库操作失败（create_plan_task）：OperationalError
数据库操作失败（update_plan_task）：OperationalError
数据库操作失败（delete_plan_task）：OperationalError
数据库操作失败（defer_plan_task）：OperationalError
ok
test_days_filter_excludes_overdue_includes_exact_boundary (tests.test_learning_items.LearningItemsApiTests.test_days_filter_excludes_overdue_includes_exact_boundary) ... ok
test_defer_counter_uses_atomic_sql_expression_and_owner_where_clause (tests.test_learning_items.LearningItemsApiTests.test_defer_counter_uses_atomic_sql_expression_and_owner_where_clause) ... ok
test_defer_default_and_explicit_days_accumulate (tests.test_learning_items.LearningItemsApiTests.test_defer_default_and_explicit_days_accumulate) ... ok
test_defer_invalid_days_and_overflow_do_not_change_row (tests.test_learning_items.LearningItemsApiTests.test_defer_invalid_days_and_overflow_do_not_change_row) ... ok
test_defer_without_deadline_starts_from_local_now (tests.test_learning_items.LearningItemsApiTests.test_defer_without_deadline_starts_from_local_now) ... ok
test_due_at_is_not_converted_when_iso_offset_is_supplied (tests.test_learning_items.LearningItemsApiTests.test_due_at_is_not_converted_when_iso_offset_is_supplied) ... ok
test_every_role_lists_only_own_records_and_ignores_query_owner (tests.test_learning_items.LearningItemsApiTests.test_every_role_lists_only_own_records_and_ignores_query_owner) ... ok
test_fk_race_is_business_409_and_no_success (tests.test_learning_items.LearningItemsApiTests.test_fk_race_is_business_409_and_no_success) ... 数据库操作失败（delete_assignment）：IntegrityError
ok
test_invalid_assignment_type_is_400_on_create_and_update (tests.test_learning_items.LearningItemsApiTests.test_invalid_assignment_type_is_400_on_create_and_update) ... ok
test_invalid_course_id_and_overlong_note_are_400 (tests.test_learning_items.LearningItemsApiTests.test_invalid_course_id_and_overlong_note_are_400) ... ok
test_invalid_due_at_is_400_and_not_persisted (tests.test_learning_items.LearningItemsApiTests.test_invalid_due_at_is_400_and_not_persisted) ... ok
test_invalid_filters_and_pagination_are_business_400 (tests.test_learning_items.LearningItemsApiTests.test_invalid_filters_and_pagination_are_business_400) ... ok
test_invalid_status_is_400_for_both_create_and_update (tests.test_learning_items.LearningItemsApiTests.test_invalid_status_is_400_for_both_create_and_update) ... ok
test_new_dependencies_can_be_cleared_then_parent_deleted (tests.test_learning_items.LearningItemsApiTests.test_new_dependencies_can_be_cleared_then_parent_deleted) ... ok
test_nonexistent_course_returns_404_for_create_and_update (tests.test_learning_items.LearningItemsApiTests.test_nonexistent_course_returns_404_for_create_and_update) ... ok
test_other_student_and_admin_cannot_update_delete_or_defer (tests.test_learning_items.LearningItemsApiTests.test_other_student_and_admin_cannot_update_delete_or_defer) ... ok
test_own_record_can_be_completed_and_deleted (tests.test_learning_items.LearningItemsApiTests.test_own_record_can_be_completed_and_deleted) ... ok
test_partial_update_preserves_omitted_fields (tests.test_learning_items.LearningItemsApiTests.test_partial_update_preserves_omitted_fields) ... ok
test_task_null_clears_course_and_due_at_but_assignment_note_can_be_cleared (tests.test_learning_items.LearningItemsApiTests.test_task_null_clears_course_and_due_at_but_assignment_note_can_be_cleared) ... ok
test_task_optional_fields_defaults_and_counter_cannot_be_forged (tests.test_learning_items.LearningItemsApiTests.test_task_optional_fields_defaults_and_counter_cannot_be_forged) ... ok
test_task_pagination_status_filter (tests.test_learning_items.LearningItemsApiTests.test_task_pagination_status_filter) ... ok
test_text_study_plans_work_independently_of_tasks (tests.test_learning_items.LearningItemsApiTests.test_text_study_plans_work_independently_of_tasks) ... ok
test_user_assignment_reference_returns_409 (tests.test_learning_items.LearningItemsApiTests.test_user_assignment_reference_returns_409) ... ok
test_user_task_reference_returns_409 (tests.test_learning_items.LearningItemsApiTests.test_user_task_reference_returns_409) ... ok
test_check_does_not_create_tables (tests.test_learning_item_migration.LearningItemMigrationTests.test_check_does_not_create_tables) ... ok
test_creates_only_two_tables_and_user_indexes (tests.test_learning_item_migration.LearningItemMigrationTests.test_creates_only_two_tables_and_user_indexes) ... ok
test_database_defaults_and_foreign_keys_are_enforced (tests.test_learning_item_migration.LearningItemMigrationTests.test_database_defaults_and_foreign_keys_are_enforced) ... ok
test_existing_incompatible_table_fails_without_dropping_data (tests.test_learning_item_migration.LearningItemMigrationTests.test_existing_incompatible_table_fails_without_dropping_data) ... ok
test_missing_parent_fails_without_creating_new_tables (tests.test_learning_item_migration.LearningItemMigrationTests.test_missing_parent_fails_without_creating_new_tables) ... ok
test_missing_user_index_is_repaired_without_recreating_table (tests.test_learning_item_migration.LearningItemMigrationTests.test_missing_user_index_is_repaired_without_recreating_table) ... ok
test_partial_migration_resumes_missing_table (tests.test_learning_item_migration.LearningItemMigrationTests.test_partial_migration_resumes_missing_table) ... ok
test_second_run_is_noop_preserves_new_rows_and_legacy_schedule (tests.test_learning_item_migration.LearningItemMigrationTests.test_second_run_is_noop_preserves_new_rows_and_legacy_schedule) ... ok
test_timestamps_have_no_server_default_and_are_required (tests.test_learning_item_migration.LearningItemMigrationTests.test_timestamps_have_no_server_default_and_are_required) ... ok
test_clean_only_own_marker_preserves_non_demo_and_original_assets (tests.test_demo_learning_seed.DemoLearningSeedTests.test_clean_only_own_marker_preserves_non_demo_and_original_assets) ... ok
test_deadlines_are_relative_except_explicit_holiday_example (tests.test_demo_learning_seed.DemoLearningSeedTests.test_deadlines_are_relative_except_explicit_holiday_example) ... ok
test_demo_counts_and_sparse_student_are_distinct (tests.test_demo_learning_seed.DemoLearningSeedTests.test_demo_counts_and_sparse_student_are_distinct) ... ok
test_missing_real_course_or_student_is_rejected (tests.test_demo_learning_seed.DemoLearningSeedTests.test_missing_real_course_or_student_is_rejected) ... ok
test_one_done_task_and_one_twice_deferred_task (tests.test_demo_learning_seed.DemoLearningSeedTests.test_one_done_task_and_one_twice_deferred_task) ... ok
test_relative_deadlines_move_when_run_date_changes (tests.test_demo_learning_seed.DemoLearningSeedTests.test_relative_deadlines_move_when_run_date_changes) ... ok
test_repeat_seed_keeps_ids_and_does_not_duplicate (tests.test_demo_learning_seed.DemoLearningSeedTests.test_repeat_seed_keeps_ids_and_does_not_duplicate) ... ok
test_whole_seed_rolls_back_on_failure (tests.test_demo_learning_seed.DemoLearningSeedTests.test_whole_seed_rolls_back_on_failure) ... ok

----------------------------------------------------------------------
Ran 50 tests in 2.198s

OK
```

## 全量后端测试结果

```text
数据库操作失败（update_password）：OperationalError
数据库操作失败（delete_user）：OperationalError
.....数据库操作失败（delete_user）：IntegrityError
............................Executing <Task pending name='Task-2459' coro=<WeekScheduleTests.test_current_week_today_flags_and_default_week_use_injected_shanghai_date() running at /Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend/tests/test_week_schedules.py:335> cb=[_run_until_complete_cb() at /Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/asyncio/base_events.py:181] created at /Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/asyncio/runners.py:100> took 0.139 seconds
.....................
----------------------------------------------------------------------
Ran 425 tests in 25.280s

OK
```

## 前端81项测试结果

```text
✔ buildCourseTimeline 对缺失或异常字段保持安全 (0.087041ms)
ℹ tests 81
ℹ suites 0
ℹ pass 81
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 501.081791
```

## 真实HTTP与并发延期实测

```text
/Users/jiazepin/Desktop/校园项目/campus-course-agent/scaffold/backend/.venv/lib/python3.12/site-packages/fastmcp/server/auth/providers/jwt.py:10: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
It will be compatible before version 2.0.0.
  from authlib.jose import JsonWebKey, JsonWebToken
[PASS] 8 个并发延期：计数 1→9；截止时间再推后8天
[PASS] CRUD / 分页筛选 / 越权404 / 非法400 / 课程与用户关联409 / 未登录401
[PASS] 临时任务、作业、用户与课程均已清理
```

## 最终新增表数量

```text
{
  "assignments": 15,
  "plan_tasks": 5,
  "assignments_by_user_type": [
    [
      2,
      "assignment",
      9
    ],
    [
      2,
      "exam",
      3
    ],
    [
      3,
      "assignment",
      2
    ],
    [
      3,
      "exam",
      1
    ]
  ],
  "tasks_by_user_status": [
    [
      2,
      "pending",
      4
    ],
    [
      2,
      "done",
      1
    ]
  ],
  "task_defer_counts": [
    0,
    2,
    0,
    0,
    0
  ]
}
```

## 原有资产最终核对

```text
PASS: users=3 coruses=22 schedules=314 documents=4 study_plans=0; vectors=28; protected assets/.env/frontend unchanged
```

## 真实接口返回记录

完整非登录响应见 [P13-Step1-真实接口记录.json](P13-Step1-真实接口记录.json)。记录中的临时课程、用户、作业与任务均已清理；没有保存任何JWT或密码。

### POST /api/assignments

```json
{
  "code": 200,
  "message": "请求成功",
  "data": {
    "id": 32,
    "user_id": 2,
    "title": "第3章课后习题",
    "status": "pending",
    "course_id": 143,
    "course_name": "P13-fc3363a55f5f验证课程",
    "due_at": "2026-10-12T18:00:00",
    "create_time": "2026-10-11T12:20:28",
    "update_time": "2026-10-11T12:20:28",
    "type": "assignment",
    "note": null
  }
}
```

### GET /api/assignments?course_id=143&type=assignment&status=done&page=1&page_size=1

```json
{
  "code": 200,
  "message": "请求成功",
  "data": {
    "list": [
      {
        "id": 32,
        "user_id": 2,
        "title": "第3章课后习题",
        "status": "done",
        "course_id": 143,
        "course_name": "P13-fc3363a55f5f验证课程",
        "due_at": "2026-10-12T18:00:00",
        "create_time": "2026-10-11T12:20:28",
        "update_time": "2026-10-11T12:20:28",
        "type": "assignment",
        "note": null
      }
    ],
    "total": 1
  }
}
```

### POST /api/plan-tasks

```json
{
  "code": 200,
  "message": "请求成功",
  "data": {
    "id": 13,
    "user_id": 2,
    "title": "复习栈与队列",
    "status": "pending",
    "course_id": 143,
    "course_name": "P13-fc3363a55f5f验证课程",
    "due_at": "2026-10-12T20:00:00",
    "create_time": "2026-10-11T12:20:28",
    "update_time": "2026-10-11T12:20:28",
    "defer_count": 0
  }
}
```

### POST /api/plan-tasks/13/defer

```json
{
  "code": 200,
  "message": "请求成功",
  "data": {
    "id": 13,
    "user_id": 2,
    "title": "复习栈与队列",
    "status": "pending",
    "course_id": 143,
    "course_name": "P13-fc3363a55f5f验证课程",
    "due_at": "2026-10-13T20:00:00",
    "create_time": "2026-10-11T12:20:28",
    "update_time": "2026-10-11T12:20:28",
    "defer_count": 1
  }
}
```
