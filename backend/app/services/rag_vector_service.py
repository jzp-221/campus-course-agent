"""Serialize all Chroma clients across threads AND processes on the same path."""

import fcntl
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from app.common.exceptions import BusinessException
from app.config import BASE_DIR, CHROMA_DIR, settings


def embedding_identity(config=None):
    config = settings if config is None else config
    value = config.EMBEDDING_BASE_URL.rstrip("/") + "\n" + config.EMBEDDING_MODEL
    return hashlib.sha256(value.encode()).hexdigest()


def _prepare_directory(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    ignore_path = directory / ".gitignore"
    previous = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    if "*" not in previous.splitlines():
        ignore_path.write_text(previous.rstrip() + "\n*\n" if previous else "*\n", encoding="utf-8")
    return directory


@contextmanager
def document_operation_lock(document_id: int):
    """Hold throughout reindex/delete including embedding and compensation."""
    try:
        directory = _prepare_directory(CHROMA_DIR)
        with (directory / f".document-{document_id}.lock").open("a") as lock:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BusinessException("文档正在处理，请稍后重试", code=503)
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError as error:
        raise BusinessException("文档操作锁不可用，请稍后重试", code=503) from error


def vector_operation(operation: str, *, directory=None, **payload):
    directory = Path(CHROMA_DIR if directory is None else directory).resolve()
    try:
        directory = _prepare_directory(directory)
        with (directory / ".operation.lock").open("a") as lock:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BusinessException("检索失败：Chroma 操作繁忙，请稍后重试", code=503)
                    time.sleep(0.02)
            # The lock remains held until the child (including native Chroma
            # threads) has fully exited. No long-lived cached client exists.
            try:
                process = subprocess.run(
                    [sys.executable, "-m", "app.services.rag_vector_worker"],
                    input=json.dumps({"path": str(directory), "operation": operation, **payload}, ensure_ascii=False, allow_nan=False),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    # If the parent is killed, its child still holds the same
                    # open lock description until native Chroma has exited.
                    pass_fds=(lock.fileno(),),
                    cwd=BASE_DIR, timeout=15,
                    env={**os.environ, "ANONYMIZED_TELEMETRY": "False", "OMP_NUM_THREADS": "1"},
                )
                result = json.loads(process.stdout)
                if process.returncode or result.get("ok") is not True:
                    raise ValueError("Chroma operation failed")
                return result["result"]
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except BusinessException:
        raise
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise BusinessException("检索失败：Chroma 持久化向量库读写异常", code=502) from error
