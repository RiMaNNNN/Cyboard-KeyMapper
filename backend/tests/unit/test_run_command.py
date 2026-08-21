"""Tests for services.run_command and serial-port discovery (no real devices)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import List

import pytest

from services import discover_studio_port, run_command
from tests.conftest import build_parameters


# --------------------------------------------------------------------------- #
# run_command                                                                  #
# --------------------------------------------------------------------------- #
def test_run_command_success_captures_stdout() -> None:
    code, out, err = run_command([sys.executable, "-c", "print('keymap-ok')"], timeout_s=30.0)
    assert code == 0
    assert "keymap-ok" in out
    assert err == ""


def test_run_command_propagates_exit_code_and_stderr() -> None:
    code, out, err = run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        timeout_s=30.0,
    )
    assert code == 3
    assert "boom" in err


def test_run_command_timeout_returns_124() -> None:
    code, out, err = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=1.0
    )
    assert code == 124
    assert out == ""
    assert "timeout" in err.lower()


def test_run_command_missing_binary_returns_127() -> None:
    code, out, err = run_command(["keymap-no-such-binary-xyz"], timeout_s=5.0)
    assert code == 127
    assert out == ""
    assert err != ""


# --------------------------------------------------------------------------- #
# discover_studio_port (list_ports monkeypatched; no real serial access)       #
# --------------------------------------------------------------------------- #
def _port(vid: int, pid: int, device: str, product: str = "", description: str = "") -> SimpleNamespace:
    """Build a fake serial.tools.list_ports entry."""
    return SimpleNamespace(vid=vid, pid=pid, device=device, product=product, description=description)


def _patch_ports(monkeypatch: pytest.MonkeyPatch, ports: List[SimpleNamespace]) -> None:
    import services

    monkeypatch.setattr(services.list_ports, "comports", lambda: ports)


def test_discover_returns_none_without_matching_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ports(monkeypatch, [_port(0x1234, 0x5678, "COM3", product="Other Device")])
    assert discover_studio_port(build_parameters().device) is None


def test_discover_matches_by_vid_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    device = build_parameters().device
    _patch_ports(
        monkeypatch,
        [
            _port(0x1234, 0x5678, "COM3"),
            _port(device.usb_vid, device.usb_pid, "COM7", product="Imprint"),
        ],
    )
    assert discover_studio_port(device) == "COM7"


def test_discover_uses_product_hint_among_multiple_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = build_parameters().device
    _patch_ports(
        monkeypatch,
        [
            _port(device.usb_vid, device.usb_pid, "COM4", description="Some other ZMK board"),
            _port(device.usb_vid, device.usb_pid, "COM9", description="Cyboard IMPRINT Studio"),
        ],
    )
    assert discover_studio_port(device) == "COM9"


def test_discover_falls_back_to_first_when_hint_matches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = build_parameters().device
    _patch_ports(
        monkeypatch,
        [
            _port(device.usb_vid, device.usb_pid, "COM4", description="Board A"),
            _port(device.usb_vid, device.usb_pid, "COM9", description="Board B"),
        ],
    )
    assert discover_studio_port(device) == "COM4"


# --------------------------------------------------------------------------- #
# connect_device (transport monkeypatched; no real serial access)              #
# --------------------------------------------------------------------------- #
def _bare_state() -> "AppState":
    from entities import AppState

    from tests.conftest import RecorderLogger

    return AppState(
        params=build_parameters(), paths=SimpleNamespace(), logger=RecorderLogger()
    )


def test_connect_device_returns_false_when_no_port_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services

    _patch_ports(monkeypatch, [])
    state = _bare_state()
    assert services.connect_device(state) is False
    assert state.client is None
    assert state.port_name is None


def test_connect_device_wires_client_and_reads_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connect_device over a fake transport reads identity and lock state."""
    import services
    from entities import FakeImprint, LockState, LoopbackTransport

    device_params = build_parameters().device
    _patch_ports(
        monkeypatch,
        [_port(device_params.usb_vid, device_params.usb_pid, "COM7", product="Imprint")],
    )

    loop = LoopbackTransport()
    fake = FakeImprint(loop.device_end, locked=True)
    monkeypatch.setattr(services, "SerialTransport", lambda port: loop.client_end)

    state = _bare_state()
    try:
        assert services.connect_device(state) is True
        assert state.port_name == "COM7"
        assert state.device_info is not None and state.device_info.name == "Imprint"
        assert state.lock_state == LockState.LOCKED
        assert state.client is not None and state.client.connected
    finally:
        if state.client is not None:
            state.client.close()
        fake.stop()
