# 独立公网演示部署

本目录把现有 Vue、FastAPI、真实 MCP Streamable-HTTP 与 MySQL 包装为独立的 Docker Compose 环境。它使用新数据库、新密码、新 JWT 和新编的虚构资料，保留本机业务入口与数据。源码公开不等于网站已经上线；需要服务器和域名后才能生成可分享的 HTTPS 链接。

## 平台选择

建议使用 **腾讯云轻量应用服务器，Docker CE / Ubuntu 24.04 LTS 镜像**。单机 Compose 可同时运行 MySQL、API、MCP、Caddy，持久化资料与向量也比较直接，不需要把这几个服务分散到不同的托管平台。

先按 **2 核 4 GB、40 GB 以上磁盘**估算演示资源；这是项目的工程建议，实际用量需在启动后观察。地区、价格和域名要求以购买页面为准；本部署包没有购买资源。[腾讯云 Docker 镜像官方说明](https://cloud.tencent.com/document/product/1207/60423)

准备一台独立服务器、一个可配置 DNS 的域名，并在服务器防火墙只放行 HTTP/HTTPS 的 80/443（SSH 仅管理员使用）。MySQL 3306、FastAPI 8000、MCP 8001 没有映射到公网。

## 隔离的数据与配置

| 项目 | 公网环境 |
| --- | --- |
| Compose 项目 | `campus-public-demo` |
| 数据库 | 容器 `db` 内的 `campus_demo`，独立命名卷 |
| 学生 | `demo_aaa`、`demo_bbb`，课表不同，20 周均有数据 |
| 管理员 | `demo_admin`，密码只交给运营者，不公开 |
| 初始数据 | 9 门虚构课程、148 条课表、8 条作业考试、3 条计划任务 |
| 知识库 | `sample_docs/` 的 3 份全新虚构资料；真实 embedding 入库后可检索 |
| API/MCP 资料目录 | 同一个 `/app/backend/private_documents` 命名卷 |
| API/MCP Chroma 目录 | 同一个 `/app/backend/chroma_data` 命名卷 |
| 配额 | API 独享 `/app/demo_state/quota.sqlite3` 持久卷 |

**不要复制本机 `backend/.env`、MySQL 数据、`private_documents/` 或 `chroma_data/`。** Docker 构建白名单已排除它们。当前学期仍为 2026-09-07 起的 20 周；如果真实今天不在这个学期内，可用周课表导航演示，助手会如实报告当天是否有课。

初始化入口先检查容器标记、显式演示开关和 `db/campus_demo` 目标，再导入数据库模块；拒绝本机数据库 URL 和可覆盖连接目标的 URL query。API 与 MCP 部署入口也做同一检查。种子可重复运行，不删除数据，不修改已存在的账号密码或到期时间，不写文本学习计划。

## 服务器上部署

以下命令在**独立演示服务器**执行。Docker 镜像中会安装项目依赖，服务器无需安装原项目虚拟环境；需要 Git、Python 3（仅生成配置）、Docker 与 `docker compose`。

```bash
git clone https://github.com/jzp-221/campus-course-agent.git
cd campus-course-agent
docker compose version
python3 deploy/demo/configure.py --site demo.your-domain.com
```

生成的 `deploy/demo/demo.env` 权限为 600，已忽略，且不会覆盖现有文件。它随机生成独立数据库密码、JWT、管理员密码和学生演示密码；API Key 保持空。请在服务器上用编辑器填写**演示专用**配置：

```ini
LLM_API_KEY=你的演示专用DeepSeekKey
EMBEDDING_API_KEY=你的演示专用SiliconFlowKey
DEMO_INDEX_DOCUMENTS=true
```

这两行 Key 只在服务运行时注入，不进入前端代码或 Docker 镜像。`DEMO_STUDENT_PASSWORD` 可以随演示链接发给观众；`DEMO_ADMIN_PASSWORD` 和 Key 不公开。公网演示禁用注册，演示学生不能修改共享账号信息或密码。

将域名 A/AAAA 记录指向服务器，确认 80/443 已放行，然后执行：

```bash
docker compose --env-file deploy/demo/demo.env -f deploy/demo/compose.yaml config --quiet
docker compose --env-file deploy/demo/demo.env -f deploy/demo/compose.yaml up --build -d
docker compose --env-file deploy/demo/demo.env -f deploy/demo/compose.yaml ps
docker compose --env-file deploy/demo/demo.env -f deploy/demo/compose.yaml logs --tail=80 init api mcp web
```

不要把已填真实值的 `docker compose config` 输出贴到聊天或提交 Git；上述 `--quiet` 只校验而不显示配置。首次构建和向量入库需要网络。顺序为 MySQL 健康 → 初始化成功 → MCP 就绪 → API 就绪 → Web。若初始化失败，后续服务不会假装启动成功。[Docker 启动依赖说明](https://docs.docker.com/compose/how-tos/startup-order/)

域名正确配置后，Caddy 自动申请 HTTPS 证书。访问地址是 `https://demo.your-domain.com`；这行是格式示例，实际域名需由你配置。Caddy 为 Vue 路由做回退，`/api` 同源代理到 API，并即时转发 SSE 帧，不缓存整段回答。[Caddy HTTPS](https://caddyserver.com/docs/automatic-https)，[反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

SSE 使用 `Content-Type: text/event-stream` 的默认即时刷新；没有设置负数 `flush_interval`，以保留浏览器断开向上游传播取消的行为。生产环境仍需按上线验收确认“停止生成”后请求被回收。

若只做服务器 IP 的临时连通性测试，可设 `DEMO_SITE_ADDRESS=http://服务器IP`，同时调整 `DEMO_CORS_ORIGINS`；分享登录与 AI 演示前使用上面的域名 HTTPS。

## 真实 AI 限制

限制只存在于 `deploy.demo.app:app`，原本的 `app.main:app` 不受影响，也没有假模型或绕过 MCP。API 必须按 Compose 配置使用 **1 个 worker / 1 个 API 实例**，否则进程内并发计数无法代表整个站点。

| 默认配置 | 限制 |
| --- | --- |
| `DEMO_AI_PER_MINUTE=3` | 每个账号每分钟最多 3 个付费请求 |
| `DEMO_AI_PER_USER_DAY=20` | 每个账号每天最多 20 个付费请求 |
| `DEMO_AI_GLOBAL_DAY=60` | 全站每天最多 60 个付费请求 |
| `DEMO_AI_CONCURRENCY=2` | 同时最多 2 个付费请求，超出直接拒绝 |
| `DEMO_AI_TIMEOUT_SECONDS=90` | 请求总超时；SSE 开始后用 `error` 事件报告 |
| `DEMO_MAX_OUTPUT_TOKENS=700` | 每次模型调用最多输出 700 tokens |
| `AGENT_MAX_ITERATIONS=4` | 本轮最多 4 次模型调用，含流式最终回答 |
| 输入限制 | 问题最多 1000 字，历史最多 10 条/共 6000 字，请求正文最多 32 KiB |
| 其它限制 | 登录两个入口共用每访客 10 次/分钟；访客总访问 120 次/分钟；账号业务写入 20 次/分钟、100 次/日；私有管理员批量上传总正文最多 2 MiB |

付费请求包括非流式/流式助手、课程详情的资料检索、管理员上传和重新入库，避免直接刷新检索接口绕过额度。初始化三份固定的小型资料属于一次部署入库，单独记录在 `init` 日志。学生上传与文档管理仍受原角色权限限制。

配额按 Asia/Shanghai 的日期重置，按用户 ID 而不是 JWT 计数，重新登录或重启 API 不会刷新每日额度。已经准入的失败请求也消耗次数；超额返回明确 429，配额存储故障返回 503，不会静默取消限制。流中断/超时会取消下游请求并释放并发名额。

这限制的是调用次数、输入和输出规模，**不等于供应商的金额硬限额**。演示 Key 应使用独立额度，在供应商控制台设置可用的预算限制；不要用不限额的生产 Key。空 Key 保持现有明确业务错误，不能获得伪造的 AI 或检索结果。

共享学生账号的数据可被使用该账号的观众编辑，因此只用于虚构演示，界面中不要填写私人信息；账号之间仍沿用原系统用户隔离。

## 上线验收

先确认无敏感参数的健康入口：

```bash
curl --fail https://demo.your-domain.com/api/demo/health
```

预期为 `{"status":"ok","environment":"demo"}`。随后用浏览器逐项验证：

1. `demo_aaa` 登录：周课表有虚构课程，首页作业/计划有数据。
2. 切换 `demo_bbb`：同周的课表明显不同，学生传其他用户 ID 仍只能看到自己。
3. 助手问“我明天有什么课？”：看到过程区与逐步回答，确实走真实 MCP 查询。
4. 问“虚构算法探索的实验报告可以打印吗？”：引用新编虚构资料，回答以资料原文为准。
5. 明确要求保存学习计划：回查文本计划；只规划而未要求保存时不写入。
6. 连续请求超过每分钟额度：收到 429 文案；原浏览器退出/重新登录不能绕过日配额。
7. 不公开管理员登录、MySQL、MCP 端口；另一台电脑用相同 HTTPS 链接访问。

部署包的离线测试不需要真实 Key，可在原开发工作区运行：

```bash
cd backend
.venv/bin/python -m unittest tests.test_public_demo_policy tests.test_public_demo_seed tests.test_demo_health
.venv/bin/python -m unittest discover -s tests -t .
```

构建、无 Key 的隔离容器验证与最终公网真实 AI 验收是不同检查；在没有服务器与演示 Key 前，不应把后者写成已通过。

## 停止、升级与恢复

停止服务保留全部演示卷：

```bash
docker compose --env-file deploy/demo/demo.env -f deploy/demo/compose.yaml down
```

日常更新用 `git pull` 后重复 `up --build -d`。同一配置不会重建数据；重新生成密码不会自动改已有用户密码，所以**保留原服务器的 `demo.env`**。MySQL、资料、Chroma 与 quota 卷一起备份，恢复时同时恢复这些卷。不要在更新流程使用 `down -v`，它会删除演示数据与持久配额。

本目录不是本机数据库导出工具，也不提供本机库的清空命令。
