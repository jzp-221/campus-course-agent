"""curl -N 真正请求 FastAPI SSE；完整输出并检查正常、错误两条流。"""

from datetime import timedelta
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import httpx

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
from app.config import settings
from app.services.agent_service import shanghai_today
from e2e_mcp import API, CheckFailed, Fixture, check, headers, interrupted, login, passed, start_mcp, stop_mcp, wait_mcp
from sse_contract import FIELDS, SSEContract


def curl_stream(token):
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'

    # Token 只通过 stdin 传给 curl，不放到 argv、临时文件或输出里。
    config = '\n'.join([
        'url = ' + quote(API + '/api/agent/chat/stream'), 'request = "POST"',
        'header = ' + quote('Authorization: Bearer ' + token),
        'header = "Content-Type: application/json"', 'header = "Accept: text/event-stream"',
        'data = ' + quote(json.dumps({"message": "我明天有什么课？", "history": []}, ensure_ascii=False)),
        'max-time = ' + str((settings.LLM_TIMEOUT_SECONDS + 10) * settings.AGENT_MAX_ITERATIONS + 30),
    ]) + '\n'
    contract = SSEContract()
    with tempfile.TemporaryFile(mode='w+') as errors:
        process = subprocess.Popen(['curl', '-N', '--noproxy', '*', '--silent', '--show-error',
                                    '--fail-with-body', '--config', '-'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, text=True)
        try:
            process.stdin.write(config)
            process.stdin.close()
            frame = []
            for line in process.stdout:
                print(line, end='', flush=True)
                text = line.rstrip('\r\n')
                if not text:
                    contract.frame(frame)
                    frame = []
                else:
                    frame.append(text)
            check(not frame, 'SSE 帧未结束')
            check(process.wait(timeout=5) == 0, 'curl 请求失败，请检查 HTTP 状态及后端配置')
            contract.finish()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
    return contract


def main():
    if not settings.LLM_API_KEY.get_secret_value().strip():
        print('[FAIL] 未配置 LLM_API_KEY；未启动 MCP、未调用模型', file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, interrupted)
    process = None
    fixture = None
    failures = []
    with tempfile.TemporaryFile() as log, httpx.Client(base_url=API, trust_env=False, timeout=25) as client:
        try:
            response = client.get('/openapi.json')
            check(response.status_code == 200 and '/api/agent/chat/stream' in response.json().get('paths', {}),
                  '已有 FastAPI 未提供 SSE 接口，请先启动或重载后端')
            process = start_mcp(log)
            wait_mcp(process)
            admin, _ = login(client, 'admin', 'admin')
            aaa, aaa_id = login(client, 'aaa', 'student')
            _, bbb_id = login(client, 'bbb', 'student')
            fixture = Fixture(client, admin, {'aaa': aaa_id, 'bbb': bbb_id})
            fixture.create(shanghai_today() + timedelta(days=1))
            print('=== 正常 SSE：curl -N 完整帧 ===', flush=True)
            normal = curl_stream(aaa)
            check(normal.terminal == 'done' and normal.calls, '正常流未实际调用工具或未完成')
            check(fixture.marker in normal.events[-1][1]['answer'], '回答未包含真实临时课程名')
            check(any(call['args'].get('date') == (shanghai_today() + timedelta(days=1)).isoformat()
                      for call in normal.calls), '未查询系统给定的明天日期')
            print('=== 连续第二次 SSE：验证连接池未被前一条流关闭 ===', flush=True)
            repeated = curl_stream(aaa)
            check(repeated.terminal == 'done' and repeated.calls, '连续第二次流未完成')
            check(fixture.marker in repeated.events[-1][1]['answer'], '连续第二次回答未包含真实临时课程')
            # 关闭脚本自己的 MCP，稳定产生 status -> error，且不再触达模型。
            stop_mcp(process)
            process = None
            print('=== MCP 不可用 SSE：curl -N 完整帧 ===', flush=True)
            failed = curl_stream(aaa)
            check(failed.terminal == 'error', 'MCP 不可用应以 error 终止')
            check({name for name, _ in normal.events + failed.events} == set(FIELDS), '未覆盖全部六种类型')
            passed('SSE 六种事件、固定字段、顺序、非空最终回答及 error 终止均通过')
        except KeyboardInterrupt:
            failures.append('运行被中断')
        except (CheckFailed, AssertionError) as exc:
            failures.append(str(exc))
        except Exception as exc:
            failures.append(f'SSE 冒烟异常：{type(exc).__name__}')
        finally:
            try:
                if fixture is not None:
                    fixture.cleanup()
            except BaseException as exc:
                failures.append(f'数据清理失败（标记 {fixture.marker}）：{type(exc).__name__}')
            finally:
                if process is not None:
                    stop_mcp(process)
    for failure in failures:
        print('[FAIL] ' + failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
