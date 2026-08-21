"""Tests for the keymap/firmware generator (macros, DTS rendering, conf)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from entities import MacroDefinition, MacroStep, MacroStepKind
from services import (
    altcode_macro,
    binding_to_dts,
    compute_queue_size,
    keycode_param_to_dts,
    layer_color_macro,
    materialize_firmware_workspace,
    preset_macros,
    render_conf,
    render_keymap_dts,
    render_macro_dts,
    sanitize_dts_node_name,
)
from tests.conftest import build_parameters


def _kp(page: int, usage: int, mods: int = 0) -> int:
    """Compose a ZMK &kp parameter from page, usage, and modifier bits."""
    return (mods << 24) | (page << 16) | usage


def test_keycode_names_letters_digits_mods() -> None:
    assert keycode_param_to_dts(_kp(0x07, 0x04)) == "A"
    assert keycode_param_to_dts(_kp(0x07, 0x27)) == "N0"
    assert keycode_param_to_dts(_kp(0x07, 0x62)) == "KP_N0"
    assert keycode_param_to_dts(_kp(0x07, 0x04, mods=0x02)) == "LS(A)"
    assert keycode_param_to_dts(_kp(0x0C, 0xE9)) == "C_VOL_UP"


def test_keycode_unknown_usage_renders_hex() -> None:
    raw = _kp(0x07, 0x9999)
    assert keycode_param_to_dts(raw) == f"0x{raw:X}"


def test_binding_to_dts_common_behaviors() -> None:
    names = {1: "Key Press", 3: "To Layer", 5: "Transparent", 6: "None", 7: "Underglow"}
    assert binding_to_dts({"behavior_id": 1, "param1": _kp(0x07, 0x08)}, names) == ("&kp E", None)
    assert binding_to_dts({"behavior_id": 3, "param1": 2}, names) == ("&to 2", None)
    assert binding_to_dts({"behavior_id": 5}, names) == ("&trans", None)
    assert binding_to_dts({"behavior_id": 6}, names) == ("&none", None)
    dts, warning = binding_to_dts({"behavior_id": 7, "param1": 15, "param2": 3937500}, names)
    assert dts == "&rgb_ug 15 3937500"
    assert warning is None


def test_binding_to_dts_translates_stable_layer_ids_to_positions() -> None:
    """Wire layer IDs 0,2,1,3 at positions 0,1,2,3 must bake as positions."""
    names = {3: "To Layer", 2: "Momentary Layer", 8: "Layer-Tap"}
    index_by_id = {0: 0, 2: 1, 1: 2, 3: 3}
    assert binding_to_dts({"behavior_id": 3, "param1": 2}, names, index_by_id) == ("&to 1", None)
    assert binding_to_dts({"behavior_id": 2, "param1": 1}, names, index_by_id) == ("&mo 2", None)
    dts, warning = binding_to_dts(
        {"behavior_id": 8, "param1": 3, "param2": _kp(0x07, 0x2C)}, names, index_by_id
    )
    assert dts == "&lt 3 SPACE"
    assert warning is None
    dts, warning = binding_to_dts({"behavior_id": 3, "param1": 9}, names, index_by_id)
    assert dts == "&to 9"
    assert "not in the baked layer set" in warning


def test_binding_to_dts_unknown_behavior_warns_and_falls_back() -> None:
    dts, warning = binding_to_dts({"behavior_id": 99, "param1": 1}, {99: "Weird Thing"})
    assert dts == "&trans"
    assert "Weird Thing" in warning


def test_altcode_macro_egrave_golden() -> None:
    macro = altcode_macro("e_grave", "è (Alt+0232)", "0232")
    rendered = render_macro_dts(macro)
    assert 'compatible = "zmk,behavior-macro";' in rendered
    assert "<&macro_press &kp LALT>" in rendered
    assert (
        "<&macro_tap &kp KP_N0>\n                , <&macro_tap &kp KP_N2>\n"
        "                , <&macro_tap &kp KP_N3>\n                , <&macro_tap &kp KP_N2>"
        in rendered
    )
    assert "<&macro_release &kp LALT>" in rendered
    assert "wait-ms = <30>;" in rendered
    assert "tap-ms = <30>;" in rendered


def test_layer_color_macro_golden() -> None:
    macro = layer_color_macro("to1_yellow", "Layer 1 + Yellow", 1, 60, 100, 100)
    rendered = render_macro_dts(macro)
    assert "<&macro_tap &to 1>" in rendered
    assert "<&macro_tap &rgb_ug RGB_COLOR_HSB(60,100,100)>" in rendered
    assert "wait-ms = <0>;" in rendered


def test_preset_macros_cover_requested_characters() -> None:
    presets = {m.node_name: m for m in preset_macros()}
    assert set(presets) == {
        "euro_sign", "e_grave", "e_acute", "a_grave", "i_grave", "u_grave", "o_grave",
    }
    euro_taps = [s.binding for s in presets["euro_sign"].steps if s.kind == MacroStepKind.TAP]
    assert euro_taps == ["&kp KP_N0", "&kp KP_N1", "&kp KP_N2", "&kp KP_N8"]
    assert presets["euro_sign"].shifted_steps == []
    # Accent presets are Shift pairs: à on plain press, À (Alt+0192) shifted.
    a_pair = presets["a_grave"]
    lower_taps = [s.binding for s in a_pair.steps if s.kind == MacroStepKind.TAP]
    upper_taps = [s.binding for s in a_pair.shifted_steps if s.kind == MacroStepKind.TAP]
    assert lower_taps == ["&kp KP_N0", "&kp KP_N2", "&kp KP_N2", "&kp KP_N4"]
    assert upper_taps == ["&kp KP_N0", "&kp KP_N1", "&kp KP_N9", "&kp KP_N2"]


def test_mo_layer_color_macro_wraps_in_rgb_mem() -> None:
    from services import mo_layer_color_macro

    macro = mo_layer_color_macro("mo1_red", "Hold layer 1 + Red", 1, 0, 100, 50)
    rendered = render_macro_dts(macro)
    lines = [l.strip() for l in rendered.splitlines() if "<&" in l]
    assert lines == [
        "= <&macro_press &rgb_mem>",
        ", <&macro_press &mo 1>",
        ", <&macro_tap &rgb_ug RGB_COLOR_HSB(0,100,50)>",
        ", <&macro_pause_for_release>",
        ", <&macro_release &mo 1>",
        ", <&macro_release &rgb_mem>;",
    ]
    assert "wait-ms = <0>;" in rendered


def test_keymap_includes_rgb_mem_behavior_node() -> None:
    source, _ = render_keymap_dts(_sample_backup(), [])
    assert 'compatible = "zmk,behavior-rgb-remember";' in source
    assert "rgb_mem: rgb_mem {" in source


def test_render_shift_pair_emits_mod_morph() -> None:
    from services import altcode_pair

    rendered = render_macro_dts(altcode_pair("a_grave", "à / À", "0224", "0192"))
    assert "a_grave_l: a_grave_l {" in rendered
    assert "a_grave_u: a_grave_u {" in rendered
    assert 'compatible = "zmk,behavior-mod-morph";' in rendered
    assert "bindings = <&a_grave_l>, <&a_grave_u>;" in rendered
    assert "mods = <(MOD_LSFT|MOD_RSFT)>;" in rendered
    # The pair node keeps the original name so existing assignments survive.
    assert "a_grave: a_grave {" in rendered
    # The shifted variant must run synchronously inside the morph's Shift-mask
    # window: zero pacing, regardless of the pair's configured timing.
    upper = rendered[rendered.index("a_grave_u: a_grave_u {"):]
    upper = upper[: upper.index("};")]
    assert "wait-ms = <0>;" in upper
    assert "tap-ms = <0>;" in upper
    # The plain variant keeps the configured pacing.
    lower = rendered[rendered.index("a_grave_l: a_grave_l {"):]
    lower = lower[: lower.index("};")]
    assert "wait-ms = <30>;" in lower


def test_keymap_render_maps_compiled_sequence_bindings() -> None:
    """Keys bound to a user sequence must bake as that macro, not Transparent."""
    backup = _sample_backup()
    backup["behaviors"].append(
        {"behavior_id": 77, "display_name": "Layer 1 + Red", "param_sets": []}
    )
    backup["keymap"]["layers"][0]["bindings"][2] = {
        "behavior_id": 77, "param1": 0, "param2": 0,
    }
    macro = layer_color_macro("to_layer_1_red", "Layer 1 + Red", 1, 0, 100, 50)
    source, warnings = render_keymap_dts(backup, [macro])
    assert "&to_layer_1_red" in source
    assert not any("Layer 1 + Red" in w for w in warnings)


def test_queue_size_only_grows_when_needed() -> None:
    small = altcode_macro("e_grave", "è", "0232")
    assert compute_queue_size([small], slack=16) is None
    long_macro = MacroDefinition(
        node_name="huge",
        display_name="huge",
        steps=[MacroStep(MacroStepKind.TAP, "&kp A") for _ in range(40)],
    )
    assert compute_queue_size([long_macro], slack=16) == 96
    assert compute_queue_size([], slack=16) is None


def test_queue_size_sums_across_macros() -> None:
    """The behavior queue is shared: overlapping macro activations add up."""
    macros = [altcode_macro(f"m{i}", f"m{i}", "0232") for i in range(7)]
    per_macro = macros[0].queue_slots()
    assert compute_queue_size(macros, slack=0) == per_macro * 7
    assert compute_queue_size(macros[:3], slack=0) is None


def test_render_conf_contains_locking_off_and_queue() -> None:
    long_macro = MacroDefinition(
        node_name="huge",
        display_name="huge",
        steps=[MacroStep(MacroStepKind.TAP, "&kp A") for _ in range(40)],
    )
    conf = render_conf([long_macro], slack=16)
    assert "CONFIG_ZMK_RGB_UNDERGLOW=y" in conf
    assert "CONFIG_ZMK_STUDIO_LOCKING=n" in conf
    assert "CONFIG_ZMK_BEHAVIORS_QUEUE_SIZE=96" in conf
    assert "CONFIG_ZMK_BEHAVIORS_QUEUE_SIZE" not in render_conf([], slack=16)


def test_sanitize_dts_node_name() -> None:
    assert sanitize_dts_node_name("Numpad/Nav Layer") == "numpad_nav_layer"
    assert sanitize_dts_node_name("123 go") == "layer_123_go"
    assert sanitize_dts_node_name("///") == "layer"


def _sample_backup() -> Dict[str, Any]:
    """Backup bundle shaped like the fake device's output."""
    return {
        "format": "keymap-backup-v1",
        "created_utc": "2026-08-19T12:00:00+00:00",
        "device": {"name": "Imprint", "serial_number": "aa"},
        "physical_layouts": {
            "active_index": 0,
            "layouts": [
                {"name": "Imprint Function Row (5-Key Bottom Row)", "keys": []},
                {"name": "Imprint Function Row (2-Key Bottom Row)", "keys": []},
            ],
        },
        "keymap": {
            "layers": [
                {
                    "layer_id": 0,
                    "name": "Base",
                    "bindings": [
                        {"behavior_id": 1, "param1": _kp(0x07, 0x04), "param2": 0},
                        {"behavior_id": 3, "param1": 1, "param2": 0},
                        {"behavior_id": 5, "param1": 0, "param2": 0},
                    ],
                },
                {
                    "layer_id": 1,
                    "name": "Símbolos!",
                    "bindings": [
                        {"behavior_id": 6, "param1": 0, "param2": 0},
                        {"behavior_id": 42, "param1": 7, "param2": 0},
                        {"behavior_id": 1, "param1": _kp(0x07, 0x1E), "param2": 0},
                    ],
                },
            ],
            "available_layers": 30,
            "max_layer_name_length": 20,
        },
        "behaviors": [
            {"behavior_id": 1, "display_name": "Key Press", "param_sets": []},
            {"behavior_id": 3, "display_name": "To Layer", "param_sets": []},
            {"behavior_id": 5, "display_name": "Transparent", "param_sets": []},
            {"behavior_id": 6, "display_name": "None", "param_sets": []},
            {"behavior_id": 42, "display_name": "Mystery", "param_sets": []},
        ],
    }


def test_render_keymap_dts_full_document() -> None:
    macros = [altcode_macro("e_grave", "è (Alt+0232)", "0232")]
    source, warnings = render_keymap_dts(_sample_backup(), macros)

    assert source.startswith("#include <input/processors.dtsi>")
    assert (
        "chosen { zmk,physical-layout = "
        "&physical_layout_imprint_function_row_full_bottom_row; };" in source
    )
    assert "e_grave: e_grave {" in source
    assert 'display-name = "Base";' in source
    assert 'display-name = "Símbolos!";' in source
    assert "&kp A  &to 1  &trans" in source
    assert "&trackball_central_listener" in source
    assert source.count('status = "reserved";') == 30
    assert len(warnings) == 1 and "Mystery" in warnings[0]


def test_render_keymap_unknown_layout_warns() -> None:
    backup = _sample_backup()
    backup["physical_layouts"]["layouts"][0]["name"] = "Custom Thing"
    source, warnings = render_keymap_dts(backup, [])
    assert "&physical_layout_imprint_function_row_full_bottom_row" in source
    assert any("not recognized" in w for w in warnings)


def test_materialize_firmware_workspace(tmp_path: Path) -> None:
    result = materialize_firmware_workspace(
        tmp_path, _sample_backup(), preset_macros(), build_parameters()
    )
    keymap_path = tmp_path / "config" / "imprint.keymap"
    conf_path = tmp_path / "config" / "imprint.conf"
    notes_path = tmp_path / "KEYMAP_NOTES.md"
    assert keymap_path.is_file() and conf_path.is_file() and notes_path.is_file()
    assert {str(keymap_path), str(conf_path), str(notes_path)} <= set(result["files"])
    assert (tmp_path / "zephyr" / "module.yml").is_file()
    assert (tmp_path / "src" / "battery_alert_blink.c").is_file()
    assert "CONFIG_ZMK_STUDIO_LOCKING=n" in conf_path.read_text("utf-8")
    keymap_text = keymap_path.read_text("utf-8")
    assert "euro_sign: euro_sign {" in keymap_text
    assert "o_grave: o_grave {" in keymap_text
    notes = notes_path.read_text("utf-8")
    assert "è (Alt+0232)" in notes
    assert "Mystery" in notes
