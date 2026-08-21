"""Tests for backup restore/diff/delete/download and layer-color endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

from entities import AppState, FakeImprint
from services import create_app


@pytest.fixture()
def api(app_state: AppState) -> Iterator[Tuple[TestClient, AppState]]:
    """TestClient over the unlocked fake device."""
    app_state.fake_device.press_unlock_combo()
    app = create_app(app_state)
    with TestClient(app) as client:
        app_state.lock_state = app_state.client.get_lock_state()
        yield client, app_state


def _macro(node: str, binding: str) -> dict:
    return {
        "node_name": node,
        "display_name": node,
        "steps": [{"kind": "tap", "binding": binding, "value": 0}],
        "wait_ms": 0,
        "tap_ms": 0,
    }


def test_layer_color_rewrites_matching_sequences(api) -> None:
    client, state = api
    client.post(
        "/api/firmware/macros",
        json={
            "macros": [
                _macro("to_layer_1_red", "&to 1, &rgb_ug RGB_COLOR_HSB(0,100,50)"),
                _macro("mom_layer_1_red", "&mo 1, &rgb_ug RGB_COLOR_HSB(0,100,50)"),
                _macro("to_layer_2_green", "&to 2, &rgb_ug RGB_COLOR_HSB(120,100,50)"),
            ]
        },
    )
    response = client.post(
        "/api/firmware/layer_color",
        json={"layer": 1, "hue": 300, "saturation": 80, "brightness": 40},
    )
    assert response.json() == {"ok": True, "updated": 2}
    bindings = [
        s.binding
        for m in state.pending_macros
        for s in m.steps
        if "RGB_COLOR_HSB" in s.binding
    ]
    assert bindings.count("&rgb_ug RGB_COLOR_HSB(300,80,40)") == 2
    assert "&rgb_ug RGB_COLOR_HSB(120,100,50)" in bindings  # layer 2 untouched


def test_presets_include_per_layer_sequences_with_staged_colors(api) -> None:
    client, _ = api
    client.post(
        "/api/firmware/macros",
        json={"macros": [_macro("to_layer_1_red", "&to 1, &rgb_ug RGB_COLOR_HSB(0,100,50)")]},
    )
    presets = client.get("/api/firmware/presets").json()
    names = {p["node_name"] for p in presets}
    # Static accents plus dynamic per-layer entries for the fake's 5 layers.
    assert "e_grave" in names
    assert {"to_layer_0", "mom_layer_0", "to_layer_4", "mom_layer_4"} <= names
    to_layer_1 = next(p for p in presets if p["node_name"] == "to_layer_1")
    joined = " ".join(s["binding"] for s in to_layer_1["steps"])
    # Mirrors the color the user already staged for layer 1.
    assert "RGB_COLOR_HSB(0,100,50)" in joined
    mom_1 = next(p for p in presets if p["node_name"] == "mom_layer_1")
    assert any("&rgb_mem" in s["binding"] for s in mom_1["steps"])


def test_backup_diff_and_restore_roundtrip(api) -> None:
    client, state = api
    # Snapshot the pristine keymap.
    backup_name = client.post("/api/backup").json()["backup"]

    # Mutate two keys, then diff against the snapshot.
    client.put(
        "/api/binding",
        json={"layer_id": 0, "key_position": 0,
              "behavior_id": FakeImprint.BEHAVIOR_NONE},
    )
    client.put(
        "/api/binding",
        json={"layer_id": 1, "key_position": 2,
              "behavior_id": FakeImprint.BEHAVIOR_TRANSPARENT},
    )
    diff = client.get(f"/api/backups/{backup_name}/diff").json()
    assert diff["identical"] is False
    assert len(diff["changed"]) == 2
    labels = {(c["layer"], c["position"]) for c in diff["changed"]}
    assert ("Base", 0) in labels and ("Numpad_Nav", 2) in labels

    # Restore: both keys revert (as pending edits) and the diff becomes clean.
    result = client.post(
        "/api/backups/restore", json={"name": backup_name, "confirm": True}
    ).json()
    assert result["applied"] == 2 and result["skipped_layers"] == []
    diff = client.get(f"/api/backups/{backup_name}/diff").json()
    assert diff["identical"] is True


def test_backup_delete_and_download(api) -> None:
    client, state = api
    name = client.post("/api/backup").json()["backup"]

    download = client.get(f"/api/backups/{name}/download")
    assert download.status_code == 200
    assert json.loads(download.content)["format"] == "keymap-backup-v1"
    assert "attachment" in download.headers.get("content-disposition", "")

    response = client.post(
        "/api/backups/delete", json={"names": [name, "evil\\path.json"], "confirm": True}
    )
    assert response.json() == {"ok": True, "deleted": 1}
    assert not (Path(state.paths.BACKUPS_DIR) / name).exists()
    assert client.post(
        "/api/backups/delete", json={"names": [name], "confirm": False}
    ).status_code == 400


def test_manual_route_serves_html(api, tmp_path) -> None:
    client, state = api
    assert client.get("/manual").status_code == 404
    Path(state.paths.MANUAL_FILE).write_text("<html>manual</html>", encoding="utf-8")
    response = client.get("/manual")
    assert response.status_code == 200
    assert "manual" in response.text
