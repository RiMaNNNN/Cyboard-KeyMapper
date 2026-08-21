"""Tests for the GitHub build driver, flash assistant, and firmware wizard API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from entities import GIT_IDENTITY_EMAIL, GIT_IDENTITY_NAME, AppState, KeyMapperError
from services import (
    create_app,
    download_firmware,
    ensure_firmware_repo,
    find_bootloader_drive,
    flash_uf2,
    github_status,
    push_firmware_config,
    wait_for_build,
)
from tests.conftest import build_parameters


class FakeRunner:
    """Scripted command runner: matches commands by prefix, records every call."""

    def __init__(self) -> None:
        self.calls: List[List[str]] = []
        self.rules: List[Tuple[Tuple[str, ...], Tuple[int, str, str]]] = []

    def on(self, *prefix: str, result: Tuple[int, str, str]) -> None:
        """Register a canned result for commands starting with ``prefix``."""
        self.rules.append((prefix, result))

    def __call__(
        self, args: List[str], cwd: Optional[Path] = None, timeout_s: float = 120.0
    ) -> Tuple[int, str, str]:
        self.calls.append(list(args))
        for prefix, result in self.rules:
            if tuple(args[: len(prefix)]) == prefix:
                return result
        return 1, "", f"no rule for: {args}"


def test_github_status_parses_login() -> None:
    runner = FakeRunner()
    runner.on("gh", "api", "user", result=(0, "keebuser\n", ""))
    assert github_status(runner) == {"available": True, "authenticated": True, "login": "keebuser"}

    missing = FakeRunner()
    missing.on("gh", result=(127, "", "not found"))
    assert github_status(missing)["available"] is False


def test_ensure_firmware_repo_creates_and_clones(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.on("gh", "api", "user", result=(0, "keebuser", ""))
    runner.on("gh", "repo", "view", result=(1, "", "not found"))
    runner.on("gh", "repo", "create", result=(0, "", ""))

    def clone_side_effect(args: List[str], cwd=None, timeout_s=120.0):
        repo_dir = Path(args[4])
        (repo_dir / ".git").mkdir(parents=True)
        (repo_dir / "config").mkdir()
        return 0, "", ""

    original_call = runner.__call__

    def dispatch(args: List[str], cwd=None, timeout_s=120.0):
        if args[:3] == ["gh", "repo", "clone"]:
            original_call(args, cwd, timeout_s)
            return clone_side_effect(args)
        return original_call(args, cwd, timeout_s)

    repo_dir = ensure_firmware_repo(build_parameters(), tmp_path, dispatch)
    assert repo_dir == tmp_path / "repo"
    assert (repo_dir / ".git").is_dir()
    created = [c for c in runner.calls if c[:3] == ["gh", "repo", "create"]]
    assert created and created[0][3] == "keebuser/keymap-imprint-firmware"
    assert "--template" in created[0]


def test_ensure_firmware_repo_requires_auth(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.on("gh", "api", "user", result=(1, "", "not logged in"))
    with pytest.raises(KeyMapperError, match="not authenticated"):
        ensure_firmware_repo(build_parameters(), tmp_path, runner)


def test_push_firmware_config_commits_and_returns_sha(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "config").mkdir(parents=True)
    (workspace / "config" / "imprint.keymap").write_text("keymap", encoding="utf-8")
    (workspace / "config" / "imprint.conf").write_text("conf", encoding="utf-8")
    repo_dir = tmp_path / "repo"
    (repo_dir / "config").mkdir(parents=True)

    runner = FakeRunner()
    runner.on("git", "add", result=(0, "", ""))
    runner.on("git", "status", result=(0, " M config/imprint.keymap\n", ""))
    # The commit runs as "git -c user.name=... -c user.email=... commit ...".
    runner.on("git", "-c", result=(0, "", ""))
    runner.on("git", "push", result=(0, "", ""))
    runner.on("git", "rev-parse", result=(0, "abc123def456\n", ""))

    sha = push_firmware_config(repo_dir, workspace, runner)
    assert sha == "abc123def456"
    assert (repo_dir / "config" / "imprint.keymap").read_text("utf-8") == "keymap"
    assert ["git", "push"] in runner.calls
    commit_call = next(c for c in runner.calls if "commit" in c)
    assert f"user.name={GIT_IDENTITY_NAME}" in commit_call
    assert f"user.email={GIT_IDENTITY_EMAIL}" in commit_call


def test_wait_for_build_polls_until_success() -> None:
    runner = FakeRunner()
    states = iter(
        [
            (0, json.dumps([{"databaseId": 9, "status": "in_progress", "conclusion": None}]), ""),
            (0, json.dumps([{"databaseId": 9, "status": "completed", "conclusion": "success"}]), ""),
        ]
    )

    def dispatch(args, cwd=None, timeout_s=120.0):
        runner.calls.append(list(args))
        return next(states)

    result = wait_for_build("keebuser/repo", "abc", timeout_s=30.0, runner=dispatch, poll_interval_s=0.01)
    assert result == {"run_id": 9, "status": "completed", "conclusion": "success"}


def test_wait_for_build_raises_on_failure() -> None:
    def dispatch(args, cwd=None, timeout_s=120.0):
        return 0, json.dumps([{"databaseId": 9, "status": "completed", "conclusion": "failure"}]), ""

    with pytest.raises(KeyMapperError, match="failure"):
        wait_for_build("keebuser/repo", "abc", timeout_s=5.0, runner=dispatch, poll_interval_s=0.01)


def test_download_firmware_finds_uf2(tmp_path: Path) -> None:
    def dispatch(args, cwd=None, timeout_s=120.0):
        dest = Path(args[args.index("--dir") + 1]) / "firmware"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "imprint_left-assimilator-bt-zmk.uf2").write_bytes(b"L")
        (dest / "imprint_right-assimilator-bt-zmk.uf2").write_bytes(b"R")
        return 0, "", ""

    files = download_firmware("keebuser/repo", 9, tmp_path / "artifacts", dispatch)
    assert [f.name for f in files] == [
        "imprint_left-assimilator-bt-zmk.uf2",
        "imprint_right-assimilator-bt-zmk.uf2",
    ]


def test_download_firmware_clears_stale_artifacts(tmp_path: Path) -> None:
    """Leftovers from an earlier run are removed before downloading."""
    stale = tmp_path / "artifacts" / "firmware" / "imprint_left-assimilator-bt-zmk.uf2"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"OLD")

    def dispatch(args, cwd=None, timeout_s=120.0):
        dest = Path(args[args.index("--dir") + 1]) / "firmware"
        # The downloader fails when the target file already exists, mirroring
        # the real tool; the pre-clear must have removed the stale file.
        assert not (dest / "imprint_left-assimilator-bt-zmk.uf2").exists()
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "imprint_left-assimilator-bt-zmk.uf2").write_bytes(b"NEW")
        return 0, "", ""

    files = download_firmware("keebuser/repo", 9, tmp_path / "artifacts", dispatch)
    assert files[0].read_bytes() == b"NEW"


def test_find_bootloader_drive_and_flash(tmp_path: Path) -> None:
    drive = tmp_path / "ASSIMILATOR"
    drive.mkdir()
    marker = drive / "INFO_UF2.TXT"
    marker.write_text("Board-ID: nRF52840-assimilator-ble", encoding="utf-8")
    assert find_bootloader_drive([tmp_path / "nope", drive]) == drive
    assert find_bootloader_drive([tmp_path / "nope"]) is None

    uf2 = tmp_path / "fw.uf2"
    uf2.write_bytes(b"UF2!")

    import threading

    def consume() -> None:
        time.sleep(0.2)
        marker.unlink()

    threading.Thread(target=consume, daemon=True).start()
    assert flash_uf2(uf2, drive, disappearance_timeout_s=5.0) is True
    assert (drive / "fw.uf2").read_bytes() == b"UF2!"


@pytest.fixture()
def wizard(app_state: AppState, tmp_path: Path) -> Iterator[Tuple[TestClient, AppState, FakeRunner]]:
    """API client with fake runner, fake drive probe, unlocked fake keyboard."""
    runner = FakeRunner()
    app_state.process_runner = runner
    drive_dir = tmp_path / "bootdrive"
    app_state.drive_probe = lambda: [drive_dir]
    app_state.fake_device.press_unlock_combo()
    app = create_app(app_state)
    with TestClient(app) as client:
        app_state.lock_state = app_state.client.get_lock_state()
        yield client, app_state, runner


def test_wizard_presets_and_macro_staging(wizard) -> None:
    client, state, _ = wizard
    presets = client.get("/api/firmware/presets").json()
    assert any(p["node_name"] == "euro_sign" for p in presets)

    response = client.post("/api/firmware/macros", json={"macros": presets[:2]})
    assert response.json()["count"] == 2
    assert len(state.pending_macros) == 2
    assert client.get("/api/firmware/macros").json()[0]["node_name"] == presets[0]["node_name"]

    duplicated = client.post("/api/firmware/macros", json={"macros": [presets[0], presets[0]]})
    assert duplicated.status_code == 400


def test_wizard_generate_uses_fresh_backup(wizard) -> None:
    client, state, _ = wizard
    presets = client.get("/api/firmware/presets").json()
    client.post("/api/firmware/macros", json={"macros": presets})
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 200
    body = response.json()
    keymap_file = Path(state.paths.FIRMWARE_WORKSPACE_DIR) / "config" / "imprint.keymap"
    assert str(keymap_file) in body["files"]
    text = keymap_file.read_text("utf-8")
    assert "euro_sign: euro_sign {" in text
    assert 'display-name = "Base";' in text
    # The fake device's layout names are not real Imprint layouts: expect warning.
    assert any("not recognized" in w for w in body["warnings"])


def test_wizard_generate_requires_backup_or_device(wizard) -> None:
    client, state, _ = wizard
    state.fake_device.lock()
    time.sleep(0.1)
    state.lock_state = state.client.get_lock_state()
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 409


def test_wizard_build_job_lifecycle(wizard, tmp_path: Path) -> None:
    client, state, runner = wizard
    presets = client.get("/api/firmware/presets").json()
    client.post("/api/firmware/macros", json={"macros": presets[:1]})
    assert client.post("/api/firmware/generate", json={"confirm": True}).status_code == 200

    workspace = Path(state.paths.FIRMWARE_WORKSPACE_DIR)
    repo_dir = workspace / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    (repo_dir / "config").mkdir()

    runner.on("gh", "api", "user", result=(0, "keebuser", ""))
    runner.on("gh", "repo", "view", result=(0, "{}", ""))
    runner.on("git", "pull", result=(0, "", ""))
    runner.on("git", "add", result=(0, "", ""))
    runner.on("git", "status", result=(0, "M config/imprint.keymap", ""))
    runner.on("git", "-c", result=(0, "", ""))
    runner.on("git", "push", result=(0, "", ""))
    runner.on("git", "rev-parse", result=(0, "cafebabe1234", ""))
    runner.on(
        "gh", "run", "list",
        result=(0, json.dumps([{"databaseId": 5, "status": "completed", "conclusion": "success"}]), ""),
    )

    def download_rule(args, cwd=None, timeout_s=120.0):
        if args[:3] == ["gh", "run", "download"]:
            dest = Path(args[args.index("--dir") + 1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "imprint_left-assimilator-bt-zmk.uf2").write_bytes(b"L")
            return 0, "", ""
        return runner(args, cwd, timeout_s)

    state.process_runner = download_rule

    assert client.post("/api/firmware/build", json={"confirm": True}).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get("/api/firmware/status").json()["job"]
        if job.get("phase") in ("done", "error"):
            break
        time.sleep(0.05)
    assert job["phase"] == "done", job
    assert job["uf2_files"] and job["uf2_files"][0].endswith(".uf2")


def test_wizard_flash_and_finalize(wizard, tmp_path: Path) -> None:
    client, state, _ = wizard
    artifacts = Path(state.paths.FIRMWARE_WORKSPACE_DIR) / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "imprint_left-assimilator-bt-zmk.uf2").write_bytes(b"L")

    # No bootloader drive present yet.
    response = client.post(
        "/api/firmware/flash",
        json={"file": "imprint_left-assimilator-bt-zmk.uf2", "confirm": True},
    )
    assert response.status_code == 409

    drive_dir = state.drive_probe()[0]
    drive_dir.mkdir()
    marker = drive_dir / "INFO_UF2.TXT"
    marker.write_text("Board-ID: nRF52840-assimilator-ble", encoding="utf-8")

    import threading

    threading.Timer(0.3, marker.unlink).start()
    response = client.post(
        "/api/firmware/flash",
        json={"file": "imprint_left-assimilator-bt-zmk.uf2", "confirm": True},
    )
    assert response.status_code == 200, response.text

    finalize = client.post("/api/firmware/finalize", json={"confirm": True})
    assert finalize.status_code == 200
    body = finalize.json()
    assert body["backup"].startswith("keymap_backup_")
    assert len(body["keymap"]["layers"]) == 5

    unknown = client.post("/api/firmware/flash", json={"file": "evil\\..\\x.uf2", "confirm": True})
    assert unknown.status_code == 404
