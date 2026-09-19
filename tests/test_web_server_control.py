"""Web 服务进程登记和安全终止测试。"""

from __future__ import annotations

import json

import pytest

from tradingagents.web import server_control


def _write_state(path, *, pid=4321, port=8765, instance_id="instance-a"):
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "port": port,
                "instance_id": instance_id,
                "started_at": "2026-09-19T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_registered_web_server_writes_and_cleans_state(tmp_path, monkeypatch):
    path = tmp_path / "server.json"
    monkeypatch.setattr(server_control, "_process_exists", lambda _pid: False)

    with server_control.registered_web_server(
        port=9015,
        instance_id="instance-new",
        state_path=path,
    ) as state:
        assert state.port == 9015
        assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "instance-new"

    assert not path.exists()


def test_registered_web_server_rejects_running_instance(tmp_path, monkeypatch):
    path = tmp_path / "server.json"
    _write_state(path)
    monkeypatch.setattr(server_control, "_process_exists", lambda _pid: True)
    monkeypatch.setattr(
        server_control,
        "_probe_instance",
        lambda _port: "instance-a",
    )

    with (
        pytest.raises(server_control.WebServerAlreadyRunning, match="--restart"),
        server_control.registered_web_server(
            port=9015,
            instance_id="instance-new",
            state_path=path,
        ),
    ):
        pytest.fail("不应登记第二个 Web 服务")


def test_stop_web_server_terminates_only_matching_instance(tmp_path, monkeypatch):
    path = tmp_path / "server.json"
    _write_state(path)
    terminated = []
    monkeypatch.setattr(server_control, "_process_exists", lambda _pid: True)
    monkeypatch.setattr(
        server_control,
        "_probe_instance",
        lambda _port: "instance-a",
    )
    monkeypatch.setattr(
        server_control,
        "_terminate_process",
        lambda pid: terminated.append(pid),
    )
    monkeypatch.setattr(server_control, "_wait_for_exit", lambda _pid, timeout: True)

    result = server_control.stop_web_server(state_path=path)

    assert result.stopped is True
    assert terminated == [4321]
    assert not path.exists()


def test_stop_web_server_refuses_mismatched_process(tmp_path, monkeypatch):
    path = tmp_path / "server.json"
    _write_state(path)
    monkeypatch.setattr(server_control, "_process_exists", lambda _pid: True)
    monkeypatch.setattr(
        server_control,
        "_probe_instance",
        lambda _port: "another-instance",
    )
    monkeypatch.setattr(
        server_control,
        "_terminate_process",
        lambda _pid: pytest.fail("不应终止未通过校验的进程"),
    )

    with pytest.raises(server_control.WebServerControlError, match="没有终止"):
        server_control.stop_web_server(state_path=path)

    assert path.exists()


def test_stop_web_server_cleans_stale_state(tmp_path, monkeypatch):
    path = tmp_path / "server.json"
    _write_state(path)
    monkeypatch.setattr(server_control, "_process_exists", lambda _pid: False)

    result = server_control.stop_web_server(state_path=path)

    assert result.stopped is False
    assert "已经停止" in result.message
    assert not path.exists()
