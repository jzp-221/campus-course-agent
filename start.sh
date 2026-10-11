#!/usr/bin/env bash
#
# 校园课程智能助手 · 一键启动与环境自检
#
#   ./start.sh           检查环境并启动三个服务（后端 8000 / MCP 8001 / 前端 5173）
#   ./start.sh --check   只做自检，不启动任何服务
#   ./start.sh --no-frontend  只启动后端与 MCP（前端已在别处运行时用）
#   ./start.sh --restart-mcp  确认 8001 为本项目 MCP 后关闭并重新启动
#   ./start.sh --help    查看用法
#
# 设计原则：宁可启动前多花 20 秒检查，也不要在演示中途发现 Key 过期、端口被占。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"

CHECK_ONLY=0
SKIP_FRONTEND=0
RESTART_MCP=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --no-frontend) SKIP_FRONTEND=1 ;;
    --restart-mcp) RESTART_MCP=1 ;;
    -h|--help)
      sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "未知参数：$arg（用 --help 查看用法）"; exit 2 ;;
  esac
done

# ---------- 输出helper ----------
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[36m'; C_BOLD=$'\033[1m'
else
  C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''
fi
PASS=0; WARN=0; FAIL=0
ok()   { printf "  ${C_GREEN}✓${C_RESET} %s\n" "$1"; PASS=$((PASS+1)); }
warn() { printf "  ${C_YELLOW}!${C_RESET} %s\n" "$1"; WARN=$((WARN+1)); }
bad()  { printf "  ${C_RED}✗${C_RESET} %s\n" "$1"; FAIL=$((FAIL+1)); }
info() { printf "    %s\n" "$1"; }
section() { printf "\n${C_BOLD}${C_BLUE}%s${C_RESET}\n" "$1"; }

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

printf "${C_BOLD}校园课程智能助手 · 环境自检${C_RESET}\n"
printf "项目目录：%s\n" "$SCRIPT_DIR"

# ---------- 1. 基础命令 ----------
section "1. 运行环境"

for cmd in python3 node npm lsof; do
  if command -v "$cmd" >/dev/null 2>&1; then
    if [ "$cmd" = lsof ]; then
      ok "lsof 可用"
    else
      ok "$cmd $("$cmd" --version 2>&1 | head -1 | tr -d '\n')"
    fi
  else
    bad "找不到 $cmd，请先安装"
  fi
done

if [ -x "$PYTHON" ]; then
  ok "后端虚拟环境存在（.venv，$("$PYTHON" --version 2>&1)）"
else
  bad "缺少 backend/.venv"
  info "修复：cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
fi

if [ -d "$FRONTEND_DIR/node_modules" ]; then
  ok "前端依赖已安装（node_modules）"
else
  bad "缺少 frontend/node_modules"
  info "修复：cd frontend && npm install"
fi

# ---------- 2. 环境变量 ----------
section "2. 环境变量（backend/.env）"

ENV_FILE="$BACKEND_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  bad "缺少 backend/.env"
  info "修复：cd backend && cp .env.example .env，然后填入 DeepSeek 与硅基流动的 Key"
else
  ok "backend/.env 存在"
  # 只检查必填项；其余配置在 app/config.py 里有可用默认值
  check_key() {
    local name="$1" hint="$2"
    local raw
    raw="$(grep -E "^${name}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
    if [ -z "$raw" ]; then
      bad "$name 未配置或为空"
      info "$hint"
    else
      ok "$name 已配置（长度 ${#raw}）"
    fi
  }
  check_key "LLM_API_KEY"        "填入 DeepSeek Key；缺失时助手会明确报错而不是假装回答"
  check_key "EMBEDDING_API_KEY"  "填入硅基流动 Key；缺失时资料检索会报“检索失败”，不会说成“没有相关内容”"
  check_key "DATABASE_URL"       "形如 mysql+pymysql://user:pass@127.0.0.1:3306/campus_course_agent"
  check_key "JWT_SECRET_KEY"     "任意长随机串；修改后所有已登录 Token 失效"
fi

# ---------- 3. 数据库 ----------
section "3. 数据库连接"

if [ -x "$PYTHON" ] && [ -f "$ENV_FILE" ]; then
  # 必须在 backend 目录下运行，否则 import app 会失败
  DB_RESULT="$(cd "$BACKEND_DIR" && "$PYTHON" -c '
import sys
sys.path.insert(0, ".")
try:
    from app.database import SessionLocal
    from app.models.user import User
    from app.models.coures import Course
    from app.models.schedule import Schedule
    from app.models.document import Document
    from sqlalchemy import text
    db = SessionLocal()
    db.execute(text("SELECT 1"))
    print("OK|%d|%d|%d|%d" % (db.query(User).count(), db.query(Course).count(),
                              db.query(Schedule).count(), db.query(Document).count()))
    db.close()
except Exception as exc:
    # Exception text may contain a connection string or credentials.
    print("ERR|%s" % type(exc).__name__)
' 2>&1)"
  case "$DB_RESULT" in
    OK*)
      IFS='|' read -r _ users courses schedules documents <<<"$DB_RESULT"
      ok "MySQL 可连接：用户 $users · 课程 $courses · 课表 $schedules · 知识库文档 $documents"
      if [ "$schedules" = "0" ]; then
        warn "课表为空，助手查课表会返回空结果"
        info "修复：.venv/bin/python tests/manual/seed_demo_schedules.py"
      fi
      if [ "$documents" = "0" ]; then
        warn "知识库为空，资料类提问会答“没有检索到”"
        info "修复：用 admin 登录 →「知识库文档」上传课程资料并入库"
      fi
      ;;
    ERR*) bad "数据库连接失败"; info "${DB_RESULT#ERR|}"
          info "检查 MySQL 是否启动、backend/.env 的 DATABASE_URL 是否正确" ;;
    *)    bad "数据库自检输出异常，请检查依赖和配置（内部输出已隐藏）" ;;
  esac
else
  warn "跳过（缺少 .venv 或 .env）"
fi

# ---------- 4. 外部 API Key 实际可用性 ----------
section "4. 外部服务连通性（会真实调用，产生少量费用）"

if [ -x "$PYTHON" ] && [ -f "$ENV_FILE" ] && [ "$FAIL" -eq 0 ]; then
  API_RESULT="$(cd "$BACKEND_DIR" && "$PYTHON" -c '
import sys
sys.path.insert(0, ".")
from app.config import settings

def llm():
    if not settings.LLM_API_KEY.get_secret_value().strip():
        return "SKIP|未配置 LLM_API_KEY"
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        model = ChatOpenAI(model=settings.LLM_MODEL, base_url=settings.LLM_BASE_URL,
                           api_key=settings.LLM_API_KEY, timeout=20, max_retries=0)
        reply = model.invoke([HumanMessage(content="回复两个字：正常")])
        text = str(getattr(reply, "content", "")).strip()
        return "OK|%s，返回：%s" % (settings.LLM_MODEL, text[:20]) if text else "ERR|模型返回空内容"
    except Exception as exc:
        return "ERR|%s（服务响应详情已隐藏）" % type(exc).__name__

def embed():
    if not settings.EMBEDDING_API_KEY.get_secret_value().strip():
        return "SKIP|未配置 EMBEDDING_API_KEY"
    try:
        from app.services import embedding_service
        vectors = embedding_service.embed_texts(["连通性测试"])
        dim = len(vectors[0]) if vectors and vectors[0] else 0
        return "OK|%s，向量维度 %d" % (settings.EMBEDDING_MODEL, dim) if dim else "ERR|返回了空向量"
    except Exception as exc:
        return "ERR|%s（服务响应详情已隐藏）" % type(exc).__name__

for label, fn in (("对话模型", llm), ("Embedding", embed)):
    status, _, detail = fn().partition("|")
    print("%s|%s|%s" % (label, status, detail))
' 2>&1)"
  while IFS='|' read -r label status detail; do
    [ -z "$label" ] && continue
    case "$status" in
      OK)   ok "$label：$detail" ;;
      SKIP) warn "$label：$detail（助手对应能力不可用）" ;;
      ERR)  bad "$label：$detail"
            case "$label" in
              对话模型)  info "检查 LLM_API_KEY 是否有效、账户是否有额度" ;;
              Embedding) info "检查 EMBEDDING_API_KEY 是否有效（硅基流动）" ;;
            esac ;;
      *)    bad "外部服务自检输出异常，请检查依赖和配置（内部输出已隐藏）" ;;
    esac
  done <<<"$API_RESULT"
else
  warn "跳过（前置检查未通过）"
fi

# ---------- 5. 端口 ----------
section "5. 端口占用"

check_port() {
  local port="$1" name="$2"
  if port_in_use "$port"; then
    warn "$port 已被占用（$name）——将复用现有进程，不会重复启动"
  else
    ok "$port 空闲（$name 待启动）"
  fi
}
check_port 8000 "FastAPI"
check_port 8001 "MCP"
check_port 5173 "前端"

# ---------- 汇总 ----------
section "自检结果"
printf "  通过 %s 项 · 警告 %s 项 · 失败 %s 项\n" "$PASS" "$WARN" "$FAIL"

if [ "$FAIL" -gt 0 ]; then
  printf "\n${C_RED}${C_BOLD}存在必须解决的问题，已停止启动。${C_RESET}\n"
  exit 1
fi

if [ "$WARN" -gt 0 ]; then
  printf "\n${C_YELLOW}有警告项，功能可能不完整，但可以启动。${C_RESET}\n"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  printf "\n${C_GREEN}自检完成（未启动服务）。${C_RESET}\n"
  exit 0
fi

# ---------- 启动 ----------
section "启动服务"

PIDS=()
SERVICE_NAMES=()
SERVICE_LOGS=()
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-30}"
if ! [[ "$STARTUP_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$STARTUP_TIMEOUT_SECONDS" -lt 1 ] || [ "$STARTUP_TIMEOUT_SECONDS" -gt 120 ]; then
  bad "STARTUP_TIMEOUT_SECONDS 必须为 1～120 的整数"
  exit 1
fi

# 每次启动单独保存日志；终端只给路径，不回显可能含配置的异常原文。
umask 077
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/campus-course-start.XXXXXX")" || exit 1
info "本次启动日志：$LOG_DIR"

cleanup() {
  [ "${#PIDS[@]}" -gt 0 ] || return 0
  printf "\n${C_YELLOW}正在停止本次启动的服务...${C_RESET}\n"
  for pid in "${PIDS[@]:-}"; do
    # launcher 用 setsid 为每个新服务建独立进程组，包含 reload / npm 子进程。
    if [ -n "$pid" ]; then
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    fi
  done
  for _ in $(seq 1 20); do
    local alive=0
    for pid in "${PIDS[@]:-}"; do
      if [ -n "$pid" ]; then
        { kill -0 -- "-$pid" 2>/dev/null || kill -0 "$pid" 2>/dev/null; } && alive=1
      fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.1
  done
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill -KILL -- "-$pid" 2>/dev/null
    [ -n "$pid" ] && wait "$pid" 2>/dev/null
  done
  printf "已停止。\n"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

probe_service() {
  # 真实 HTTP / MCP 握手；不调用模型、embedding 或任何业务工具。
  (cd "$BACKEND_DIR" && "$PYTHON" -c '
# campus_service_probe
import asyncio, json, sys, urllib.request

async def probe_mcp():
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    async with Client(StreamableHttpTransport("http://127.0.0.1:8001/mcp")) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        if not {"get_my_schedules", "save_study_plan", "search_course_docs"} <= names:
            raise ValueError("Missing MCP tools")

try:
    service = sys.argv[1]
    if service == "MCP":
        asyncio.run(asyncio.wait_for(probe_mcp(), timeout=2))
    elif service == "FastAPI":
        with urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=2) as response:
            paths = json.load(response)["paths"]
            if not {"/api/auth/login", "/api/agent/chat/stream", "/api/schedules/week"} <= set(paths):
                raise ValueError("Unexpected API")
    else:
        with urllib.request.urlopen("http://localhost:5173/", timeout=2) as response:
            page = response.read().decode("utf-8")
            if "id=\"app\"" not in page or "/src/main.js" not in page:
                raise ValueError("Unexpected frontend")
except Exception:
    sys.exit(1)
' "$1") >/dev/null 2>&1
}

start_service() {
  local name="$1" directory="$2" logfile="$3"
  shift 3
  # exec 保持 PID 与进程组 ID 一致，回收范围永远只包含本脚本新建的服务。
  "$PYTHON" -c '
# campus_service_launcher
import os, sys
os.setsid()
os.chdir(sys.argv[1])
os.execvp(sys.argv[2], sys.argv[2:])
' "$directory" "$@" >"$logfile" 2>&1 &
  PIDS+=($!)
  SERVICE_NAMES+=("$name")
  SERVICE_LOGS+=("$logfile")
  info "$name 启动中；日志：$logfile"
}

wait_service() {
  # Bash SECONDS 只有整秒；加一秒避免 1 秒的等待在边界处立即过期。
  local name="$1" pid="${2:-}" logfile="${3:-}" deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS + 1))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      bad "$name 启动进程提前退出；日志：$logfile"
      return 1
    fi
    if probe_service "$name"; then
      # 启动后恰好退出的进程也不能被旧服务的响应冒充。
      if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        bad "$name 启动进程已退出；日志：$logfile"
        return 1
      fi
      ok "$name 已就绪"
      return 0
    fi
    sleep 0.1
  done
  bad "$name 未在 ${STARTUP_TIMEOUT_SECONDS} 秒内就绪"
  [ -z "$logfile" ] || info "查看日志：$logfile"
  [ -n "$pid" ] || info "现有端口上的服务不符合本项目响应，请检查对应终端；脚本不会关闭它"
  return 1
}

if port_in_use 8000; then
  warn "复用已在运行的 FastAPI（8000）"
  wait_service FastAPI || exit 1
else
  start_service FastAPI "$BACKEND_DIR" "$LOG_DIR/fastapi.log" "$PYTHON" -m uvicorn app.main:app --reload --port 8000
  wait_service FastAPI "${PIDS[${#PIDS[@]}-1]}" "$LOG_DIR/fastapi.log" || exit 1
fi

if [ "$RESTART_MCP" -eq 1 ] && port_in_use 8001; then
  # 显式重启也须先确认是本项目 MCP，不能关闭恰好占用此端口的其他服务。
  if ! probe_service MCP; then
    bad "无法确认 8001 是本项目 MCP，拒绝关闭现有进程，已停止启动"
    exit 1
  fi
  LEFTOVER="$(lsof -nP -iTCP:8001 -sTCP:LISTEN -t 2>/dev/null | head -1)"
  if [ -n "$LEFTOVER" ]; then
    warn "按 --restart-mcp 关闭占用 8001 的进程（PID $LEFTOVER）"
    kill -TERM "$LEFTOVER" 2>/dev/null
    for _ in $(seq 1 20); do
      port_in_use 8001 || break
      sleep 0.5
    done
    if port_in_use 8001; then
      kill -KILL "$LEFTOVER" 2>/dev/null
      sleep 1
    fi
    if port_in_use 8001; then
      bad "8001 仍被占用，已停止启动"
      exit 1
    fi
    ok "8001 已释放"
  fi
fi

if port_in_use 8001; then
  warn "复用已在运行的 MCP（8001）"
  wait_service MCP || exit 1
else
  start_service MCP "$BACKEND_DIR" "$LOG_DIR/mcp.log" "$PYTHON" mcp_server.py
  wait_service MCP "${PIDS[${#PIDS[@]}-1]}" "$LOG_DIR/mcp.log" || exit 1
fi

if [ "$SKIP_FRONTEND" -eq 1 ]; then
  warn "按 --no-frontend 跳过前端启动"
elif port_in_use 5173; then
  warn "复用已在运行的前端（5173）"
  wait_service 前端 || exit 1
else
  start_service 前端 "$FRONTEND_DIR" "$LOG_DIR/frontend.log" npm run dev -- --host localhost --port 5173 --strictPort
  wait_service 前端 "${PIDS[${#PIDS[@]}-1]}" "$LOG_DIR/frontend.log" || exit 1
fi

# 最后一个服务等待期间，前面已经就绪的服务也可能退出；完成文案前再核对。
for index in "${!PIDS[@]}"; do
  if ! kill -0 "${PIDS[$index]}" 2>/dev/null; then
    bad "${SERVICE_NAMES[$index]} 在启动期间退出；日志：${SERVICE_LOGS[$index]}"
    exit 1
  fi
done
for service in FastAPI MCP 前端; do
  [ "$service" != 前端 ] || [ "$SKIP_FRONTEND" -eq 0 ] || continue
  if ! probe_service "$service"; then
    bad "$service 在最终就绪确认时不可用，已停止启动"
    info "本次启动日志：$LOG_DIR；复用服务请查看原终端"
    exit 1
  fi
done

cat <<EOF

${C_GREEN}${C_BOLD}启动完成${C_RESET}

  打开浏览器：${C_BLUE}http://localhost:5173${C_RESET}
  接口文档：  ${C_BLUE}http://127.0.0.1:8000/docs${C_RESET}

  演示账号：admin/admin（管理员）· aaa/123（课表密集）· bbb/123（课表稀疏）

  建议按这个顺序演示：
    1. 用 aaa 登录 →「课程助手」问「我明天有什么课？」
    2. 问「实验课前需要预习吗？」（需管理员先上传资料并入库）
    3. 说「帮我生成一份学习计划并保存」→ 去「学习计划」看新记录
    4. 退出，用 bbb 登录再问课表 → 看到完全不同的课表

  按 Ctrl+C 停止本次启动的服务。
EOF

# 无新进程时复用完成即可退出；有新进程时监测异常退出，避免其余服务悬挂。
if [ "${#PIDS[@]}" -gt 0 ]; then
  while true; do
    for index in "${!PIDS[@]}"; do
      if ! kill -0 "${PIDS[$index]}" 2>/dev/null; then
        bad "${SERVICE_NAMES[$index]} 已退出；日志：${SERVICE_LOGS[$index]}"
        exit 1
      fi
    done
    sleep 0.5
  done
fi
