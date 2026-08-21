"""Unit tests for StudioClient against the FakeImprint wire-protocol device."""

from __future__ import annotations

import time
from typing import Iterator, List, Tuple

import pytest

from entities import (
    Binding,
    ConnectionEvent,
    FakeImprint,
    LockState,
    LoopbackTransport,
    NotificationKind,
    StudioClient,
    StudioLockedError,
    StudioRpcError,
    StudioTimeoutError,
)


@pytest.fixture()
def rig() -> Iterator[Tuple[StudioClient, FakeImprint]]:
    """Connected (client, fake device) pair; device starts locked."""
    loop = LoopbackTransport()
    device = FakeImprint(loop.device_end, locked=True)
    client = StudioClient(loop.client_end, rpc_timeout_s=2.0)
    yield client, device
    client.close()
    device.stop()


def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    """Poll ``predicate`` until true or timeout; returns the final result."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_get_device_info_works_while_locked(rig) -> None:
    client, _ = rig
    info = client.get_device_info()
    assert info.name == "Imprint"
    assert info.serial_number != ""


def test_get_lock_state_reports_locked_then_unlocked(rig) -> None:
    client, device = rig
    assert client.get_lock_state() == LockState.LOCKED
    device.press_unlock_combo()
    assert _wait_for(lambda: client.get_lock_state() == LockState.UNLOCKED)


def test_secured_rpc_raises_locked_error(rig) -> None:
    client, _ = rig
    with pytest.raises(StudioLockedError):
        client.get_keymap()


def test_lock_notification_reaches_subscriber(rig) -> None:
    client, device = rig
    events: List[ConnectionEvent] = []
    client.subscribe(events.append)
    device.press_unlock_combo()
    assert _wait_for(lambda: any(e.kind == NotificationKind.LOCK_STATE_CHANGED for e in events))
    unlock_events = [e for e in events if e.kind == NotificationKind.LOCK_STATE_CHANGED]
    assert unlock_events[-1].value == LockState.UNLOCKED


def test_full_keymap_read(rig) -> None:
    client, device = rig
    device.press_unlock_combo()
    keymap = client.get_keymap()
    assert [l.name for l in keymap.layers][:2] == ["Base", "Numpad_Nav"]
    assert keymap.available_layers == FakeImprint.TOTAL_LAYERS - 5
    assert all(len(l.bindings) == FakeImprint.KEY_COUNT for l in keymap.layers)


def test_behavior_catalog_roundtrip(rig) -> None:
    client, device = rig
    device.press_unlock_combo()
    ids = client.list_behavior_ids()
    assert FakeImprint.BEHAVIOR_TO_LAYER in ids
    details = client.get_behavior_details(FakeImprint.BEHAVIOR_KEY_PRESS)
    assert details.display_name == "Key Press"
    assert details.param_sets[0][0][0].kind == "hid_usage"
    assert details.param_sets[0][0][0].keyboard_max == 0xFF
    layer_details = client.get_behavior_details(FakeImprint.BEHAVIOR_TO_LAYER)
    assert layer_details.param_sets[0][0][0].kind == "layer_id"


def test_set_binding_save_and_discard_cycle(rig) -> None:
    client, device = rig
    device.press_unlock_combo()
    target = Binding(FakeImprint.BEHAVIOR_TO_LAYER, 1, 0)

    client.set_layer_binding(layer_id=0, key_position=2, binding=target)
    assert client.check_unsaved_changes() is True
    assert client.get_keymap().layers[0].bindings[2].behavior_id == FakeImprint.BEHAVIOR_TO_LAYER

    client.discard_changes()
    assert client.check_unsaved_changes() is False
    assert client.get_keymap().layers[0].bindings[2].behavior_id == FakeImprint.BEHAVIOR_KEY_PRESS

    client.set_layer_binding(layer_id=0, key_position=2, binding=target)
    client.save_changes()
    assert client.check_unsaved_changes() is False
    assert device.saved_layers[0].bindings[2].behavior_id == FakeImprint.BEHAVIOR_TO_LAYER


def test_set_binding_rejects_bad_location_and_behavior(rig) -> None:
    client, device = rig
    device.press_unlock_combo()
    with pytest.raises(StudioRpcError) as excinfo:
        client.set_layer_binding(0, 999, Binding(FakeImprint.BEHAVIOR_NONE))
    assert "INVALID_LOCATION" in str(excinfo.value)
    with pytest.raises(StudioRpcError) as excinfo:
        client.set_layer_binding(0, 0, Binding(4242))
    assert "INVALID_BEHAVIOR" in str(excinfo.value)


def test_layer_lifecycle_add_rename_move_remove_restore(rig) -> None:
    client, device = rig
    device.press_unlock_combo()

    index, layer = client.add_layer()
    assert index == 5
    assert layer.name.startswith("extra")

    client.set_layer_props(layer.layer_id, "Symbols")
    assert client.get_keymap().layers[index].name == "Symbols"

    moved = client.move_layer(start_index=index, dest_index=0)
    assert moved.layers[0].layer_id == layer.layer_id

    client.remove_layer(layer_index=0)
    assert all(l.layer_id != layer.layer_id for l in client.get_keymap().layers)

    restored = client.restore_layer(layer_id=layer.layer_id, at_index=1)
    assert restored.name == "Symbols"
    assert client.get_keymap().layers[1].layer_id == layer.layer_id


def test_physical_layouts_and_switch(rig) -> None:
    client, device = rig
    device.press_unlock_combo()
    layouts = client.get_physical_layouts()
    assert layouts.active_index == 0
    assert [l.name for l in layouts.layouts] == ["Fake Full", "Fake Compact"]
    assert layouts.layouts[0].keys[1].x == 100

    keymap = client.set_active_physical_layout(1)
    assert len(keymap.layers) == 5
    assert client.get_physical_layouts().active_index == 1


def test_unsaved_changes_notification(rig) -> None:
    client, device = rig
    events: List[ConnectionEvent] = []
    client.subscribe(events.append)
    device.press_unlock_combo()
    client.set_layer_binding(0, 0, Binding(FakeImprint.BEHAVIOR_NONE))
    assert _wait_for(
        lambda: any(
            e.kind == NotificationKind.UNSAVED_CHANGES_CHANGED and e.value is True for e in events
        )
    )


def test_connection_lost_event_on_transport_death() -> None:
    """Transport death (USB unplug) emits CONNECTION_LOST and drops connected."""
    loop = LoopbackTransport()
    device = FakeImprint(loop.device_end, locked=True)
    client = StudioClient(loop.client_end, rpc_timeout_s=1.0)
    events: List[ConnectionEvent] = []
    client.subscribe(events.append)
    try:
        loop.client_end.close()
        assert _wait_for(
            lambda: any(e.kind == NotificationKind.CONNECTION_LOST for e in events)
        )
        assert client.connected is False
    finally:
        client.close()
        device.stop()


def test_rpc_times_out_when_device_never_answers() -> None:
    """A transport with no device on the other end raises StudioTimeoutError."""
    loop = LoopbackTransport()  # device_end intentionally unattended
    client = StudioClient(loop.client_end, rpc_timeout_s=0.2)
    try:
        with pytest.raises(StudioTimeoutError):
            client.get_device_info()
    finally:
        client.close()


def test_reset_settings_requires_unlock_then_resets(rig) -> None:
    client, device = rig
    with pytest.raises(StudioLockedError):
        client.reset_settings()
    device.press_unlock_combo()
    client.set_layer_binding(0, 0, Binding(FakeImprint.BEHAVIOR_NONE))
    assert client.reset_settings() is True
    assert client.check_unsaved_changes() is False
