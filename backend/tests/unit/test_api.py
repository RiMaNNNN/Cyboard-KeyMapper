"""API-layer tests over the fake device (no hardware, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

from entities import AppState, FakeImprint
from services import BACKUP_FILE_PREFIX, create_app


@pytest.fixture()
def api(app_state: AppState) -> Iterator[Tuple[TestClient, AppState]]:
    """TestClient bound to an app over the fake device (lifespan running)."""
    app = create_app(app_state)
    with TestClient(app) as client:
        yield client, app_state


def _unlock(state: AppState) -> None:
    """Unlock the fake keyboard and refresh the cached lock state."""
    assert state.fake_device is not None
    state.fake_device.press_unlock_combo()
    assert state.client is not None
    state.lock_state = state.client.get_lock_state()


def test_health_reports_fake_connection(api) -> None:
    client, _ = api
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "connected": True, "fake": True}


def test_state_snapshot_shape(api) -> None:
    client, _ = api
    body = client.get("/api/state").json()
    assert body["type"] == "state"
    assert body["connected"] is True
    assert body["device"]["name"] == "Imprint"
    assert body["lock_state"] == "LOCKED"


def test_secured_endpoint_maps_locked_to_423(api) -> None:
    client, _ = api
    response = client.get("/api/keymap")
    assert response.status_code == 423


def test_keymap_layouts_behaviors_after_unlock(api) -> None:
    client, state = api
    _unlock(state)
    keymap = client.get("/api/keymap").json()
    assert [l["name"] for l in keymap["layers"]][:2] == ["Base", "Numpad_Nav"]
    layouts = client.get("/api/layouts").json()
    assert layouts["active_index"] == 0
    assert len(layouts["layouts"]) == 2
    behaviors = client.get("/api/behaviors").json()
    names = {b["display_name"] for b in behaviors}
    assert {"Key Press", "To Layer", "Transparent", "None"} <= names


def test_put_binding_and_bulk_set(api) -> None:
    client, state = api
    _unlock(state)
    response = client.put(
        "/api/binding",
        json={"layer_id": 0, "key_position": 1, "behavior_id": FakeImprint.BEHAVIOR_TO_LAYER,
              "param1": 2, "param2": 0},
    )
    assert response.status_code == 200
    keymap = client.get("/api/keymap").json()
    assert keymap["layers"][0]["bindings"][1]["behavior_id"] == FakeImprint.BEHAVIOR_TO_LAYER

    response = client.post(
        "/api/bulk_set",
        json={"layer_id": 1, "behavior_id": FakeImprint.BEHAVIOR_TRANSPARENT},
    )
    assert response.json()["positions"] == FakeImprint.KEY_COUNT
    keymap = client.get("/api/keymap").json()
    assert all(
        b["behavior_id"] == FakeImprint.BEHAVIOR_TRANSPARENT
        for b in keymap["layers"][1]["bindings"]
    )


def test_bulk_set_unknown_layer_404(api) -> None:
    client, state = api
    _unlock(state)
    response = client.post("/api/bulk_set", json={"layer_id": 77, "behavior_id": 1})
    assert response.status_code == 404


def test_save_writes_backup_first(api) -> None:
    client, state = api
    _unlock(state)
    client.put(
        "/api/binding",
        json={"layer_id": 0, "key_position": 0, "behavior_id": FakeImprint.BEHAVIOR_NONE},
    )
    response = client.post("/api/save")
    assert response.status_code == 200
    backup_name = response.json()["backup"]
    assert backup_name.startswith(BACKUP_FILE_PREFIX)
    bundle = json.loads((Path(state.paths.BACKUPS_DIR) / backup_name).read_text("utf-8"))
    assert bundle["format"] == "keymap-backup-v1"
    assert bundle["device"]["name"] == "Imprint"
    assert len(bundle["keymap"]["layers"]) == 5
    assert len(bundle["behaviors"]) == len(state.fake_device.behaviors)


def test_backup_endpoint_and_listing(api) -> None:
    client, state = api
    _unlock(state)
    created = client.post("/api/backup").json()["backup"]
    listing = client.get("/api/backups").json()
    assert listing[0]["name"] == created
    content = client.get(f"/api/backups/{created}").json()
    assert content["format"] == "keymap-backup-v1"


def test_backup_content_rejects_traversal(api) -> None:
    client, state = api
    _unlock(state)
    assert client.get("/api/backups/..%5Csecrets.json").status_code == 404
    assert client.get("/api/backups/notaprefix.json").status_code == 404


def test_reset_settings_requires_confirmation_and_backs_up(api) -> None:
    client, state = api
    _unlock(state)
    assert client.post("/api/reset_settings", json={"confirm": False}).status_code == 400
    response = client.post("/api/reset_settings", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["backup"].startswith(BACKUP_FILE_PREFIX)


def test_layer_lifecycle_via_api(api) -> None:
    client, state = api
    _unlock(state)
    added = client.post("/api/layer/add").json()
    layer_id = added["layer"]["layer_id"]
    assert added["index"] == 5

    assert client.put(
        "/api/layer/name", json={"layer_id": layer_id, "name": "Symbols"}
    ).status_code == 200
    moved = client.post("/api/layer/move", json={"start_index": 5, "dest_index": 0}).json()
    assert moved["layers"][0]["layer_id"] == layer_id
    assert client.post("/api/layer/remove", json={"layer_index": 0}).status_code == 200
    restored = client.post(
        "/api/layer/restore", json={"layer_id": layer_id, "at_index": 2}
    ).json()
    assert restored["layer"]["name"] == "Symbols"


def test_websocket_pushes_state_and_lock_change(api) -> None:
    client, state = api
    with client.websocket_connect("/ws") as socket:
        first = socket.receive_json()
        assert first["type"] == "state"
        assert first["lock_state"] == "LOCKED"
        state.fake_device.press_unlock_combo()
        update = socket.receive_json()
        assert update["lock_state"] == "UNLOCKED"


def test_backup_pruning_respects_keep_last(api) -> None:
    client, state = api
    _unlock(state)
    for _ in range(7):
        assert client.post("/api/backup").status_code == 200
    remaining = list(Path(state.paths.BACKUPS_DIR).glob("*.json"))
    assert len(remaining) <= state.params.backup.keep_last
