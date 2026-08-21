"""Shared fixtures for the KeyMapper backend test suite."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, List, Tuple

import pytest

from entities import AppState, Parameters


class RecorderLogger:
    """Test double for the project logger: records lines instead of writing files."""

    def __init__(self) -> None:
        self.lines: List[Tuple[str, str]] = []

    def info(self, message: str) -> None:
        """Record an INFO line."""
        self.lines.append(("INFO", message))

    def warning(self, message: str) -> None:
        """Record a WARNING line."""
        self.lines.append(("WARNING", message))

    def error(self, message: str) -> None:
        """Record an ERROR line."""
        self.lines.append(("ERROR", message))


def build_parameters() -> Parameters:
    """Return a valid Parameters instance for tests (no file needed)."""
    return Parameters.model_validate(
        {
            "server": {"host": "127.0.0.1", "port": 8756, "ui_disconnect_shutdown_s": 20},
            "device": {
                "usb_vid": 0x1D50,
                "usb_pid": 0x615E,
                "product_hint": "Imprint",
                "rpc_timeout_s": 2.0,
                "reconnect_interval_s": 5.0,
            },
            "backup": {"keep_last": 5},
            "firmware": {
                "repo_name": "keymap-imprint-firmware",
                "template_owner": "Cyboard-DigitalTailor",
                "template_repo": "zmk-user-config-template",
                "keyboard_tag": "v2026.07",
                "build_timeout_s": 900,
                "behaviors_queue_slack": 16,
            },
        }
    )


@pytest.fixture()
def app_state(tmp_path: Path) -> Iterator[AppState]:
    """AppState wired to a locked FakeImprint and temp directories."""
    from services import start_fake_device

    for sub in ("backups", "logs", "firmware_workspace", "web"):
        (tmp_path / sub).mkdir()
    paths = SimpleNamespace(
        DATA_DIR=tmp_path,
        CONFIG_FILE=tmp_path / "config.yaml",
        MACROS_FILE=tmp_path / "macros.json",
        BATTERY_ALERT_FILE=tmp_path / "battery_alert.json",
        POWER_FILE=tmp_path / "power.json",
        LOCKING_FILE=tmp_path / "locking.json",
        TRACKBALLS_FILE=tmp_path / "trackballs.json",
        LOGS_DIR=tmp_path / "logs",
        BACKUPS_DIR=tmp_path / "backups",
        FIRMWARE_WORKSPACE_DIR=tmp_path / "firmware_workspace",
        WEB_DIR=tmp_path / "web",
        PROTO_DIR=tmp_path,
        MANUAL_FILE=tmp_path / "manual.html",
    )
    state = AppState(params=build_parameters(), paths=paths, logger=RecorderLogger())
    start_fake_device(state)
    # HERMETIC GUARD: without an injected writer, the trackball live-apply
    # and resync paths fall back to scanning for the REAL keyboard over
    # WinRT and writing test configs to it (this corrupted the user's
    # actual trackballs on every pytest run). Tests override as needed.
    state.trackball_writer = lambda payload: True
    yield state
    if state.client is not None:
        state.client.close()
    if state.fake_device is not None:
        state.fake_device.stop()
