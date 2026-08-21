"""Boundary tests for the pydantic Parameters models (config validation)."""

from __future__ import annotations

from typing import Any, Dict

import pytest
from pydantic import ValidationError

from entities import Parameters
from tests.conftest import build_parameters


def _valid_raw() -> Dict[str, Any]:
    """Return a fresh, fully valid raw config mapping (mutate per test)."""
    return {
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


def test_valid_config_accepted() -> None:
    params = Parameters.model_validate(_valid_raw())
    assert params.server.port == 8756
    assert params.device.rpc_timeout_s == 2.0
    assert params.backup.keep_last == 5
    assert params.firmware.behaviors_queue_slack == 16
    # The shared test fixture builder must stay valid too.
    assert build_parameters().server.host == "127.0.0.1"


@pytest.mark.parametrize(
    ("section", "key", "bad_value"),
    [
        ("server", "port", 1023),  # below 1024
        ("server", "port", 65536),  # above 65535
        ("server", "ui_disconnect_shutdown_s", 4.9),  # below 5
        ("server", "ui_disconnect_shutdown_s", 3601.0),  # above 3600
        ("server", "host", ""),  # min_length 1
        ("device", "usb_vid", -1),  # below 0
        ("device", "usb_vid", 0x10000),  # above 0xFFFF
        ("device", "usb_pid", 0x10000),  # above 0xFFFF
        ("device", "rpc_timeout_s", 60.1),  # above 60
        ("device", "rpc_timeout_s", 0.4),  # below 0.5
        ("device", "reconnect_interval_s", 0.4),  # below 0.5
        ("device", "reconnect_interval_s", 60.1),  # above 60
        ("backup", "keep_last", 0),  # below 1
        ("backup", "keep_last", 10001),  # above 10000
        ("firmware", "build_timeout_s", 59.9),  # below 60
        ("firmware", "build_timeout_s", 7201.0),  # above 7200
        ("firmware", "behaviors_queue_slack", -1),  # below 0
        ("firmware", "behaviors_queue_slack", 513),  # above 512
        ("firmware", "repo_name", ""),  # min_length 1
    ],
)
def test_out_of_range_values_rejected(section: str, key: str, bad_value: Any) -> None:
    raw = _valid_raw()
    raw[section][key] = bad_value
    with pytest.raises(ValidationError):
        Parameters.model_validate(raw)


def test_boundary_values_accepted() -> None:
    """The documented range endpoints themselves are valid."""
    raw = _valid_raw()
    raw["server"]["port"] = 1024
    raw["server"]["ui_disconnect_shutdown_s"] = 5.0
    raw["device"]["rpc_timeout_s"] = 60.0
    raw["device"]["reconnect_interval_s"] = 0.5
    raw["backup"]["keep_last"] = 1
    raw["firmware"]["build_timeout_s"] = 60.0
    raw["firmware"]["behaviors_queue_slack"] = 0
    params = Parameters.model_validate(raw)
    assert params.server.port == 1024
    assert params.backup.keep_last == 1

    raw["server"]["port"] = 65535
    raw["backup"]["keep_last"] = 10000
    raw["firmware"]["behaviors_queue_slack"] = 512
    params = Parameters.model_validate(raw)
    assert params.server.port == 65535
    assert params.firmware.behaviors_queue_slack == 512


def test_missing_section_rejected() -> None:
    raw = _valid_raw()
    del raw["backup"]
    with pytest.raises(ValidationError):
        Parameters.model_validate(raw)
