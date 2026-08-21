"""Tests for macro staging validation (comma split, HSB ranges), persistence,
and the low-battery alert configuration pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

from entities import AppState, BatteryAlertConfig, PowerConfig
from services import create_app, render_conf, validate_macro_binding


@pytest.fixture()
def api(app_state: AppState) -> Iterator[Tuple[TestClient, AppState]]:
    """TestClient over the fake device (unlocked for generation flows)."""
    app_state.fake_device.press_unlock_combo()
    app = create_app(app_state)
    with TestClient(app) as client:
        app_state.lock_state = app_state.client.get_lock_state()
        yield client, app_state


def _macro(node: str, binding: str) -> dict:
    """One-tap macro payload in API shape."""
    return {
        "node_name": node,
        "display_name": node,
        "steps": [{"kind": "tap", "binding": binding, "value": 0}],
        "wait_ms": 0,
        "tap_ms": 0,
    }


def test_comma_separated_behaviors_split_into_steps(api) -> None:
    client, state = api
    response = client.post(
        "/api/firmware/macros",
        json={"macros": [_macro("to_layer_1_red", "&to 1, &rgb_ug RGB_COLOR_HSB(0,100,50)")]},
    )
    assert response.status_code == 200, response.text
    macro = state.pending_macros[0]
    assert [s.binding for s in macro.steps] == ["&to 1", "&rgb_ug RGB_COLOR_HSB(0,100,50)"]
    assert all(s.kind.value == "tap" for s in macro.steps)


def test_rgb_hsb_out_of_range_rejected_with_guidance(api) -> None:
    client, _ = api
    response = client.post(
        "/api/firmware/macros",
        json={"macros": [_macro("bad", "&rgb_ug RGB_COLOR_HSB(25,250,150)")]},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "HSB" in detail and "0–100" in detail


def test_validate_macro_binding_accepts_good_and_rejects_bad() -> None:
    assert validate_macro_binding("&kp LS(A)") is None
    assert validate_macro_binding("&rgb_ug RGB_COLOR_HSB(360,100,100)") is None
    assert validate_macro_binding("kp A") is not None
    assert validate_macro_binding("&rgb_ug RGB_COLOR_HSB(361,1,1)") is not None


def test_staged_macros_persist_and_reload(api) -> None:
    client, state = api
    client.post(
        "/api/firmware/macros",
        json={"macros": [_macro("seq_one", "&to 1, &rgb_ug RGB_COLOR_HSB(60,100,50)")]},
    )
    persisted = json.loads(Path(state.paths.MACROS_FILE).read_text("utf-8"))
    assert persisted[0]["node_name"] == "seq_one"
    assert len(persisted[0]["steps"]) == 2

    from services import load_staged_state

    state.pending_macros = []
    load_staged_state(state)
    assert state.pending_macros[0].node_name == "seq_one"


def test_sequence_brightness_rewrites_all_colors(api) -> None:
    client, state = api
    client.post(
        "/api/firmware/macros",
        json={
            "macros": [
                _macro("to_layer_1_red", "&to 1, &rgb_ug RGB_COLOR_HSB(0,100,50)"),
                _macro("mom_layer_2", "&mo 2, &rgb_ug RGB_COLOR_HSB(120, 100, 50)"),
                _macro("plain_key", "&kp A"),
            ]
        },
    )
    response = client.post("/api/firmware/sequence_brightness", json={"brightness": 35})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "updated": 2, "brightness": 35}
    bindings = [
        s.binding for m in state.pending_macros for s in m.steps if "RGB" in s.binding
    ]
    assert bindings == [
        "&rgb_ug RGB_COLOR_HSB(0,100,35)",
        "&rgb_ug RGB_COLOR_HSB(120,100,35)",
    ]
    persisted = json.loads(Path(state.paths.MACROS_FILE).read_text("utf-8"))
    assert any("RGB_COLOR_HSB(0,100,35)" in s["binding"]
               for m in persisted for s in m["steps"])
    # 0 is the darkest still-lit stored value (the firmware remaps 0-100
    # onto its compiled BRT_MIN..BRT_MAX output window) and must be allowed.
    assert client.post(
        "/api/firmware/sequence_brightness", json={"brightness": 0}
    ).status_code == 200
    assert client.post(
        "/api/firmware/sequence_brightness", json={"brightness": -1}
    ).status_code == 422


def test_battery_alert_endpoints_and_persistence(api) -> None:
    client, state = api
    default = client.get("/api/firmware/battery_alert").json()
    assert default["enabled"] is False and default["threshold_percent"] == 10

    update = {
        "enabled": True,
        "threshold_percent": 10,
        "blink_count": 3,
        "hue": 359,
        "saturation": 90,
        "brightness": 50,
        "interval_minutes": 2,
    }
    assert client.put("/api/firmware/battery_alert", json=update).status_code == 200
    assert client.get("/api/firmware/battery_alert").json()["enabled"] is True
    persisted = json.loads(Path(state.paths.BATTERY_ALERT_FILE).read_text("utf-8"))
    assert persisted["interval_minutes"] == 2

    bad = dict(update, threshold_percent=0)
    assert client.put("/api/firmware/battery_alert", json=bad).status_code == 422


def test_battery_endpoint_labels_and_hints(api) -> None:
    client, state = api
    state.battery_reader = lambda: [83, 77]
    body = client.get("/api/battery").json()
    assert body["halves"] == [
        {"label": "Left (central)", "percent": 83},
        {"label": "Right", "percent": 77},
    ]
    assert body["detail"] is None

    state.battery_reader = lambda: [90]
    body = client.get("/api/battery").json()
    assert body["halves"] == [{"label": "Left (central)", "percent": 90}]
    assert "proxy" in body["detail"]

    state.battery_reader = lambda: []
    body = client.get("/api/battery").json()
    assert body["halves"] == [] and "no battery service" in body["detail"]


def test_render_conf_includes_battery_proxy_line() -> None:
    conf = render_conf([], slack=0)
    # PROXY is gated behind FETCHING (no default): both lines are required or
    # the proxy silently never compiles in.
    assert "CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING=y" in conf
    assert "CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_PROXY=y" in conf
    assert conf.index("FETCHING=y") < conf.index("PROXY=y")


def test_render_conf_includes_battery_lines_only_when_enabled() -> None:
    off = render_conf([], slack=0, battery_alert=BatteryAlertConfig(enabled=False))
    assert "KEYMAP_BATTERY_ALERT" not in off
    on = render_conf(
        [],
        slack=0,
        battery_alert=BatteryAlertConfig(
            enabled=True,
            threshold_percent=10,
            blink_count=3,
            hue=359,
            saturation=90,
            brightness=50,
            interval_minutes=2,
        ),
    )
    assert "CONFIG_KEYMAP_BATTERY_ALERT=y" in on
    assert "CONFIG_KEYMAP_BATTERY_ALERT_THRESHOLD=10" in on
    assert "CONFIG_KEYMAP_BATTERY_ALERT_BLINK_COUNT=3" in on
    assert "CONFIG_KEYMAP_BATTERY_ALERT_HUE=359" in on
    assert "CONFIG_KEYMAP_BATTERY_ALERT_INTERVAL_S=120" in on


def test_generate_writes_module_files_and_valid_dts(api, tmp_path) -> None:
    client, state = api
    client.put(
        "/api/firmware/battery_alert",
        json={
            "enabled": True, "threshold_percent": 10, "blink_count": 3,
            "hue": 359, "saturation": 90, "brightness": 50, "interval_minutes": 2,
        },
    )
    client.post(
        "/api/firmware/macros",
        json={"macros": [_macro("to_layer_1_red", "&to 1, &rgb_ug RGB_COLOR_HSB(0,100,50)")]},
    )
    client.put(
        "/api/firmware/power",
        json={
            "idle_seconds": 42, "deep_sleep_enabled": True,
            "deep_sleep_minutes": 20, "rgb_off_when_idle": True,
            "rgb_off_when_unplugged": True,
        },
    )
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 200, response.text
    workspace = Path(state.paths.FIRMWARE_WORKSPACE_DIR)
    keymap_text = (workspace / "config" / "imprint.keymap").read_text("utf-8")
    # One behavior per macro_tap cell: no comma may appear inside a <...> cell.
    for line in keymap_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("= <", ", <")) and stripped.endswith((">;", ">")):
            cell = stripped[stripped.index("<") + 1 : stripped.rindex(">")]
            assert "," not in cell.replace("RGB_COLOR_HSB(0,100,50)", "HSB"), line
    assert "<&macro_tap &to 1>" in keymap_text
    assert "<&macro_tap &rgb_ug RGB_COLOR_HSB(0,100,50)>" in keymap_text
    conf_text = (workspace / "config" / "imprint.conf").read_text("utf-8")
    assert "CONFIG_KEYMAP_BATTERY_ALERT=y" in conf_text
    assert "CONFIG_ZMK_IDLE_TIMEOUT=42000" in conf_text
    assert "CONFIG_ZMK_IDLE_SLEEP_TIMEOUT=1200000" in conf_text
    assert "CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_USB=y" in conf_text
    assert (workspace / "zephyr" / "module.yml").is_file()
    assert (workspace / "Kconfig").is_file()
    source = (workspace / "src" / "battery_alert_blink.c").read_text("utf-8")
    assert "ZMK_SUBSCRIPTION(battery_alert_blink, zmk_battery_state_changed);" in source
    assert (workspace / "CMakeLists.txt").read_text("utf-8").startswith(
        "target_sources_ifdef(CONFIG_KEYMAP_BATTERY_ALERT"
    )
    wake_sync = (workspace / "src" / "underglow_wake_sync.c").read_text("utf-8")
    assert "ZMK_SUBSCRIPTION(underglow_wake_sync, zmk_activity_state_changed);" in wake_sync
    assert "RGB_ON_CMD" in wake_sync
    capture = (workspace / "src" / "capture_gatt.c").read_text("utf-8")
    assert "ZMK_SUBSCRIPTION(keymapper_capture, zmk_position_state_changed);" in capture
    assert "BT_GATT_PERM_READ_ENCRYPT" in capture
    assert "capture_gatt.c" in (workspace / "CMakeLists.txt").read_text("utf-8")


def test_capture_endpoint_reports_presses(api) -> None:
    client, state = api
    state.capture_reader = lambda: (7, 42, 0b110)
    body = client.get("/api/capture/press").json()
    assert body == {
        "supported": True, "counter": 7, "position": 42, "layers": 6,
    }

    # Old firmware without layer reporting: two-element readings tolerated.
    state.capture_reader = lambda: (8, 41)
    body = client.get("/api/capture/press").json()
    assert body["position"] == 41 and body["layers"] is None

    state.capture_reader = lambda: None
    body = client.get("/api/capture/press").json()
    assert body["supported"] is False and "capture service" in body["detail"]


def test_power_endpoints_and_persistence(api) -> None:
    client, state = api
    default = client.get("/api/firmware/power").json()
    assert default["idle_seconds"] == 30
    assert default["deep_sleep_enabled"] is True
    assert default["deep_sleep_minutes"] == 15

    update = {
        "idle_seconds": 60,
        "deep_sleep_enabled": False,
        "deep_sleep_minutes": 30,
        "rgb_off_when_idle": True,
        "rgb_off_when_unplugged": True,
    }
    assert client.put("/api/firmware/power", json=update).status_code == 200
    assert client.get("/api/firmware/power").json()["idle_seconds"] == 60
    persisted = json.loads(Path(state.paths.POWER_FILE).read_text("utf-8"))
    assert persisted["deep_sleep_enabled"] is False

    bad = dict(update, idle_seconds=1)
    assert client.put("/api/firmware/power", json=bad).status_code == 422

    # Deep sleep firing before idle is rejected by the cross-field validator.
    preempt = dict(update, deep_sleep_enabled=True, idle_seconds=7200,
                   deep_sleep_minutes=1)
    assert client.put("/api/firmware/power", json=preempt).status_code == 422

    # Restart round trip: a fresh load must read the saved settings back.
    from services import load_staged_state

    state.power = PowerConfig()
    load_staged_state(state)
    assert state.power.idle_seconds == 60
    assert state.power.deep_sleep_enabled is False


def test_render_conf_power_lines() -> None:
    conf = render_conf(
        [],
        slack=0,
        power=PowerConfig(
            idle_seconds=45,
            deep_sleep_enabled=True,
            deep_sleep_minutes=20,
            rgb_off_when_idle=True,
            rgb_off_when_unplugged=False,
        ),
    )
    assert "CONFIG_ZMK_IDLE_TIMEOUT=45000" in conf
    assert "CONFIG_ZMK_SLEEP=y" in conf
    assert "CONFIG_ZMK_IDLE_SLEEP_TIMEOUT=1200000" in conf
    assert "CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_IDLE=y" in conf
    assert "CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_USB=n" in conf

    off = render_conf([], slack=0, power=PowerConfig(deep_sleep_enabled=False))
    assert "CONFIG_ZMK_SLEEP=n" in off
    assert "CONFIG_ZMK_IDLE_SLEEP_TIMEOUT" not in off


def test_locking_endpoints_and_persistence(api) -> None:
    client, state = api
    default = client.get("/api/firmware/locking").json()
    assert default == {"studio_locking_enabled": False}

    update = {"studio_locking_enabled": True}
    assert client.put("/api/firmware/locking", json=update).status_code == 200
    assert client.get("/api/firmware/locking").json()["studio_locking_enabled"] is True
    persisted = json.loads(Path(state.paths.LOCKING_FILE).read_text("utf-8"))
    assert persisted["studio_locking_enabled"] is True


def test_render_conf_locking_line() -> None:
    from entities import LockingConfig

    assert "CONFIG_ZMK_STUDIO_LOCKING=n" in render_conf([], slack=0)
    assert "CONFIG_ZMK_STUDIO_LOCKING=n" in render_conf(
        [], slack=0, locking=LockingConfig(studio_locking_enabled=False)
    )
    assert "CONFIG_ZMK_STUDIO_LOCKING=y" in render_conf(
        [], slack=0, locking=LockingConfig(studio_locking_enabled=True)
    )


def test_speed_to_scaler_picks_close_small_fractions() -> None:
    from services import speed_to_scaler

    assert speed_to_scaler(100) == (1, 1)
    assert speed_to_scaler(33) == (1, 3)
    assert speed_to_scaler(250) == (5, 2)
    assert speed_to_scaler(150) == (3, 2)
    mul, div = speed_to_scaler(6)
    assert 1 <= mul <= 16 and 1 <= div <= 16
    assert abs(mul / div - 0.06) < 0.01
    assert speed_to_scaler(1600) == (16, 1)


def test_trackball_endpoints_and_persistence(api) -> None:
    client, state = api
    default = client.get("/api/firmware/trackballs").json()
    assert default["left"]["mode"] == "scroll_vertical"
    assert default["right"]["mode"] == "mouse"
    assert default["responsiveness_ms"] == 8

    update = {
        "left": {
            "installed": True,
            "mode": "mouse",
            "speed_percent": 150,
            "natural_direction": False,
        },
        "right": {
            "installed": True,
            "mode": "scroll_horizontal",
            "speed_percent": 25,
            "natural_direction": True,
        },
        "responsiveness_ms": 4,
    }
    assert client.put("/api/firmware/trackballs", json=update).status_code == 200
    stored = client.get("/api/firmware/trackballs").json()
    assert stored["right"]["mode"] == "scroll_horizontal"
    persisted = json.loads(Path(state.paths.TRACKBALLS_FILE).read_text("utf-8"))
    assert persisted["responsiveness_ms"] == 4

    bad = dict(update, responsiveness_ms=999)
    assert client.put("/api/firmware/trackballs", json=bad).status_code == 422
    bad_speed = dict(update)
    bad_speed["left"] = dict(update["left"], speed_percent=5)
    assert client.put("/api/firmware/trackballs", json=bad_speed).status_code == 422


def test_render_conf_trackball_interval() -> None:
    from entities import TrackballConfig

    conf = render_conf([], slack=0, trackballs=TrackballConfig(responsiveness_ms=4))
    assert "CONFIG_PMW3610_REPORT_INTERVAL_MIN=4" in conf
    assert "CONFIG_PMW3610_REPORT_INTERVAL_MIN" not in render_conf([], slack=0)


def test_trackballs_survive_restart_via_staged_state(api) -> None:
    from entities import TrackballConfig
    from services import load_staged_state

    client, state = api
    update = {
        "left": {
            "installed": False,
            "mode": "disabled",
            "speed_percent": 100,
            "natural_direction": False,
        },
        "right": {
            "installed": True,
            "mode": "scroll_vertical",
            "speed_percent": 50,
            "natural_direction": False,
        },
        "responsiveness_ms": 16,
    }
    assert client.put("/api/firmware/trackballs", json=update).status_code == 200
    state.trackballs = TrackballConfig()
    load_staged_state(state)
    assert state.trackballs.left.installed is False
    assert state.trackballs.right.mode.value == "scroll_vertical"
    assert state.trackballs.responsiveness_ms == 16


def test_display_names_with_quotes_and_backslashes_are_sanitized(api) -> None:
    client, state = api
    raw_name = 'Open: "D:' + chr(92) + 'Apps' + chr(92) + 'Code.exe"'
    launcher = {
        "node_name": "vs_code",
        "display_name": raw_name,
        "wait_ms": 25,
        "tap_ms": 15,
        "steps": [
            {"kind": "tap", "binding": "&kp LG(R)"},
            {"kind": "wait_ms", "value": 500},
            {"kind": "tap", "binding": "&kp RET"},
        ],
    }
    response = client.post("/api/firmware/macros", json={"macros": [launcher]})
    assert response.status_code == 200, response.text
    # Staged names are normalized immediately: Zephyr's build system copies
    # devicetree strings into CMake without escaping, so backslashes and
    # double quotes must never reach the firmware at all.
    staged = client.get("/api/firmware/macros").json()
    assert staged[0]["display_name"] == "Open: 'D:/Apps/Code.exe'"
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 200, response.text
    workspace = Path(state.paths.FIRMWARE_WORKSPACE_DIR)
    keymap_text = (workspace / "config" / "imprint.keymap").read_text("utf-8")
    assert "display-name = \"Open: 'D:/Apps/Code.exe'\";" in keymap_text
    for line in keymap_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("display-name = "):
            inner = stripped[len("display-name = ") + 1 : -2]
            assert chr(34) not in inner and chr(92) not in inner, line
    assert (workspace / "dts" / "bindings" / "vendor-prefixes.txt").is_file()
def test_render_trackball_dts_runtime_wiring() -> None:
    from entities import TrackballConfig, TrackballMode, TrackballSideConfig
    from services import render_trackball_dts

    default = render_trackball_dts(TrackballConfig())
    assert (
        "&trackball_central_listener {\n"
        "    input-processors = <&keymapper_tb 0>;\n"
        "};"
    ) in default
    assert (
        "&trackball_peripheral_listener {\n"
        "    input-processors = <&keymapper_tb 1>;\n"
        "};"
    ) in default

    # A DISABLED mode keeps the runtime wiring (so it can be re-enabled
    # live); only "not installed" compiles the listener out.
    off = render_trackball_dts(
        TrackballConfig(
            left=TrackballSideConfig(installed=False),
            right=TrackballSideConfig(mode=TrackballMode.DISABLED),
        )
    )
    assert '&trackball_central_listener { status = "disabled"; };' in off
    assert "Left trackball: not installed" in off
    assert "&trackball_peripheral_listener {\n    input-processors" in off


def test_trackball_config_payload_encoding() -> None:
    from entities import TrackballConfig, TrackballMode, TrackballSideConfig
    from services import trackball_config_payload

    payload = trackball_config_payload(TrackballConfig())
    # version, left: vscroll 1/3 natural, right: mouse 1/1 plain.
    assert payload == bytes([1, 1, 1, 3, 1, 0, 1, 1, 0])

    payload = trackball_config_payload(
        TrackballConfig(
            left=TrackballSideConfig(installed=False),
            right=TrackballSideConfig(
                mode=TrackballMode.SCROLL_HORIZONTAL,
                speed_percent=25,
                natural_direction=True,
            ),
        )
    )
    # Uninstalled encodes as DISABLED (3); horizontal is mode 2 at 1/4.
    assert payload == bytes([1, 3, 1, 1, 0, 2, 1, 4, 1])


def test_generate_bakes_staged_trackball_settings(api) -> None:
    client, state = api
    update = {
        "left": {
            "installed": True,
            "mode": "mouse",
            "speed_percent": 850,
            "natural_direction": True,
        },
        "right": {
            "installed": True,
            "mode": "scroll_horizontal",
            "speed_percent": 25,
            "natural_direction": True,
        },
        "responsiveness_ms": 4,
    }
    state.trackball_writer = lambda payload: True
    assert client.put("/api/firmware/trackballs", json=update).status_code == 200
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 200, response.text
    workspace = Path(state.paths.FIRMWARE_WORKSPACE_DIR)
    keymap_text = (workspace / "config" / "imprint.keymap").read_text("utf-8")
    assert 'compatible = "keymapper,input-processor-trackball";' in keymap_text
    assert "input-processors = <&keymapper_tb 0>;" in keymap_text
    assert "input-processors = <&keymapper_tb 1>;" in keymap_text
    conf_text = (workspace / "config" / "imprint.conf").read_text("utf-8")
    assert "CONFIG_PMW3610_REPORT_INTERVAL_MIN=4" in conf_text
    assert "CONFIG_KEYMAP_TB_LEFT_MODE=0" in conf_text
    assert "CONFIG_KEYMAP_TB_LEFT_MUL=8" in conf_text
    assert "CONFIG_KEYMAP_TB_LEFT_DIV=1" in conf_text
    assert "CONFIG_KEYMAP_TB_RIGHT_MODE=2" in conf_text
    assert "CONFIG_KEYMAP_TB_RIGHT_MUL=1" in conf_text
    assert "CONFIG_KEYMAP_TB_RIGHT_DIV=4" in conf_text
    assert "CONFIG_KEYMAP_TB_RIGHT_FLAGS=1" in conf_text
    assert (workspace / "src" / "trackball_runtime.c").is_file()
    binding = (
        workspace
        / "dts"
        / "bindings"
        / "input_processors"
        / "keymapper,input-processor-trackball.yaml"
    )
    assert binding.is_file()


def test_trackball_put_reports_live_apply(api) -> None:
    client, state = api
    update = {
        "left": {
            "installed": True,
            "mode": "scroll_vertical",
            "speed_percent": 33,
            "natural_direction": True,
        },
        "right": {
            "installed": True,
            "mode": "mouse",
            "speed_percent": 100,
            "natural_direction": False,
        },
        "responsiveness_ms": 8,
    }
    written: list = []

    def writer(payload: bytes) -> bool:
        written.append(payload)
        return True

    state.trackball_writer = writer
    body = client.put("/api/firmware/trackballs", json=update).json()
    assert body["applied_live"] is True and body["detail"] is None
    assert written == [bytes([1, 1, 1, 3, 1, 0, 1, 1, 0])]

    state.trackball_writer = lambda payload: False
    body = client.put("/api/firmware/trackballs", json=update).json()
    assert body["applied_live"] is False
    assert "live trackball channel" in body["detail"]


def test_capture_watch_endpoint_with_injected_starter(api) -> None:
    client, state = api
    state.capture_watch_starter = lambda: True
    body = client.post("/api/capture/watch", json={"on": True}).json()
    assert body["supported"] is True

    state.capture_watch_starter = lambda: False
    body = client.post("/api/capture/watch", json={"on": True}).json()
    assert body["supported"] is False

    body = client.post("/api/capture/watch", json={"on": False}).json()
    assert body["supported"] is False and "stopped" in body["detail"]


def test_firmware_status_lists_uf2_files_from_disk_after_restart(api) -> None:
    client, state = api
    firmware_dir = (
        Path(state.paths.FIRMWARE_WORKSPACE_DIR) / "artifacts" / "firmware"
    )
    firmware_dir.mkdir(parents=True, exist_ok=True)
    (firmware_dir / "imprint_left-assimilator-bt-zmk.uf2").write_bytes(b"left")
    (firmware_dir / "imprint_right-assimilator-bt-zmk.uf2").write_bytes(b"right")
    # A fresh server has an empty in-memory job; the files must still show.
    state.firmware_job = {}
    job = client.get("/api/firmware/status").json()["job"]
    assert len(job["uf2_files"]) == 2
    assert job["phase"] == "done"
    # But never surface stale files while a new build is running.
    state.firmware_job = {"phase": "build", "detail": "", "error": None,
                          "uf2_files": []}
    job = client.get("/api/firmware/status").json()["job"]
    assert job["uf2_files"] == []


def test_resync_trackballs_uses_injected_writer(api) -> None:
    import asyncio

    from services import resync_trackballs, trackball_config_payload

    client, state = api
    written: list = []
    state.trackball_writer = lambda payload: (written.append(payload), True)[1]
    assert asyncio.run(resync_trackballs(state)) is True
    assert written == [trackball_config_payload(state.trackballs)]

    def failing(_payload: bytes) -> bool:
        raise RuntimeError("radio gone")

    state.trackball_writer = failing
    # Opportunistic: failures are swallowed and reported as not-applied.
    assert asyncio.run(resync_trackballs(state)) is False


def test_render_conf_trackball_wake_timing() -> None:
    from entities import TrackballConfig

    stock = render_conf([], slack=0, trackballs=TrackballConfig())
    assert "REST1_SAMPLE_TIME_MS" not in stock
    assert "RUN_DOWNSHIFT_TIME_MS" not in stock

    tuned = render_conf(
        [],
        slack=0,
        trackballs=TrackballConfig(
            wake_check_ms=14, awake_after_motion_ms=1000
        ),
    )
    # Rounded to the driver granularities: tens and 32s.
    assert "CONFIG_PMW3610_REST1_SAMPLE_TIME_MS=10" in tuned
    assert "CONFIG_PMW3610_REST2_SAMPLE_TIME_MS=10" in tuned
    assert "CONFIG_PMW3610_REST3_SAMPLE_TIME_MS=10" in tuned
    assert "CONFIG_PMW3610_RUN_DOWNSHIFT_TIME_MS=992" in tuned


def test_half_overlays_carry_force_awake(api) -> None:
    from entities import TrackballConfig
    from services import render_half_overlay

    off = render_half_overlay(TrackballConfig(), left=True)
    assert "force-awake" not in off

    on_left = render_half_overlay(TrackballConfig(force_awake=True), left=True)
    assert "&spi1 {" in on_left
    assert "trackball_central@0 {" in on_left
    assert "force-awake;" in on_left
    # Shield labels are undefined at this overlay's parse point (it is
    # appended before the shield's own overlay) - never reference them.
    assert "&trackball_central" not in on_left
    on_right = render_half_overlay(
        TrackballConfig(force_awake=True), left=False)
    assert "trackball_peripheral@0 {" in on_right
    assert "&trackball_peripheral" not in on_right

    client, state = api
    update = client.get("/api/firmware/trackballs").json()
    update.pop("applied_live", None)
    update.pop("detail", None)
    update["force_awake"] = True
    state.trackball_writer = lambda payload: True
    assert client.put("/api/firmware/trackballs", json=update).status_code == 200
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 200, response.text
    workspace = Path(state.paths.FIRMWARE_WORKSPACE_DIR)
    left_overlay = (workspace / "config" / "imprint_left.overlay").read_text("utf-8")
    assert "force-awake" in left_overlay


def test_battcheck_conf_keymap_and_endpoint(api) -> None:
    from entities import FirmwareTemplates

    # Duration renders whenever battery settings exist, even with the
    # low-battery alert disabled (BattCheck is an independent feature).
    conf = render_conf(
        [], slack=0, battery_alert=BatteryAlertConfig(enabled=False))
    assert "CONFIG_KEYMAP_BATTCHECK_MS=2000" in conf
    tuned = render_conf(
        [],
        slack=0,
        battery_alert=BatteryAlertConfig(enabled=False, battcheck_ms=5000),
    )
    assert "CONFIG_KEYMAP_BATTCHECK_MS=5000" in tuned

    # The behavior node ships in the keymap and the module wiring is present.
    assert 'display-name = "BattCheck"' in FirmwareTemplates.RGB_REMEMBER_NODE
    assert "src/battcheck.c" in FirmwareTemplates.MODULE_CMAKE
    assert "KEYMAP_BATTCHECK_MS" in FirmwareTemplates.MODULE_KCONFIG
    assert "zmk_battery_state_of_charge" in FirmwareTemplates.BATTCHECK_SOURCE
    assert "BEHAVIOR_LOCALITY_GLOBAL" in FirmwareTemplates.BATTCHECK_SOURCE

    client, state = api
    current = client.get("/api/firmware/battery_alert").json()
    assert current["battcheck_ms"] == 2000
    current["battcheck_ms"] = 3000
    assert client.put(
        "/api/firmware/battery_alert", json=current).status_code == 200
    assert client.get(
        "/api/firmware/battery_alert").json()["battcheck_ms"] == 3000

    # Generate materializes the source and binding into the workspace.
    response = client.post("/api/firmware/generate", json={"confirm": True})
    assert response.status_code == 200, response.text
    workspace = Path(state.paths.FIRMWARE_WORKSPACE_DIR)
    assert (workspace / "src" / "battcheck.c").is_file()
    binding = (
        workspace / "dts" / "bindings" / "behaviors"
        / "zmk,behavior-battcheck.yaml"
    )
    assert binding.is_file()
    keymap = (workspace / "config" / "imprint.keymap").read_text("utf-8")
    # Node name <= 8 chars: the BLE split relay truncates longer behavior
    # names, and the peripheral half then never runs the behavior at all.
    assert "batt_chk: batt_chk" in keymap
    assert "batt_check: batt_check" not in keymap
    conf_text = (workspace / "config" / "imprint.conf").read_text("utf-8")
    assert "CONFIG_KEYMAP_BATTCHECK_MS=3000" in conf_text


def test_battcheck_duration_clamped_below_idle_timeout() -> None:
    # A glow spanning the idle transition would latch its forced-on state
    # into ZMK's underglow auto-off bookkeeping, so the generator caps it.
    conf = render_conf(
        [],
        slack=0,
        battery_alert=BatteryAlertConfig(enabled=False, battcheck_ms=60000),
        power=PowerConfig(idle_seconds=30),
    )
    assert "CONFIG_KEYMAP_BATTCHECK_MS=29500" in conf


def test_sequence_brightness_accepts_zero(api) -> None:
    client, state = api
    response = client.post(
        "/api/firmware/sequence_brightness", json={"brightness": 0})
    assert response.status_code == 200, response.text


def test_zero_param_behaviors_declare_empty_metadata() -> None:
    # Studio's set-binding RPC errors with INVALID_PARAMETERS for behaviors
    # without parameter metadata, so every 0-param behavior must declare the
    # empty metadata or it can never be assigned to a key from the app.
    from entities import FirmwareTemplates

    for source in (
        FirmwareTemplates.BATTCHECK_SOURCE,
        FirmwareTemplates.RGB_REMEMBER_SOURCE,
        FirmwareTemplates.NUMLOCK_GUARD_SOURCE,
    ):
        assert "zmk_behavior_get_empty_param_metadata" in source


def test_fake_mode_never_touches_real_bluetooth(api, monkeypatch) -> None:
    # Regression: trackball PUT/resync used to fall back to scanning for
    # the REAL keyboard when no writer was injected, writing test configs
    # onto the user's actual trackballs during every pytest run.
    import services as services_module

    def _forbidden(*args, **kwargs):
        raise AssertionError("real Bluetooth reached from fake mode")

    monkeypatch.setattr(services_module, "find_ble_address", _forbidden)
    monkeypatch.setattr(services_module, "write_trackball_config", _forbidden)

    client, state = api
    state.trackball_writer = None
    assert state.fake_device is not None
    update = client.get("/api/firmware/trackballs").json()
    update.pop("applied_live", None)
    update.pop("detail", None)
    response = client.put("/api/firmware/trackballs", json=update)
    assert response.status_code == 200, response.text
    assert response.json()["applied_live"] is False

    import asyncio

    from services import resync_trackballs

    assert asyncio.run(resync_trackballs(state)) is False


def test_ble_backend_defaults_and_override(monkeypatch) -> None:
    import os as os_module

    from services import ble_backend

    monkeypatch.delenv("KEYMAPPER_BLE_BACKEND", raising=False)
    expected = "winrt" if os_module.name == "nt" else "bleak"
    assert ble_backend() == expected
    monkeypatch.setenv("KEYMAPPER_BLE_BACKEND", "bleak")
    assert ble_backend() == "bleak"
    monkeypatch.setenv("KEYMAPPER_BLE_BACKEND", "winrt")
    assert ble_backend() == "winrt"
    monkeypatch.setenv("KEYMAPPER_BLE_BACKEND", "nonsense")
    assert ble_backend() == expected


def test_ble_dispatch_routes_to_selected_backend(monkeypatch) -> None:
    import asyncio

    import services as services_module

    calls = []

    async def fake_bleak_battery(address):
        calls.append(("bleak", address))
        return [55]

    async def fake_winrt_battery(address):
        calls.append(("winrt", address))
        return [66]

    monkeypatch.setattr(
        services_module, "_bleak_read_battery_levels", fake_bleak_battery)
    monkeypatch.setattr(
        services_module, "_winrt_read_battery_levels", fake_winrt_battery)

    monkeypatch.setenv("KEYMAPPER_BLE_BACKEND", "bleak")
    assert asyncio.run(services_module.read_battery_levels("AA")) == [55]
    monkeypatch.setenv("KEYMAPPER_BLE_BACKEND", "winrt")
    assert asyncio.run(services_module.read_battery_levels("AA")) == [66]
    assert calls == [("bleak", "AA"), ("winrt", "AA")]


def test_parse_capture_payload_forms() -> None:
    from services import parse_capture_payload

    assert parse_capture_payload(b"\x01\x02") is None
    six = (7).to_bytes(4, "little") + (41).to_bytes(2, "little")
    assert parse_capture_payload(six) == (7, 41, None)
    ten = six + (0b101).to_bytes(4, "little")
    assert parse_capture_payload(ten) == (7, 41, 0b101)


class _StubBleakChar:
    def __init__(self, uuid, properties=("read",)):
        self.uuid = uuid
        self.properties = list(properties)


class _StubBleakService:
    def __init__(self, uuid, chars):
        self.uuid = uuid
        self.characteristics = chars


class _StubBleakClient:
    def __init__(self, services, values=None):
        self.services = services
        self.values = values or {}
        self.written = []
        self.disconnected = False

    async def read_gatt_char(self, characteristic):
        return self.values[characteristic.uuid]

    async def write_gatt_char(self, characteristic, payload, response=False):
        self.written.append((characteristic.uuid, bytes(payload), response))

    async def disconnect(self):
        self.disconnected = True


def test_bleak_backend_logic_with_stub_client(monkeypatch) -> None:
    import asyncio

    import services as services_module
    from entities import (
        TRACKBALL_GATT_CHAR_UUID,
        TRACKBALL_GATT_SERVICE_UUID,
    )

    battery = _StubBleakService(
        services_module.BATTERY_SERVICE_UUID,
        [_StubBleakChar(services_module.BATTERY_LEVEL_CHAR_UUID)],
    )
    battery2 = _StubBleakService(
        services_module.BATTERY_SERVICE_UUID,
        [_StubBleakChar(services_module.BATTERY_LEVEL_CHAR_UUID)],
    )
    trackball = _StubBleakService(
        TRACKBALL_GATT_SERVICE_UUID,
        [_StubBleakChar(TRACKBALL_GATT_CHAR_UUID, ("read", "write"))],
    )
    # Both battery instances share a UUID: values keyed by uuid returns the
    # same byte, which is fine for asserting BOTH instances are read.
    client = _StubBleakClient(
        [battery, battery2, trackball],
        values={services_module.BATTERY_LEVEL_CHAR_UUID: bytes([77])},
    )

    async def fake_open(address, **kwargs):
        return client

    monkeypatch.setattr(services_module, "_bleak_open", fake_open)

    levels = asyncio.run(services_module._bleak_read_battery_levels("X"))
    assert levels == [77, 77]
    assert client.disconnected is True

    ok = asyncio.run(
        services_module._bleak_write_trackball_config("X", b"\x01payload"))
    assert ok is True
    assert client.written == [
        (TRACKBALL_GATT_CHAR_UUID, b"\x01payload", True)]

    # A firmware without the trackball service reports False, not an error.
    bare = _StubBleakClient([battery])

    async def fake_open_bare(address, **kwargs):
        return bare

    monkeypatch.setattr(services_module, "_bleak_open", fake_open_bare)
    assert asyncio.run(
        services_module._bleak_write_trackball_config("X", b"\x01")) is False
