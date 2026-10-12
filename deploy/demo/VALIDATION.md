# 部署包验证记录

日期：2026-10-11。验证对象为独立演示部署包，尚未创建云服务器或公网地址。

| 检查 | 结果 |
| --- | --- |
| 后端完整离线回归 | 542 项通过（原 493 + 新增 49） |
| 新增部署测试 | 配额/ASGI/配置 24 项、隔离种子 19 项、健康探针 6 项通过 |
| 前端回归 | 原 159 项通过 |
| 前端生产构建 | 通过 |
| Compose 配置 | 校验通过；仅 Web 发布端口、API/MCP 共用资料/向量卷、配额持久卷 |
| 本机保护文件 | backend/.env、frontend/.env.development、旧 SQL、原始课程资料的 SHA-256 未变化 |
| 业务源代码 | backend/app、mcp_server.py、frontend/src 均未修改 |
| 独立容器启动 | **未完成**：两次构建均在基础镜像拉取阶段停止，尚未进入应用镜像构建 |
| 公网 HTTPS、真实 AI | **待部署后验收**：没有服务器、域名和演示专用 Key，不计为通过 |

本次临时验证项目 `campus-demo-validation` 的容器与卷均为 0，临时生成的演示凭据已删除。没有对本机数据库执行种子、迁移或业务写入，也没有调用真实 AI/embedding。

在可构建 Docker 镜像的独立服务器上，按 [部署手册](README.md) 完成构建与“上线验收”，再补记服务器实测结果。离线通过不能替代容器与公网实测。

## 2026-10-12：独立服务器与公网 IPv4 HTTPS 实测

本次在腾讯云中国香港的独立演示服务器部署，公网地址为 **https://43.129.170.247**。使用 Ubuntu 24.04、Docker Compose 与 Caddy 2.11.7；本机业务 `.env`、数据库、原始资料与向量目录未作为部署数据使用。上面的 2026-10-11 回归数量保留为历史记录，**本次 HTTPS 切换没有重新运行整套后端/前端测试**。

| 本次检查 | 实测结果 |
| --- | --- |
| 独立初始化 | 3 个用户、9 门虚构课程、148 条课表、8 条作业考试、3 条计划任务 |
| 虚构知识库 | 3 份全新资料已真实 embedding 入库，`indexed=3` |
| 证书信任与身份 | 严格校验证书通过，Let’s Encrypt YE2 签发，SAN 为 `IP Address:43.129.170.247` |
| TLS 协议 | TLSv1.3 |
| 本次证书有效期 | 2026-10-12 03:27:02 UTC 至 2026-10-18 19:27:01 UTC；后续由 Caddy 自动续期 |
| HTTPS 健康入口 | HTTP 200，`{"status":"ok","environment":"demo"}` |
| 首页 / SPA 路由 | HTTP 200 |
| 公网 `/mcp` | HTTP 404，MCP 未通过公网代理暴露 |
| HTTP 重定向 | HTTP 308，保留路径并精确重定向到相同 IP 的 HTTPS 地址 |
| 登录 CORS 预检 | `OPTIONS` 返回允许来源 `https://43.129.170.247` |
| HTTPS 真实 AI / MCP | `demo_aaa` 登录后调用 `POST /api/agent/chat/stream` 通过；实际执行 `get_my_schedules`，参数为 `{"date":"2026-10-13"}`，没有模型可控的 `user_id`，回答返回 2 门虚构课程 |
| SSE 帧与渐进输出 | 实测 `status/status/tool_start/tool_end/token* /done`；首 token 在 +1.783 秒，`done` 在 +2.012 秒，间隔 0.229 秒 |
| 部署专用离线回归 | 当日重新运行 `tests.test_public_demo_policy`、`tests.test_public_demo_seed`、`tests.test_demo_health`，49 项全绿，用时 15.062 秒 |
| 其它真实 AI 证据 | HTTPS 切换前，用户已实测虚构资料问题回答正确；本次没有重新执行完整 AI/RAG 冒烟集合 |

切换使用私有配置备份与旧 Web 镜像保留措施，仅更新 HTTPS 入口与 API CORS；没有重新运行种子、迁移或清理数据卷。证书与自动续期状态保留在 Compose 的 Caddy 命名卷中。验证没有绕过证书信任校验，没有公开密码、API Key 或服务器私有备份。
