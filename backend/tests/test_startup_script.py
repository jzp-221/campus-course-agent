"""完整 start.sh 的离线桩回归；不监听端口、不连接数据库、不调用外部 API。"""

from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


START_SCRIPT = Path(__file__).resolve().parents[2] / "start.sh"
README = START_SCRIPT.parent / "README.md"

PYTHON_STUB = r'''
from pathlib import Path
import os, signal, subprocess, sys, time

state = Path(os.environ["STUB_STATE"])
args = sys.argv[1:]
existing = set(os.environ.get("STUB_EXISTING", "").split(","))
if args == ["--version"]:
    print("Python 3.12.0 (offline stub)")
    sys.exit(0)
if len(args) >= 2 and args[0] == "-c":
    code = args[1]
    if "campus_service_launcher" in code:
        sys.argv = [sys.argv[0], *args[2:]]
        exec(code)
    elif "campus_service_probe" in code:
        name = args[2]
        with (state / "probes").open("a") as stream:
            stream.write(name + "\n")
        if name == os.environ.get("STUB_UNREADY"):
            sys.exit(1)
        ready = name in existing or (state / (name + ".pid")).exists() and not (state / (name + ".failed")).exists()
        if ready and name == os.environ.get("STUB_LOST_AFTER_READY"):
            verified = state / (name + ".verified")
            if verified.exists():
                sys.exit(1)
            verified.touch()
        sys.exit(0 if ready else 1)
    elif "SELECT 1" in code:
        print("OK|3|22|314|4")
    elif "def llm():" in code:
        print("对话模型|OK|offline model")
        print("Embedding|OK|offline embedding")
    else:
        sys.exit("Unexpected Python preflight")
    sys.exit(0)

name = "FastAPI" if "uvicorn" in args else "MCP" if "mcp_server.py" in args else "前端"
(state / (name + ".pid")).write_text(str(os.getpid()))
if name == os.environ.get("STUB_FAIL"):
    (state / (name + ".failed")).touch()
    print("Offline simulated startup failure", flush=True)
    sys.exit(2)
child = None
if os.environ.get("STUB_CHILD") == name:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (state / (name + ".child")).write_text(str(child.pid))

def stop(signum, frame):
    with (state / "terminated").open("a") as stream:
        stream.write(name + "\n")
    if child is not None:
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=1)
    sys.exit(0)

signal.signal(signal.SIGTERM, stop)
while True:
    time.sleep(0.1)
'''

LSOF_STUB = r'''
from pathlib import Path
import os, sys
ports = {"FastAPI": "8000", "MCP": "8001", "前端": "5173"}
existing = os.environ.get("STUB_EXISTING", "").split(",")
port = next((arg.split(":", 1)[1] for arg in sys.argv if arg.startswith("-iTCP:")), "")
for name in existing:
    if ports.get(name) == port:
        if "-t" in sys.argv:
            print((Path(os.environ["STUB_STATE"]) / (name + ".pid")).read_text())
        sys.exit(0)
sys.exit(1)
'''


class StartupScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="campus-startup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "scaffold"
        self.backend = self.project / "backend"
        self.bin = self.root / "bin"
        self.state = self.root / "state"
        for path in (self.backend / ".venv/bin", self.project / "frontend/node_modules", self.bin, self.state):
            path.mkdir(parents=True)
        shutil.copyfile(START_SCRIPT, self.project / "start.sh")
        self.python = self.backend / ".venv/bin/python"
        self.executable(self.python, f"#!{sys.executable}\n" + PYTHON_STUB)
        self.executable(self.bin / "lsof", f"#!{sys.executable}\n" + LSOF_STUB)
        self.executable(self.bin / "python3", "#!/bin/sh\nprintf 'Python 3.12 offline\\n'\n")
        self.executable(self.bin / "node", "#!/bin/sh\nprintf 'v22.18 offline\\n'\n")
        self.executable(self.bin / "npm", f'''#!/bin/sh
if [ "$1" = --version ]; then printf '10 offline\\n'; exit 0; fi
exec '{self.python}' frontend-worker
''')
        (self.backend / ".env").write_text(
            "LLM_API_KEY=offline-placeholder\nEMBEDDING_API_KEY=offline-placeholder\n"
            "DATABASE_URL=offline-placeholder\nJWT_SECRET_KEY=offline-placeholder\n"
        )
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "STUB_STATE": str(self.state), "STARTUP_TIMEOUT_SECONDS": "1",
                    "TMPDIR": str(self.root)}
        # Never inherit unrelated fixture flags from a calling test process.
        for key in ("STUB_EXISTING", "STUB_FAIL", "STUB_UNREADY", "STUB_CHILD", "STUB_LOST_AFTER_READY"):
            self.env.pop(key, None)
        self.output = self.root / "output"
        self.processes = []
        self.addCleanup(self.stop_all)

    @staticmethod
    def executable(path, content):
        path.write_text(content)
        path.chmod(0o700)

    def stop_all(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        # On assertion failure, also recover only fixture-owned process groups.
        for path in self.state.glob("*.pid"):
            pid = int(path.read_text())
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def start(self, *args):
        output = self.output.open("w")
        self.addCleanup(output.close)
        process = subprocess.Popen(["/bin/bash", str(self.project / "start.sh"), *args],
                                   env=self.env, stdout=output, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    def text(self):
        return self.output.read_text() if self.output.exists() else ""

    def wait_for(self, predicate, process=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            if process is not None and process.poll() is not None:
                self.fail(f"Startup exited before expected condition: {self.text()}")
            time.sleep(0.02)
        self.fail(f"Offline startup timed out: {self.text()}")

    def assert_owned_stopped(self):
        def stopped():
            for pattern in ("*.pid", "*.child"):
                for path in self.state.glob(pattern):
                    name = path.stem
                    if name in self.env.get("STUB_EXISTING", "").split(","):
                        continue
                    try:
                        os.kill(int(path.read_text()), 0)
                        return False
                    except ProcessLookupError:
                        pass
            return True
        self.wait_for(stopped)

    def run_failure(self, name, *, unready=False):
        self.env["STUB_UNREADY" if unready else "STUB_FAIL"] = name
        process = self.start()
        self.assertNotEqual(process.wait(timeout=8), 0, self.text())
        self.assertNotIn("启动完成", self.text())
        self.assertIn(name, self.text())
        self.assertIn("日志", self.text())
        self.assertNotIn("offline-placeholder", self.text())
        self.assert_owned_stopped()

    def test_backend_start_failure_returns_nonzero(self):
        self.run_failure("FastAPI")

    def test_mcp_start_failure_cleans_owned_backend(self):
        self.run_failure("MCP")

    def test_vite_start_failure_cleans_backend_and_mcp(self):
        self.run_failure("前端")

    def test_running_but_unready_mcp_returns_nonzero(self):
        self.run_failure("MCP", unready=True)

    def test_service_losing_readiness_before_completion_is_rejected(self):
        self.env["STUB_LOST_AFTER_READY"] = "FastAPI"
        process = self.start()
        self.assertNotEqual(process.wait(timeout=8), 0, self.text())
        self.assertNotIn("启动完成", self.text())
        self.assertIn("最终就绪确认", self.text())
        self.assert_owned_stopped()

    def test_success_requires_all_three_probes_before_completion(self):
        process = self.start()
        self.wait_for(lambda: "启动完成" in self.text(), process)
        self.assertEqual(set((self.state / "probes").read_text().splitlines()), {"FastAPI", "MCP", "前端"})
        for name in ("FastAPI", "MCP", "前端"):
            self.assertLess(self.text().index(name + " 已就绪"), self.text().index("启动完成"))
        process.terminate()
        self.assertEqual(process.wait(timeout=5), 143)
        self.assert_owned_stopped()

    def test_ctrl_c_reclaims_reload_or_npm_child_process(self):
        self.env["STUB_CHILD"] = "FastAPI"
        process = self.start()
        self.wait_for(lambda: "启动完成" in self.text(), process)
        self.assertTrue((self.state / "FastAPI.child").exists())
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(timeout=5), 130)
        self.assert_owned_stopped()

    def test_existing_backend_survives_new_mcp_failure(self):
        existing = subprocess.Popen([str(self.python), "-m", "uvicorn"], env=self.env, start_new_session=True)
        self.processes.append(existing)
        self.wait_for(lambda: (self.state / "FastAPI.pid").exists())
        self.env["STUB_EXISTING"] = "FastAPI"
        self.run_failure("MCP")
        self.assertIsNone(existing.poll(), "Reused service must not be killed")

    def test_restart_mcp_rejects_unidentified_listener_without_killing_it(self):
        existing = subprocess.Popen([str(self.python), "mcp_server.py"], env=self.env, start_new_session=True)
        self.processes.append(existing)
        self.wait_for(lambda: (self.state / "MCP.pid").exists())
        self.env["STUB_EXISTING"] = "MCP"
        self.env["STUB_UNREADY"] = "MCP"
        process = self.start("--restart-mcp")
        self.assertNotEqual(process.wait(timeout=8), 0, self.text())
        self.assertIn("拒绝关闭现有进程", self.text())
        self.assertNotIn("启动完成", self.text())
        self.assertIsNone(existing.poll(), "Unknown existing listener must not be killed")
        self.assert_owned_stopped()

    def test_all_existing_services_are_checked_and_not_stopped(self):
        self.env["STUB_EXISTING"] = "FastAPI,MCP,前端"
        for name in ("FastAPI", "MCP", "前端"):
            (self.state / (name + ".pid")).write_text("99999999")
        process = self.start()
        self.assertEqual(process.wait(timeout=8), 0, self.text())
        self.assertIn("启动完成", self.text())
        self.assertFalse((self.state / "terminated").exists())
        self.assertEqual(set((self.state / "probes").read_text().splitlines()), {"FastAPI", "MCP", "前端"})

    def test_unready_existing_backend_is_rejected_without_starting_services(self):
        self.env["STUB_EXISTING"] = "FastAPI"
        self.env["STUB_UNREADY"] = "FastAPI"
        process = self.start()
        self.assertNotEqual(process.wait(timeout=8), 0)
        self.assertNotIn("启动完成", self.text())
        self.assertFalse(list(self.state.glob("*.pid")))

    def test_check_only_does_not_launch_or_probe_services(self):
        process = self.start("--check")
        self.assertEqual(process.wait(timeout=8), 0, self.text())
        self.assertIn("未启动服务", self.text())
        self.assertFalse(list(self.state.glob("*.pid")))
        self.assertFalse((self.state / "probes").exists())

    def test_no_frontend_skips_its_launch_and_probe(self):
        process = self.start("--no-frontend")
        self.wait_for(lambda: "启动完成" in self.text(), process)
        self.assertFalse((self.state / "前端.pid").exists())
        self.assertEqual(set((self.state / "probes").read_text().splitlines()), {"FastAPI", "MCP"})
        process.terminate()
        self.assertEqual(process.wait(timeout=5), 143)
        self.assert_owned_stopped()

    def test_readme_test_loop_stops_before_build_after_early_failure(self):
        text = README.read_text()
        command = next(line for line in text.splitlines() if "for f in tests/*.mjs" in line)
        prefix = """cd() { return 0; }
node() { [ "$1" != tests/first.mjs ]; }
npm() { printf BUILD_WAS_CALLED; }
"""
        source = prefix + command.replace("tests/*.mjs", "tests/first.mjs tests/last.mjs")
        result = subprocess.run(["/bin/bash", "-c", source], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("BUILD_WAS_CALLED", result.stdout)


if __name__ == "__main__":
    unittest.main()
