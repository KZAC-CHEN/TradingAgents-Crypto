"""管理本机 TradingAgents Web 服务的进程状态。"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

_SERVICE_NAME = "tradingagents-web"


class WebServerControlError(RuntimeError):
    """Web 服务状态无法被安全处理。"""


class WebServerAlreadyRunning(WebServerControlError):
    """已有受管理的 Web 服务正在运行。"""


@dataclass(frozen=True)
class WebServerState:
    """记录一个由 TradingAgents 启动的本机 Web 服务。"""

    pid: int
    port: int
    instance_id: str
    started_at: str


@dataclass(frozen=True)
class StopWebServerResult:
    """停止 Web 服务后的可展示结果。"""

    stopped: bool
    message: str


def default_server_state_path() -> Path:
    """返回 Web 服务进程状态文件的默认位置。"""
    return Path.home() / ".tradingagents" / "web" / "server.json"


def _load_state(path: Path) -> WebServerState | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return WebServerState(
            pid=int(payload["pid"]),
            port=int(payload["port"]),
            instance_id=str(payload["instance_id"]),
            started_at=str(payload["started_at"]),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise WebServerControlError(f"Web 服务状态文件无效，请删除后重试：{path}") from exc


def _write_state(path: Path, state: WebServerState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _remove_state(path: Path, instance_id: str) -> None:
    try:
        current = _load_state(path)
    except WebServerControlError:
        return
    if current is None or current.instance_id != instance_id:
        return
    with suppress(FileNotFoundError):
        path.unlink()


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows 上 os.kill(pid, 0) 会调用 TerminateProcess，不能用于存活探测。
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _probe_instance(port: int, *, timeout: float = 0.6) -> str | None:
    """读取健康检查中的实例标识，同时禁用系统代理。"""
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(
            f"http://127.0.0.1:{port}/api/health",
            timeout=timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    if payload.get("service") != _SERVICE_NAME:
        return None
    instance_id = payload.get("instanceId")
    return str(instance_id) if instance_id else None


def _terminate_process(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def _wait_for_exit(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.1)
    return not _process_exists(pid)


@contextmanager
def registered_web_server(
    *,
    port: int,
    instance_id: str,
    state_path: str | Path | None = None,
) -> Iterator[WebServerState]:
    """登记当前服务，并在正常退出时清理登记信息。"""
    path = Path(state_path or default_server_state_path()).resolve()
    existing = _load_state(path)
    if existing is not None and _process_exists(existing.pid):
        detected = _probe_instance(existing.port)
        if detected in {None, existing.instance_id}:
            raise WebServerAlreadyRunning(
                "已有 TradingAgents Web 服务正在运行"
                f"（PID {existing.pid}，端口 {existing.port}）。"
                "请使用 `tradingagents web --restart` 重启，"
                "或使用 `tradingagents web --stop` 停止。"
            )
    if existing is not None:
        _remove_state(path, existing.instance_id)

    state = WebServerState(
        pid=os.getpid(),
        port=port,
        instance_id=instance_id,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_state(path, state)
    try:
        yield state
    finally:
        _remove_state(path, instance_id)


def stop_web_server(
    *,
    state_path: str | Path | None = None,
    timeout: float = 10.0,
) -> StopWebServerResult:
    """仅终止健康检查与状态文件相互匹配的 Web 服务。"""
    path = Path(state_path or default_server_state_path()).resolve()
    state = _load_state(path)
    if state is None:
        return StopWebServerResult(
            stopped=False,
            message="没有由 TradingAgents 管理的 Web 服务正在运行。",
        )
    if not _process_exists(state.pid):
        _remove_state(path, state.instance_id)
        return StopWebServerResult(
            stopped=False,
            message="Web 服务已经停止，已清理过期的进程记录。",
        )

    detected = _probe_instance(state.port)
    if detected != state.instance_id:
        raise WebServerControlError(
            f"无法确认状态文件中的进程仍是 TradingAgents Web 服务，因此没有终止 PID {state.pid}。"
        )

    try:
        _terminate_process(state.pid)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        raise WebServerControlError(
            f"无法终止 TradingAgents Web 服务（PID {state.pid}）：{exc}"
        ) from exc

    if not _wait_for_exit(state.pid, timeout=timeout):
        raise WebServerControlError(
            f"已向 PID {state.pid} 发送终止信号，但服务未在 {timeout:g} 秒内退出。"
        )
    _remove_state(path, state.instance_id)
    return StopWebServerResult(
        stopped=True,
        message=f"已停止 TradingAgents Web 服务（PID {state.pid}，端口 {state.port}）。",
    )
