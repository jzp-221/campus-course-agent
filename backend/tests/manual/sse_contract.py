"""客户端共享的 SSE 帧解析及严格契约检查，不导入模型或读取 .env。"""

import json

FIELDS = {"status": {"stage"}, "tool_start": {"name", "args"}, "tool_end": {"name", "ok"},
          "token": {"text"}, "done": {"answer", "tool_calls"}, "error": {"code", "message"}}


class SSEContract:
    def __init__(self):
        self.events = []
        self.calls = []
        self.pending = None
        self.tokens = []
        self.terminal = None
        self.tool_failed = False

    def accept(self, name, data):
        assert name in FIELDS and isinstance(data, dict) and set(data) == FIELDS[name], "事件名或字段错误"
        assert self.terminal is None, "终止事件后又收到业务事件"
        if not self.events:
            assert name == "status", "第一个事件必须是 status"
        if self.tool_failed:
            assert name == "error", "工具失败后必须立即 error"
        if name == "status":
            assert data["stage"] in {"thinking", "querying", "saving", "retrieving"} and not self.tokens and self.pending is None
        elif name == "tool_start":
            assert not self.tokens and self.pending is None and data["name"] in {"get_my_schedules", "save_study_plan", "search_course_docs"}
            args = data["args"]
            assert isinstance(args, dict)
            if data["name"] == "get_my_schedules":
                assert set(args) <= {"date"} and (args.get("date") is None or isinstance(args["date"], str))
            elif data["name"] == "save_study_plan":
                assert set(args) == {"content"} and isinstance(args["content"], str) and args["content"].strip()
                assert len(args["content"]) <= 2000
            else:
                assert set(args) == {"query"} and isinstance(args["query"], str) and args["query"].strip()
                assert len(args["query"]) <= 2000
            self.pending = data["name"]
            self.calls.append(data)
        elif name == "tool_end":
            assert self.pending == data["name"] and type(data["ok"]) is bool
            self.pending = None
            self.tool_failed = not data["ok"]
        elif name == "token":
            assert self.pending is None and isinstance(data["text"], str)
            self.tokens.append(data["text"])
        elif name == "done":
            assert self.pending is None and isinstance(data["answer"], str) and data["answer"].strip()
            assert data["tool_calls"] == self.calls
            assert "".join(self.tokens) == data["answer"], "token 拼接与 done.answer 不一致"
            self.terminal = "done"
        elif name == "error":
            assert self.pending is None and type(data["code"]) is int and isinstance(data["message"], str)
            self.terminal = "error"
        self.events.append((name, data))

    def frame(self, lines):
        name = ""
        data = []
        for line in lines:
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            if key == "event":
                name = value.lstrip(" ")
            elif key == "data":
                data.append(value.lstrip(" "))
        if data:
            self.accept(name, json.loads("\n".join(data)))

    def finish(self):
        assert self.terminal in {"done", "error"}, "SSE 未包含终止事件"


def self_check():
    """Standalone offline contract smoke; real HTTP coverage is sse_smoke.py."""
    call = {"name": "get_my_schedules", "args": {"date": "2026-10-11"}}
    successful = [
        ("status", {"stage": "thinking"}),
        ("status", {"stage": "querying"}),
        ("tool_start", call), ("tool_end", {"name": call["name"], "ok": True}),
        ("token", {"text": "明天"}), ("token", {"text": "有课"}),
        ("done", {"answer": "明天有课", "tool_calls": [call]}),
    ]
    failed = [
        ("status", {"stage": "thinking"}), ("tool_start", call),
        ("tool_end", {"name": call["name"], "ok": False}),
        ("error", {"code": 503, "message": "服务暂时不可用"}),
    ]
    coverage = set()
    for frames in (successful, failed):
        contract = SSEContract()
        for name, data in frames:
            contract.frame(["event: " + name, "data: " + json.dumps(data, ensure_ascii=False)])
            coverage.add(name)
        contract.finish()
    assert coverage == set(FIELDS), "未覆盖六种事件"
    for invalid in [
        [("token", {"text": "顺序错误"})],
        [("status", {"stage": "thinking", "extra": True})],
        [("status", {"stage": "model-generated-stage"})],
        [("status", {"stage": "thinking"}),
         ("tool_start", {"name": "get_my_schedules", "args": {"user_id": 7}})],
        [("status", {"stage": "thinking"}), ("token", {"text": "partial"}),
         ("done", {"answer": "different", "tool_calls": []})],
        successful + [("token", {"text": "终止后错误"})],
    ]:
        contract = SSEContract()
        try:
            for name, data in invalid:
                contract.accept(name, data)
            contract.finish()
        except AssertionError:
            continue
        raise AssertionError("非法序列未被拒绝")
    print("[PASS] SSE 契约离线自检：六种事件、字段、顺序、正文拼接与非法序列拒绝均通过")


if __name__ == "__main__":
    self_check()
