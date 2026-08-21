"""Services for the KeyMapper backend.

This module owns every top-level function, per the project coding rules: startup
(path resolution, configuration ingestion, logger construction), device discovery
and connection, backup management, and the FastAPI application factory serving the
REST + WebSocket API and the built web UI.

Third-party dependencies: ``fastapi``, ``pyserial`` (via ``serial.tools``),
``PyYAML``, ``python-dotenv`` (via ``init_core_paths``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set, Tuple

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from serial.tools import list_ports  # type: ignore[import-untyped]

from entities import (
    ApiBackupNames,
    ApiBackupRestore,
    ApiBrightness,
    ApiLayerColor,
    BACKUP_FILE_PREFIX,
    BACKUP_FILE_SUFFIX,
    BACKUP_TIMESTAMP_FORMAT,
    BOOTLOADER_BOARD_SIGNATURE,
    BOOTLOADER_MARKER_FILENAME,
    CAPTURE_GATT_CHAR_UUID,
    CAPTURE_GATT_SERVICE_UUID,
    FAKE_DEVICE_ENV_VAR,
    GIT_IDENTITY_EMAIL,
    GIT_IDENTITY_NAME,
    MACRO_TIMING_MAX_MS,
    PRE_FIRST_CLIENT_GRACE_MULTIPLIER,
    ApiBindingUpdate,
    ApiBulkSet,
    ApiConfirm,
    ApiFlashFile,
    ApiGenerate,
    ApiLayerMove,
    ApiLayerName,
    ApiLayerRemove,
    ApiLayerRestore,
    ApiMacros,
    AppState,
    BackupConfigKey,
    BatteryAlertConfig,
    LockingConfig,
    PowerConfig,
    TRACKBALL_CONFIG_VERSION,
    TRACKBALL_GATT_CHAR_UUID,
    TRACKBALL_GATT_SERVICE_UUID,
    TRACKBALL_RESYNC_INTERVAL_S,
    TrackballConfig,
    TrackballMode,
    TrackballSideConfig,
    Binding,
    ConfigSection,
    ConnectionEvent,
    DeviceConfigKey,
    DeviceParameters,
    DtsMaps,
    FakeImprint,
    FirmwareConfigKey,
    FirmwareTemplates,
    KeyMapperError,
    LockState,
    LoopbackTransport,
    MacroDefinition,
    MacroStep,
    MacroStepKind,
    NotificationKind,
    Parameters,
    SerialTransport,
    ServerConfigKey,
    StudioClient,
    StudioLockedError,
    StudioRpcError,
    StudioTimeoutError,
)
from init_core_paths import init_core_paths
from logger import Logger
from startup_error_log import startup_error_log


@startup_error_log()
def resolve_core_paths() -> SimpleNamespace:
    """Resolve every project path declared in the backend ``.env``.

    The ``.env`` sits next to the backend package (``backend/.env``); its entries
    are resolved against the backend directory, directories are created when
    missing, and the config file's parent is created without touching the file.

    Returns:
        Namespace whose attributes ``DATA_DIR``, ``CONFIG_FILE``, ``LOGS_DIR``,
        ``BACKUPS_DIR``, ``FIRMWARE_WORKSPACE_DIR``, ``WEB_DIR`` and ``PROTO_DIR``
        are absolute ``pathlib.Path`` objects, usable directly without
        re-wrapping.
    """
    backend_dir = Path(__file__).resolve().parents[1]
    return init_core_paths(
        env_file=backend_dir / ".env",
        base_dir=backend_dir,
        create_missing=True,
        file_keys=(
            "CONFIG_FILE",
            "MACROS_FILE",
            "BATTERY_ALERT_FILE",
            "POWER_FILE",
            "LOCKING_FILE",
            "TRACKBALLS_FILE",
            "MANUAL_FILE",
        ),
        require_all_exist=False,
    )


@startup_error_log()
def ingest_configuration(config_file: Path) -> Parameters:
    """Load and validate ``config.yaml`` into a typed :class:`Parameters`.

    This is the single validation boundary for user-tunable values; downstream
    code trusts the returned model without re-checking.

    Every top-level section and every key inside each section must belong to the
    vocabulary declared in :class:`ConfigSection` and the per-section key enums,
    so a typo in ``config.yaml`` fails loudly at startup instead of being
    silently ignored.

    Args:
        config_file: Absolute path to the YAML configuration file.

    Returns:
        The validated configuration.

    Raises:
        FileNotFoundError: When the configuration file is missing.
        ValueError: When the file contains an undeclared section or key.
        pydantic.ValidationError: When any value is out of range or mistyped.
    """
    with open(config_file, "r", encoding="utf-8") as handle:
        raw: Dict[str, Any] = yaml.safe_load(handle)
    declared_keys: Dict[str, set] = {
        ConfigSection.SERVER.value: {k.value for k in ServerConfigKey},
        ConfigSection.DEVICE.value: {k.value for k in DeviceConfigKey},
        ConfigSection.BACKUP.value: {k.value for k in BackupConfigKey},
        ConfigSection.FIRMWARE.value: {k.value for k in FirmwareConfigKey},
    }
    unknown_sections = set(raw) - set(declared_keys)
    if unknown_sections:
        raise ValueError(
            f"{config_file}: unknown configuration section(s): {sorted(unknown_sections)}"
        )
    for section, keys in declared_keys.items():
        unknown = set(raw.get(section, {})) - keys
        if unknown:
            raise ValueError(
                f"{config_file}: unknown key(s) in section '{section}': {sorted(unknown)}"
            )
    return Parameters.model_validate(raw)


@startup_error_log()
def init_logger(logs_dir: Path) -> Logger:
    """Construct the project logger writing daily files under ``logs_dir``.

    Args:
        logs_dir: Directory for daily log files (created on first write).

    Returns:
        A started :class:`Logger` whose listener runs on its own process.
    """
    return Logger.init_logger(log_dir_path=logs_dir, unit_id="KEYMAPPER")


def discover_studio_port(device_params: DeviceParameters) -> Optional[str]:
    """Find the COM port of the keyboard's Studio serial interface.

    Matches by USB VID/PID first; when several ports share the IDs, the
    ``product_hint`` substring (case-insensitive, against product and description
    text) picks among them.

    Args:
        device_params: Validated ``device`` configuration section.

    Returns:
        The COM port name (e.g. ``COM5``), or ``None`` when no match is present.
    """
    candidates = []
    for port in list_ports.comports():
        if port.vid == device_params.usb_vid and port.pid == device_params.usb_pid:
            candidates.append(port)
    if not candidates:
        return None
    if len(candidates) > 1:
        hint = device_params.product_hint.lower()
        hinted = [
            p
            for p in candidates
            if hint in ((p.product or "") + " " + (p.description or "")).lower()
        ]
        if hinted:
            candidates = hinted
    return str(candidates[0].device)


def connect_device(state: AppState) -> bool:
    """Try to connect to the physical keyboard and refresh the shared state.

    On success the client is stored on ``state``, its notifications are bridged
    into ``state.events``, and device identity plus lock state are read (both work
    while locked).

    Args:
        state: Shared application state to mutate.

    Returns:
        ``True`` when a keyboard is now connected, ``False`` otherwise.
    """
    port = discover_studio_port(state.params.device)
    if port is None:
        return False
    try:
        transport = SerialTransport(port)
    except Exception as exc:  # noqa: BLE001 - any failure means: log it, stay disconnected
        state.logger.warning(f"Could not open {port}: {exc}")
        return False
    client = StudioClient(transport, rpc_timeout_s=state.params.device.rpc_timeout_s)
    try:
        client.subscribe(state.events.put)
        info = client.get_device_info()
        lock = client.get_lock_state()
    except Exception as exc:  # noqa: BLE001 - release the port and reader thread, stay disconnected
        client.close()
        state.logger.warning(f"Device connection attempt on {port} failed: {exc}")
        return False
    state.client = client
    state.port_name = port
    state.device_info = info
    state.lock_state = lock
    state.logger.info(f"Connected to {info.name} on {port} (lock state: {lock.name})")
    return True


def start_fake_device(state: AppState) -> None:
    """Start the in-process fake keyboard and connect the client to it.

    Used in demo mode (``KEYMAPPER_FAKE_DEVICE=1``) and by end-to-end tests. The
    fake boots locked, exactly like stock firmware.

    Args:
        state: Shared application state to mutate.
    """
    loop = LoopbackTransport()
    state.fake_device = FakeImprint(loop.device_end, locked=True)
    client = StudioClient(loop.client_end, rpc_timeout_s=state.params.device.rpc_timeout_s)
    client.subscribe(state.events.put)
    state.client = client
    state.port_name = "FAKE"
    state.device_info = client.get_device_info()
    state.lock_state = client.get_lock_state()
    state.logger.info("Fake device started (demo mode)")


def snapshot_full_state(state: AppState) -> Dict[str, Any]:
    """Assemble the connection snapshot pushed to UI clients.

    Args:
        state: Shared application state.

    Returns:
        JSON-serializable mapping with connection, lock, unsaved, device identity,
        port, demo-mode flag, and last backup name.
    """
    return {
        "type": "state",
        "connected": state.client is not None and state.client.connected,
        "fake": state.fake_device is not None,
        "port": state.port_name,
        "device": state.device_info.to_dict() if state.device_info else None,
        "lock_state": state.lock_state.name if state.lock_state is not None else None,
        "unsaved_changes": state.unsaved_changes,
        "last_backup": state.last_backup_path.name if state.last_backup_path else None,
    }


def backup_keymap(state: AppState) -> Path:
    """Read everything from the keyboard and persist a timestamped backup bundle.

    The bundle contains device identity, physical layouts (with the active index),
    the full keymap, and the complete behaviors catalog with parameter metadata —
    enough to re-apply the layout binding-by-binding or bake it into firmware.
    After writing, old backups beyond ``backup.keep_last`` are pruned.

    Args:
        state: Shared application state with a connected, unlocked keyboard.

    Returns:
        Path of the newly written backup file.

    Raises:
        HTTPException: 409 when no keyboard is connected.
        StudioLockedError: When the keyboard is locked (maps to HTTP 423).
        StudioTimeoutError: When the keyboard stops answering (maps to HTTP 504).
    """
    client = state.client
    if client is None:
        raise HTTPException(status_code=409, detail="no keyboard connected")
    keymap = client.get_keymap()
    layouts = client.get_physical_layouts()
    behavior_ids = client.list_behavior_ids()
    behaviors = [client.get_behavior_details(b).to_dict() for b in behavior_ids]
    bundle: Dict[str, Any] = {
        "format": "keymap-backup-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "device": state.device_info.to_dict() if state.device_info else None,
        "port": state.port_name,
        "physical_layouts": layouts.to_dict(),
        "keymap": keymap.to_dict(),
        "behaviors": behaviors,
    }
    # Local machine time: the file name is what the user sees and matches
    # against their clock (the bundle's created_utc stays UTC internally).
    timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    path = state.paths.BACKUPS_DIR / f"{BACKUP_FILE_PREFIX}{timestamp}{BACKUP_FILE_SUFFIX}"
    path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    state.last_backup_path = path
    state.logger.info(f"Backup written: {path.name}")

    existing = sorted(
        state.paths.BACKUPS_DIR.glob(f"{BACKUP_FILE_PREFIX}*{BACKUP_FILE_SUFFIX}")
    )
    excess = len(existing) - state.params.backup.keep_last
    for stale in existing[: max(0, excess)]:
        stale.unlink()
    return path


def list_backups(backups_dir: Path) -> List[Dict[str, Any]]:
    """List available backup files, newest first.

    Args:
        backups_dir: Directory holding backup bundles.

    Returns:
        List of ``{"name", "created_utc", "size_bytes"}`` mappings.
    """
    entries: List[Dict[str, Any]] = []
    for path in sorted(
        backups_dir.glob(f"{BACKUP_FILE_PREFIX}*{BACKUP_FILE_SUFFIX}"), reverse=True
    ):
        stat = path.stat()
        entries.append(
            {
                "name": path.name,
                "created_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "size_bytes": stat.st_size,
            }
        )
    return entries


def load_backup(backups_dir: Path, name: str) -> Dict[str, Any]:
    """Load one backup bundle by file name.

    Args:
        backups_dir: Directory holding backup bundles.
        name: Bare file name previously returned by :func:`list_backups`; path
            separators are rejected to keep access inside the backups directory.

    Returns:
        The parsed backup bundle.

    Raises:
        HTTPException: 404 when the name is unknown or malformed.
    """
    if "/" in name or "\\" in name or not name.startswith(BACKUP_FILE_PREFIX):
        raise HTTPException(status_code=404, detail="unknown backup")
    path = backups_dir / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="unknown backup")
    return json.loads(path.read_text(encoding="utf-8"))


def keycode_param_to_dts(param: int) -> str:
    """Render a ``&kp``-style 32-bit parameter as devicetree source text.

    The parameter encodes implicit modifiers in bits 24–31, the HID usage page in
    bits 16–23 and the usage ID in bits 0–15. Known usages render as canonical
    ZMK keycode names wrapped in modifier macros (``LS(A)``); unknown usages
    render as a hex literal, which compiles to the identical binding.

    Args:
        param: Raw first parameter of a key-press-style binding.

    Returns:
        Devicetree token text for the parameter.
    """
    mods = (param >> 24) & 0xFF
    page = (param >> 16) & 0xFF
    usage = param & 0xFFFF
    if page == DtsMaps.HID_PAGE_KEYBOARD:
        name = DtsMaps.KEYBOARD_USAGE_NAMES.get(usage)
    elif page == DtsMaps.HID_PAGE_CONSUMER:
        name = DtsMaps.CONSUMER_USAGE_NAMES.get(usage)
    else:
        name = None
    if name is None:
        return f"0x{param:X}"
    for bit, wrapper in DtsMaps.MOD_BIT_WRAPPERS.items():
        if mods & bit:
            name = f"{wrapper}({name})"
    return name


def binding_to_dts(
    binding: Dict[str, Any],
    name_by_id: Dict[int, str],
    layer_index_by_id: Optional[Dict[int, int]] = None,
    macro_refs: Optional[Dict[str, str]] = None,
) -> tuple[str, Optional[str]]:
    """Render one backup binding as keymap devicetree text.

    Layer-referencing parameters carry the keyboard's stable layer ID on the
    wire, but compiled keymaps address layers by position; when
    ``layer_index_by_id`` is given, layer parameters are translated through it.
    Stable IDs diverge from positions once layers have ever been reordered or
    re-created, so baking untranslated IDs would silently retarget layer keys.

    Args:
        binding: Mapping with ``behavior_id``, ``param1``, ``param2`` (backup
            bundle format).
        name_by_id: Behavior display names keyed by wire ID (from the same
            backup bundle).
        layer_index_by_id: Baked layer position keyed by stable layer ID;
            ``None`` leaves layer parameters untranslated.
        macro_refs: Devicetree references of the macros being baked, keyed by
            lower-cased display name. Keys bound to a compiled-in sequence
            resolve through this map (sequences are parameterless).

    Returns:
        Tuple of (devicetree token text, warning). The warning is ``None`` when
        the behavior mapped cleanly; otherwise the binding renders as ``&trans``
        (or with a best-effort layer number) and the warning explains why.
    """
    behavior_id = int(binding["behavior_id"])
    params = [int(binding.get("param1", 0)), int(binding.get("param2", 0))]
    display_name = name_by_id.get(behavior_id, "")
    if macro_refs is not None:
        macro_ref = macro_refs.get(display_name.lower())
        if macro_ref is not None:
            return macro_ref, None
    token = DtsMaps.BEHAVIOR_TOKENS.get(display_name.lower())
    if token is None:
        return (
            "&trans",
            f"behavior '{display_name or behavior_id}' has no devicetree mapping; "
            f"key rendered as Transparent (params {params[0]}, {params[1]})",
        )
    reference, param_kinds = token
    rendered: List[str] = [reference]
    warning: Optional[str] = None
    for kind, value in zip(param_kinds, params):
        if kind == "keycode":
            rendered.append(keycode_param_to_dts(value))
        elif kind == "layer" and layer_index_by_id is not None:
            index = layer_index_by_id.get(value)
            if index is None:
                warning = (
                    f"{reference} references layer id {value}, which is not in "
                    f"the baked layer set; the number was kept as-is"
                )
                rendered.append(str(value))
            else:
                rendered.append(str(index))
        else:
            rendered.append(str(value))
    return " ".join(rendered), warning


def altcode_macro(node_name: str, display_name: str, digits: str) -> MacroDefinition:
    """Build a Windows Alt-code macro (hold Left Alt, type keypad digits).

    Windows composes the character from the numeric keypad digits while Alt is
    held. The ``&nl_guard`` wrap switches Num Lock on when the host has it
    off (the digits would otherwise be navigation keys) and restores the
    original state afterwards.

    Args:
        node_name: Devicetree node label for the macro.
        display_name: Human-readable name (shown in behavior pickers).
        digits: Decimal code digits, e.g. ``"0232"`` for è.

    Returns:
        The macro definition (30 ms pacing, plus a settle pause before the
        Alt release: Windows commits the character on Alt-up, and native
        controls drop the composition when the release rides the last digit
        too closely).
    """
    # The guard turns Num Lock on when the host has it off (and restores it
    # afterwards) - without it the keypad digits are navigation keys and the
    # sequence types nothing or moves the caret around.
    steps = [
        MacroStep(MacroStepKind.PRESS, "&nl_guard"),
        MacroStep(MacroStepKind.PRESS, "&kp LALT"),
    ]
    steps.extend(MacroStep(MacroStepKind.TAP, f"&kp KP_N{d}") for d in digits)
    steps.append(MacroStep(MacroStepKind.WAIT_MS, "", 60))
    steps.append(MacroStep(MacroStepKind.RELEASE, "&kp LALT"))
    steps.append(MacroStep(MacroStepKind.RELEASE, "&nl_guard"))
    return MacroDefinition(
        node_name=node_name, display_name=display_name, steps=steps, wait_ms=30, tap_ms=30
    )


def layer_color_macro(
    node_name: str,
    display_name: str,
    layer_index: int,
    hue: int,
    saturation: int,
    brightness: int,
) -> MacroDefinition:
    """Build a macro that switches to a layer and sets the underglow color.

    Underglow behaviors are global on split keyboards, so both halves change
    color. Hue is 0–360 degrees (0 red, 60 yellow, 120 green, 240 blue),
    saturation and brightness are 0–100 percent.

    Args:
        node_name: Devicetree node label for the macro.
        display_name: Human-readable name (shown in behavior pickers).
        layer_index: Layer number passed to ``&to``.
        hue: Color hue in degrees (0–360).
        saturation: Color saturation percent (0–100).
        brightness: Color brightness percent (0–100).

    Returns:
        The macro definition (zero pacing: both steps are internal behaviors).
    """
    return MacroDefinition(
        node_name=node_name,
        display_name=display_name,
        steps=[
            MacroStep(MacroStepKind.TAP, f"&to {layer_index}"),
            MacroStep(
                MacroStepKind.TAP,
                f"&rgb_ug RGB_COLOR_HSB({hue},{saturation},{brightness})",
            ),
        ],
        wait_ms=0,
        tap_ms=0,
    )


def mo_layer_color_macro(
    node_name: str,
    display_name: str,
    layer_index: int,
    hue: int,
    saturation: int,
    brightness: int,
) -> MacroDefinition:
    """Build a hold-a-layer-with-color macro that restores the previous color.

    While held: the layer is momentarily active and the underglow shows the
    given color. On release: the layer deactivates and the underglow returns to
    exactly the color (and on/off state) it had before the hold, via the
    ``&rgb_mem`` save/restore behavior compiled into KeyMapper firmware — no
    bookkeeping of "which layer was I on" needed.

    Args:
        node_name: Devicetree node label for the macro.
        display_name: Human-readable name (shown in behavior pickers).
        layer_index: Layer number passed to ``&mo``.
        hue: Held-color hue in degrees (0–360).
        saturation: Held-color saturation percent (0–100).
        brightness: Held-color brightness percent (0–100).

    Returns:
        The macro definition (zero pacing: all steps are internal behaviors).
    """
    return MacroDefinition(
        node_name=node_name,
        display_name=display_name,
        steps=[
            MacroStep(MacroStepKind.PRESS, "&rgb_mem"),
            MacroStep(MacroStepKind.PRESS, f"&mo {layer_index}"),
            MacroStep(
                MacroStepKind.TAP,
                f"&rgb_ug RGB_COLOR_HSB({hue},{saturation},{brightness})",
            ),
            MacroStep(MacroStepKind.PAUSE_FOR_RELEASE),
            MacroStep(MacroStepKind.RELEASE, f"&mo {layer_index}"),
            MacroStep(MacroStepKind.RELEASE, "&rgb_mem"),
        ],
        wait_ms=0,
        tap_ms=0,
    )


def altcode_pair(
    node_name: str, display_name: str, lower_digits: str, upper_digits: str
) -> MacroDefinition:
    """Build a Shift-aware Alt-code pair (lowercase normally, uppercase shifted).

    Compiles as a mod-morph: the lowercase macro fires on a plain press; with
    Shift held the uppercase macro fires instead, and the physically-held Shift
    is masked while it types (a real Shift would turn keypad digits into
    navigation keys and break the Alt-code).

    Args:
        node_name: Devicetree node label for the pair.
        display_name: Human-readable name (shown in behavior pickers).
        lower_digits: Alt-code digits of the lowercase character.
        upper_digits: Alt-code digits of the uppercase character.

    Returns:
        The pair definition (30 ms pacing on both variants).
    """
    lower = altcode_macro(node_name, display_name, lower_digits)
    upper = altcode_macro(node_name, display_name, upper_digits)
    lower.shifted_steps = upper.steps
    return lower


def preset_macros() -> List[MacroDefinition]:
    """Return the built-in Alt-code preset macros (CP-1252 codes).

    Returns:
        The € macro plus Shift pairs for è/È é/É à/À ì/Ì ù/Ù ò/Ò, ready to
        compile or to seed the sequence composer.
    """
    # Display names must stay bit-identical across firmware generations: keys
    # already assigned to these behaviors are re-linked by display name when a
    # new firmware is baked (wire IDs are firmware-assigned and not stable).
    pairs = [
        ("e_grave", "è (Alt+0232)", "0232", "0200"),
        ("e_acute", "é (Alt+0233)", "0233", "0201"),
        ("a_grave", "à (Alt+0224)", "0224", "0192"),
        ("i_grave", "ì (Alt+0236)", "0236", "0204"),
        ("u_grave", "ù (Alt+0249)", "0249", "0217"),
        ("o_grave", "ò (Alt+0242)", "0242", "0210"),
    ]
    return [altcode_macro("euro_sign", "€ (Alt+0128)", "0128")] + [
        altcode_pair(node, name, low, up) for node, name, low, up in pairs
    ]


def render_macro_dts(macro: MacroDefinition) -> str:
    """Render one macro (or Shift pair) as devicetree node(s).

    A plain macro renders as a single ``zmk,behavior-macro`` node. A Shift pair
    (``shifted_steps`` non-empty) renders as two internal macro nodes plus a
    ``zmk,behavior-mod-morph`` node carrying the pair's name and display name,
    so existing key assignments keep their wire identity while gaining the
    shifted variant. The morph intentionally omits ``keep-mods``: the held
    Shift is masked while the shifted macro types.

    The shifted variant is forced to zero wait/tap times regardless of the
    macro's configured pacing: the morph's Shift mask only lasts while the key
    is physically held, and any non-zero pacing makes the macro finish
    asynchronously after the mask can drop — the still-held physical Shift then
    corrupts the remaining output (Shift+keypad digits become navigation keys,
    and Shift over the macro's held Alt triggers the Windows input-language
    hotkey). Zero pacing runs the whole sequence synchronously inside the
    masked window.

    Args:
        macro: The macro definition to render.

    Returns:
        Devicetree source text (indented for the ``macros`` section).
    """

    def _macro_node(
        node_name: str,
        display_name: str,
        steps: List[MacroStep],
        wait_ms: Optional[int] = None,
        tap_ms: Optional[int] = None,
    ) -> str:
        cells: List[str] = []
        for step in steps:
            if step.kind == MacroStepKind.TAP:
                cells.append(f"<&macro_tap {step.binding}>")
            elif step.kind == MacroStepKind.PRESS:
                cells.append(f"<&macro_press {step.binding}>")
            elif step.kind == MacroStepKind.RELEASE:
                cells.append(f"<&macro_release {step.binding}>")
            elif step.kind == MacroStepKind.WAIT_MS:
                cells.append(f"<&macro_wait_time {step.value}>")
            elif step.kind == MacroStepKind.TAP_MS:
                cells.append(f"<&macro_tap_time {step.value}>")
            else:
                cells.append("<&macro_pause_for_release>")
        bindings = "\n                , ".join(cells)
        effective_wait = macro.wait_ms if wait_ms is None else wait_ms
        effective_tap = macro.tap_ms if tap_ms is None else tap_ms
        return (
            f"        {node_name}: {node_name} {{\n"
            f'            compatible = "zmk,behavior-macro";\n'
            f"            #binding-cells = <0>;\n"
            f'            display-name = '
            f'"{dts_string_escape(sanitize_display_name(display_name))}";\n'
            f"            wait-ms = <{effective_wait}>;\n"
            f"            tap-ms = <{effective_tap}>;\n"
            f"            bindings\n"
            f"                = {bindings};\n"
            f"        }};\n"
        )

    if not macro.shifted_steps:
        return _macro_node(macro.node_name, macro.display_name, macro.steps)
    lower_name = f"{macro.node_name}_l"
    upper_name = f"{macro.node_name}_u"
    return (
        _macro_node(lower_name, f"{macro.display_name} (plain)", macro.steps)
        + "\n"
        + _macro_node(
            upper_name,
            f"{macro.display_name} (shift)",
            macro.shifted_steps,
            wait_ms=0,
            tap_ms=0,
        )
        + "\n"
        + (
            f"        {macro.node_name}: {macro.node_name} {{\n"
            f'            compatible = "zmk,behavior-mod-morph";\n'
            f"            #binding-cells = <0>;\n"
            f'            display-name = '
            f'"{dts_string_escape(sanitize_display_name(macro.display_name))}";\n'
            f"            bindings = <&{lower_name}>, <&{upper_name}>;\n"
            f"            mods = <(MOD_LSFT|MOD_RSFT)>;\n"
            f"        }};\n"
        )
    )


def sanitize_display_name(text: str) -> str:
    """Make a display name safe to compile into firmware.

    Zephyr's build system copies devicetree string values verbatim into a
    generated CMake file without re-escaping them, so backslashes and double
    quotes fail the firmware build even when correctly escaped at the
    devicetree level (CMake chokes on ``\\M``-style sequences). Launcher
    sequences carry user-typed paths, where both are common — they become
    forward slashes and single quotes, which read the same and build fine.

    Args:
        text: Arbitrary display text.

    Returns:
        The text with backslashes turned into forward slashes and double
        quotes into single quotes.
    """
    return text.replace("\\", "/").replace('"', "'")


def dts_string_escape(text: str) -> str:
    """Escape a display name for use inside a devicetree string literal.

    Belt-and-braces behind :func:`sanitize_display_name`: any backslash or
    double quote that still reaches a devicetree string is escaped so the
    devicetree itself stays parseable.

    Args:
        text: Arbitrary display text.

    Returns:
        The text with backslashes and double quotes backslash-escaped.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def sanitize_dts_node_name(name: str) -> str:
    """Turn a display name into a valid devicetree node name.

    Args:
        name: Arbitrary layer or macro display name.

    Returns:
        Lower-cased name with non-alphanumerics collapsed to underscores and a
        leading ``layer_`` prefix when the result would start with a digit.
    """
    node = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower() or "layer"
    if node[0].isdigit():
        node = f"layer_{node}"
    return node


def speed_to_scaler(percent: int) -> Tuple[int, int]:
    """Convert a speed percentage into a ``&zip_xy_scaler`` fraction.

    The scaler multiplies each motion delta by ``multiplier / divisor``; both
    terms must stay at or below 16 (larger values risk int16 overflow inside
    the processor). This picks the fraction closest to ``percent / 100``.

    Args:
        percent: Desired speed as a percentage (100 = unscaled).

    Returns:
        Tuple ``(multiplier, divisor)`` with both terms in 1..16.
    """
    target = percent / 100.0
    best = (1, 1)
    best_error = abs(target - 1.0)
    for divisor in range(1, 17):
        multiplier = min(16, max(1, round(target * divisor)))
        error = abs(target - multiplier / divisor)
        if error < best_error - 1e-9:
            best = (multiplier, divisor)
            best_error = error
    return best


# Mode byte values of the trackball runtime processor and its GATT payload
# (must match KEYMAP_TB_MODE_* in the firmware source).
TRACKBALL_MODE_BYTES: Dict[TrackballMode, int] = {
    TrackballMode.MOUSE: 0,
    TrackballMode.SCROLL_VERTICAL: 1,
    TrackballMode.SCROLL_HORIZONTAL: 2,
    TrackballMode.DISABLED: 3,
}


def trackball_side_bytes(config: TrackballSideConfig) -> Tuple[int, int, int, int]:
    """Encode one trackball's settings as runtime-processor values.

    Args:
        config: One side's staged settings.

    Returns:
        Tuple ``(mode, multiplier, divisor, flags)`` as the firmware expects
        them; an uninstalled side encodes as DISABLED.
    """
    mode = (
        TRACKBALL_MODE_BYTES[TrackballMode.DISABLED]
        if not config.installed
        else TRACKBALL_MODE_BYTES[config.mode]
    )
    multiplier, divisor = speed_to_scaler(config.speed_percent)
    flags = 1 if config.natural_direction else 0
    return mode, multiplier, divisor, flags


def trackball_config_payload(trackballs: TrackballConfig) -> bytes:
    """Encode the staged trackball settings as the GATT write payload.

    Layout: one version byte, then per ball (left first) four bytes — mode,
    speed multiplier, speed divisor, flags (bit 0 = natural direction).

    Args:
        trackballs: The staged trackball settings.

    Returns:
        The 9-byte payload the firmware's config characteristic accepts.
    """
    payload = [TRACKBALL_CONFIG_VERSION]
    for side in ("left", "right"):
        payload.extend(trackball_side_bytes(getattr(trackballs, side)))
    return bytes(payload)


def render_half_overlay(trackballs: TrackballConfig, left: bool) -> str:
    """Render one half's devicetree overlay (per-half sensor properties).

    The sensor nodes exist only in their own half's build, so properties
    like ``force-awake`` cannot be set from the shared keymap — ZMK picks up
    ``config/<shield>.overlay`` files instead. The overlay is written even
    when empty so its presence is consistent across generations.

    ORDERING TRAP: ZMK appends this file to ``shield_dts_files`` during the
    Zephyr module phase, which runs BEFORE shields.cmake adds the shield's
    own overlay - so shield-defined labels (``&trackball_central``) do not
    exist yet here and referencing them is a devicetree parse error. Merge
    by node PATH instead: ``&spi1`` is a SoC label (always defined by the
    board DTS, parsed first) and a partial ``<sensor>@0`` child created here
    merges with the shield's full definition of the same path later.

    Args:
        trackballs: The staged trackball settings.
        left: True for the left half (sensor ``trackball_central``), False
            for the right (``trackball_peripheral``).

    Returns:
        Overlay source text.
    """
    sensor = "trackball_central" if left else "trackball_peripheral"
    lines = [
        "/* Generated per-half sensor options (see the Advanced tab). */",
    ]
    if trackballs.force_awake:
        lines.extend(
            [
                "// Never rest: zero wake-up lag; the keyboard-level idle and",
                "// deep-sleep timeouts still bound overall power use. Merged",
                "// into the shield's sensor node by path (labels from the",
                "// shield overlay are not defined yet at this parse point).",
                "&spi1 {",
                f"    {sensor}@0 {{",
                "        force-awake;",
                "    };",
                "};",
            ]
        )
    return "\n".join(lines) + "\n"


def render_trackball_dts(trackballs: TrackballConfig) -> str:
    """Render the trackball listener overrides for the generated keymap.

    Both listeners route through KeyMapper's runtime input processor
    (``&keymapper_tb``, ball 0 = left, 1 = right), which applies mode,
    speed, and scroll direction from values stored on the keyboard — so the
    app can change them live over Bluetooth. A side marked not installed is
    compiled out entirely with ``status = "disabled"``. The left half's
    sensor is ``trackball_central``, the right half's ``trackball_peripheral``
    (ZMK names them by split role, not by position).

    Args:
        trackballs: The staged trackball settings (compiled in as the
            initial values; runtime writes override them).

    Returns:
        Devicetree source appended after the keymap root node.
    """
    listener_by_side = {
        "left": ("trackball_central_listener", 0),
        "right": ("trackball_peripheral_listener", 1),
    }
    sections: List[str] = []
    for side, (listener, ball) in listener_by_side.items():
        config: TrackballSideConfig = getattr(trackballs, side)
        if not config.installed:
            sections.append(
                f"// {side.capitalize()} trackball: not installed.\n"
                f"&{listener} {{ status = \"disabled\"; }};\n"
            )
            continue
        sections.append(
            f"// {side.capitalize()} trackball: runtime-configured "
            f"(ball {ball}).\n"
            f"&{listener} {{\n"
            f"    input-processors = <&keymapper_tb {ball}>;\n"
            f"}};\n"
        )
    return "\n".join(sections)


def render_keymap_dts(
    backup: Dict[str, Any],
    macros: List[MacroDefinition],
    total_layers: int = 32,
    trackballs: Optional[TrackballConfig] = None,
) -> tuple[str, List[str]]:
    """Render a complete ``imprint.keymap`` baking in a backed-up layout.

    The generated file reproduces the official template's structure: same
    includes, the chosen physical layout resolved from the backup's active
    layout display name, a ``macros`` section with every generated macro, one
    layer node per backed-up layer (original display names preserved), reserved
    empty layers up to ``total_layers`` for runtime use, and the trackball
    configuration.

    Args:
        backup: Backup bundle (``keymap-backup-v1`` format).
        macros: Macros to compile in.
        total_layers: Total layer slots including reserved ones (firmware
            supports up to 32).
        trackballs: Staged trackball settings; ``None`` uses the defaults
            (which reproduce the official template's behavior).

    Returns:
        Tuple of (keymap source text, warnings). Warnings list every binding
        that could not be mapped and a note when the physical layout was not
        recognized.
    """
    warnings: List[str] = []
    name_by_id: Dict[int, str] = {
        int(b["behavior_id"]): str(b["display_name"]) for b in backup.get("behaviors", [])
    }
    # Layer behaviors carry stable layer IDs on the wire; the baked keymap
    # addresses layers by position in the generated order.
    layer_index_by_id: Dict[int, int] = {
        int(layer["layer_id"]): index
        for index, layer in enumerate(backup["keymap"]["layers"])
    }
    # Keys bound to compiled-in sequences resolve by display name against the
    # macro set being baked, keeping those assignments across rebuilds.
    macro_refs: Dict[str, str] = {
        m.display_name.lower(): f"&{m.node_name}" for m in macros
    }

    layouts = backup["physical_layouts"]
    active_name = str(layouts["layouts"][int(layouts["active_index"])]["name"])
    layout_label = DtsMaps.PHYSICAL_LAYOUT_LABELS.get(active_name.lower())
    if layout_label is None:
        layout_label = "physical_layout_imprint_function_row_full_bottom_row"
        warnings.append(
            f"physical layout '{active_name}' not recognized; defaulted to the "
            f"template's Function Row (5-Key Bottom Row) — verify before flashing"
        )

    macro_section = ""
    if macros:
        macro_nodes = "\n".join(render_macro_dts(m) for m in macros)
        macro_section = f"    macros {{\n{macro_nodes}    }};\n\n"

    layer_nodes: List[str] = []
    used_node_names: Dict[str, int] = {}
    for layer in backup["keymap"]["layers"]:
        display = str(layer["name"])
        node = sanitize_dts_node_name(display)
        if node in used_node_names:
            used_node_names[node] += 1
            node = f"{node}_{used_node_names[node]}"
        else:
            used_node_names[node] = 0
        tokens: List[str] = []
        for position, binding in enumerate(layer["bindings"]):
            dts, warning = binding_to_dts(
                binding, name_by_id, layer_index_by_id, macro_refs
            )
            if warning is not None:
                warnings.append(f"layer '{display}' key {position}: {warning}")
            tokens.append(dts)
        rows = [
            "  ".join(tokens[i : i + 6]) for i in range(0, len(tokens), 6)
        ]
        bindings_text = "\n".join(rows)
        layer_nodes.append(
            f"        {node} {{\n"
            f'            display-name = '
            f'"{dts_string_escape(sanitize_display_name(display))}";\n'
            f"            bindings = <\n{bindings_text}\n            >;\n"
            f"        }};\n"
        )
    reserved_count = max(0, total_layers - len(backup["keymap"]["layers"]))
    for i in range(reserved_count):
        layer_nodes.append(
            f'        extra{i + 1} {{\n            status = "reserved";\n        }};\n'
        )

    source = (
        f"{FirmwareTemplates.KEYMAP_INCLUDES}\n"
        f"/ {{\n"
        f"    chosen {{ zmk,physical-layout = &{layout_label}; }};\n\n"
        f"{FirmwareTemplates.RGB_REMEMBER_NODE}"
        f"{FirmwareTemplates.TRACKBALL_RUNTIME_NODE}\n"
        f"{macro_section}"
        f"    keymap {{\n"
        f'        compatible = "zmk,keymap";\n\n'
        + "\n".join(layer_nodes)
        + f"    }};\n"
        f"}};\n\n"
        f"{render_trackball_dts(trackballs if trackballs is not None else TrackballConfig())}"
    )
    return source, warnings


def compute_queue_size(macros: List[MacroDefinition], slack: int) -> Optional[int]:
    """Compute the CONFIG_ZMK_BEHAVIORS_QUEUE_SIZE a macro set requires.

    The behavior queue is one shared global buffer and macros fully enqueue on
    fire, so overlapping activations add up: sizing sums every macro's slot need
    (worst case: all fired together) plus ``slack``. An undersized queue drops
    trailing events — for Alt-code macros that means a stuck Alt modifier on the
    host. Returns ``None`` when the firmware default of 64 slots suffices.

    Args:
        macros: Macros to be compiled in.
        slack: Extra slots to reserve on top of the combined macro need.

    Returns:
        The queue size to configure, or ``None`` to keep the firmware default.
    """
    if not macros:
        return None
    need = sum(m.queue_slots() for m in macros) + slack
    return need if need > 64 else None


def render_conf(
    macros: List[MacroDefinition],
    slack: int,
    battery_alert: Optional[BatteryAlertConfig] = None,
    power: Optional[PowerConfig] = None,
    locking: Optional[LockingConfig] = None,
    trackballs: Optional[TrackballConfig] = None,
) -> str:
    """Render the generated ``imprint.conf``.

    Args:
        macros: Macros to be compiled in (drives the behavior-queue size).
        slack: Extra queue slots on top of the combined need of all macros.
        battery_alert: Low-battery blink settings; ``None`` or disabled omits
            the feature's configuration lines entirely.
        power: Idle / deep-sleep settings; ``None`` keeps ZMK's defaults
            (30 s idle, deep sleep off, LEDs unaffected).
        locking: Studio-locking setting; ``None`` disables locking (the
            KeyMapper default — no A+F unlock ritual).
        trackballs: Trackball settings; sets the shared sensor report
            interval. ``None`` keeps the shield's stock 8 ms.

    Returns:
        Configuration file text with underglow on, Studio locking as
        configured (off by default), the behavior queue enlarged when needed,
        the trackball sensor report interval when configured, and the
        low-battery alert and power settings when configured.
    """
    locking_enabled = locking is not None and locking.studio_locking_enabled
    lines = [
        FirmwareTemplates.CONF_HEADER,
        "CONFIG_ZMK_RGB_UNDERGLOW=y",
        f"CONFIG_ZMK_STUDIO_LOCKING={'y' if locking_enabled else 'n'}",
        # Expose the right half's battery as an extra BLE Battery Service
        # instance on the central, so the app can display both halves. PROXY
        # depends on FETCHING (which has no default), so both must be set.
        "CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING=y",
        "CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_PROXY=y",
    ]
    queue_size = compute_queue_size(macros, slack)
    if queue_size is not None:
        lines.append(f"CONFIG_ZMK_BEHAVIORS_QUEUE_SIZE={queue_size}")
    if trackballs is not None:
        lines.append(
            f"CONFIG_PMW3610_REPORT_INTERVAL_MIN={trackballs.responsiveness_ms}"
        )
        if trackballs.wake_check_ms is not None:
            # One motion-check interval for every rest tier: how fast a
            # dozing sensor notices movement (the wake-up lag).
            wake = max(10, round(trackballs.wake_check_ms / 10) * 10)
            lines.extend(
                [
                    f"CONFIG_PMW3610_REST1_SAMPLE_TIME_MS={wake}",
                    f"CONFIG_PMW3610_REST2_SAMPLE_TIME_MS={wake}",
                    f"CONFIG_PMW3610_REST3_SAMPLE_TIME_MS={wake}",
                ]
            )
        if trackballs.awake_after_motion_ms is not None:
            # The driver register works in 32 ms steps.
            run = max(32, round(trackballs.awake_after_motion_ms / 32) * 32)
            lines.append(f"CONFIG_PMW3610_RUN_DOWNSHIFT_TIME_MS={run}")
        for side, prefix in (("left", "LEFT"), ("right", "RIGHT")):
            mode, multiplier, divisor, flag_bits = trackball_side_bytes(
                getattr(trackballs, side)
            )
            lines.extend(
                [
                    f"CONFIG_KEYMAP_TB_{prefix}_MODE={mode}",
                    f"CONFIG_KEYMAP_TB_{prefix}_MUL={multiplier}",
                    f"CONFIG_KEYMAP_TB_{prefix}_DIV={divisor}",
                    f"CONFIG_KEYMAP_TB_{prefix}_FLAGS={flag_bits}",
                ]
            )
    if power is not None:
        lines.extend(
            [
                f"CONFIG_ZMK_IDLE_TIMEOUT={power.idle_seconds * 1000}",
                f"CONFIG_ZMK_SLEEP={'y' if power.deep_sleep_enabled else 'n'}",
            ]
        )
        if power.deep_sleep_enabled:
            lines.append(
                f"CONFIG_ZMK_IDLE_SLEEP_TIMEOUT={power.deep_sleep_minutes * 60000}"
            )
        lines.append(
            "CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_IDLE="
            f"{'y' if power.rgb_off_when_idle else 'n'}"
        )
        lines.append(
            "CONFIG_ZMK_RGB_UNDERGLOW_AUTO_OFF_USB="
            f"{'y' if power.rgb_off_when_unplugged else 'n'}"
        )
    if battery_alert is not None:
        # Clamp the glow below the idle timeout: a glow spanning the idle
        # transition would latch its forced-on state into ZMK's underglow
        # auto-off bookkeeping (inverting the user's on/off after wake).
        idle_seconds = (
            power.idle_seconds if power is not None else PowerConfig().idle_seconds
        )
        battcheck_ms = min(
            battery_alert.battcheck_ms, max(250, idle_seconds * 1000 - 500)
        )
        lines.extend(
            [
                "",
                "# BattCheck (&batt_chk): battery-as-color glow duration,",
                "# kept below the idle timeout so a glow cannot span an",
                "# idle transition.",
                f"CONFIG_KEYMAP_BATTCHECK_MS={battcheck_ms}",
            ]
        )
    if battery_alert is not None and battery_alert.enabled:
        lines.extend(
            [
                "CONFIG_KEYMAP_BATTERY_ALERT=y",
                f"CONFIG_KEYMAP_BATTERY_ALERT_THRESHOLD={battery_alert.threshold_percent}",
                f"CONFIG_KEYMAP_BATTERY_ALERT_BLINK_COUNT={battery_alert.blink_count}",
                f"CONFIG_KEYMAP_BATTERY_ALERT_HUE={battery_alert.hue}",
                f"CONFIG_KEYMAP_BATTERY_ALERT_SAT={battery_alert.saturation}",
                f"CONFIG_KEYMAP_BATTERY_ALERT_BRT={battery_alert.brightness}",
                f"CONFIG_KEYMAP_BATTERY_ALERT_INTERVAL_S={battery_alert.interval_minutes * 60}",
            ]
        )
    return "\n".join(lines) + "\n"


def materialize_firmware_workspace(
    workspace_dir: Path,
    backup: Dict[str, Any],
    macros: List[MacroDefinition],
    params: Parameters,
    battery_alert: Optional[BatteryAlertConfig] = None,
    power: Optional[PowerConfig] = None,
    locking: Optional[LockingConfig] = None,
    trackballs: Optional[TrackballConfig] = None,
) -> Dict[str, Any]:
    """Write the generated firmware configuration into the workspace directory.

    Produces ``config/imprint.keymap`` and ``config/imprint.conf`` ready to be
    pushed into a repository created from the official user-config template
    (which supplies ``build.yaml`` and ``west.yml``), the ``keymap-extras``
    Zephyr module carrying the low-battery alert code (the build workflow passes
    the repository root as an extra module, so it is picked up automatically),
    plus a notes file describing what was generated.

    Args:
        workspace_dir: Root of the firmware workspace (created if missing).
        backup: Backup bundle to bake in.
        macros: Macros to compile in.
        params: Validated configuration (queue slack, keyboard tag).
        battery_alert: Low-battery blink settings compiled into ``imprint.conf``
            when enabled; the module code itself ships regardless (inactive
            unless its Kconfig switch is set).
        power: Idle / deep-sleep settings compiled into ``imprint.conf``.
        locking: Studio-locking setting compiled into ``imprint.conf``
            (``None`` disables locking, the KeyMapper default).
        trackballs: Trackball settings compiled into the keymap (listener
            overrides) and ``imprint.conf`` (sensor report interval).

    Returns:
        Mapping with ``files`` (written paths as strings) and ``warnings``.
    """
    config_dir = workspace_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    keymap_text, warnings = render_keymap_dts(backup, macros, trackballs=trackballs)
    conf_text = render_conf(
        macros,
        params.firmware.behaviors_queue_slack,
        battery_alert,
        power,
        locking,
        trackballs,
    )
    keymap_path = config_dir / "imprint.keymap"
    conf_path = config_dir / "imprint.conf"
    keymap_path.write_text(keymap_text, encoding="utf-8")
    conf_path.write_text(conf_text, encoding="utf-8")
    effective_trackballs = trackballs if trackballs is not None else TrackballConfig()
    for side_left, overlay_name in ((True, "imprint_left.overlay"),
                                    (False, "imprint_right.overlay")):
        (config_dir / overlay_name).write_text(
            render_half_overlay(effective_trackballs, side_left),
            encoding="utf-8",
        )

    module_files = {
        workspace_dir / "zephyr" / "module.yml": FirmwareTemplates.MODULE_YML,
        workspace_dir / "CMakeLists.txt": FirmwareTemplates.MODULE_CMAKE,
        workspace_dir / "Kconfig": FirmwareTemplates.MODULE_KCONFIG,
        workspace_dir / "src" / "battery_alert_blink.c": FirmwareTemplates.MODULE_SOURCE,
        workspace_dir / "src" / "battcheck.c": FirmwareTemplates.BATTCHECK_SOURCE,
        workspace_dir / "src" / "rgb_remember.c": FirmwareTemplates.RGB_REMEMBER_SOURCE,
        workspace_dir / "src" / "capture_gatt.c": FirmwareTemplates.CAPTURE_SOURCE,
        workspace_dir
        / "src"
        / "trackball_runtime.c": FirmwareTemplates.TRACKBALL_RUNTIME_SOURCE,
        workspace_dir
        / "src"
        / "underglow_wake_sync.c": FirmwareTemplates.UNDERGLOW_WAKE_SYNC_SOURCE,
        workspace_dir
        / "src"
        / "numlock_guard.c": FirmwareTemplates.NUMLOCK_GUARD_SOURCE,
        workspace_dir
        / "dts"
        / "bindings"
        / "behaviors"
        / "zmk,behavior-rgb-remember.yaml": FirmwareTemplates.RGB_REMEMBER_BINDING,
        workspace_dir
        / "dts"
        / "bindings"
        / "behaviors"
        / "zmk,behavior-battcheck.yaml": FirmwareTemplates.BATTCHECK_BINDING,
        workspace_dir
        / "dts"
        / "bindings"
        / "behaviors"
        / "zmk,behavior-numlock-guard.yaml": FirmwareTemplates.NUMLOCK_GUARD_BINDING,
        workspace_dir
        / "dts"
        / "bindings"
        / "input_processors"
        / "keymapper,input-processor-trackball.yaml": (
            FirmwareTemplates.TRACKBALL_RUNTIME_BINDING
        ),
        workspace_dir
        / "dts"
        / "bindings"
        / "vendor-prefixes.txt": FirmwareTemplates.VENDOR_PREFIXES,
    }
    for path, content in module_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    notes_path = workspace_dir / "KEYMAP_NOTES.md"
    macro_list = "\n".join(f"- `{m.node_name}`: {m.display_name}" for m in macros) or "- none"
    warning_list = "\n".join(f"- {w}" for w in warnings) or "- none"
    notes_path.write_text(
        f"# Generated firmware configuration\n\n"
        f"Source backup: {backup.get('created_utc', 'unknown')} "
        f"(device: {(backup.get('device') or {}).get('name', 'unknown')})\n"
        f"Keyboard module tag: {params.firmware.keyboard_tag}\n\n"
        f"## Macros\n{macro_list}\n\n"
        f"## Warnings\n{warning_list}\n\n"
        f"Flash the RIGHT half first, then the LEFT half; after the left flash\n"
        f"KeyMapper automatically clears the old keyboard-stored layout (which\n"
        f"would otherwise shadow this keymap) and verifies the result. The order\n"
        f"only matters for that automatic finish - flashing in the wrong order\n"
        f"is harmless, just flash the left half last. "
        + (
            "Studio locking is ENABLED in this build: the physical A+F unlock\n"
            "is required after every keyboard restart before editing.\n"
            if locking is not None and locking.studio_locking_enabled
            else "Studio locking is disabled in this build, so the\n"
            "unlock combo is never needed again.\n"
        ),
        encoding="utf-8",
    )
    return {
        "files": [str(keymap_path), str(conf_path), str(notes_path)]
        + [str(p) for p in module_files],
        "warnings": warnings,
    }


def persist_staged_state(state: AppState) -> None:
    """Write the staged macros and firmware settings to their JSON files.

    Covers the macros plus the battery-alert, power, locking, and trackball
    settings.

    Args:
        state: Shared application state whose ``pending_macros``,
            ``battery_alert``, ``power``, ``locking``, and ``trackballs``
            are saved to the paths declared in ``.env``.
    """
    Path(state.paths.MACROS_FILE).write_text(
        json.dumps([m.to_dict() for m in state.pending_macros], indent=2),
        encoding="utf-8",
    )
    Path(state.paths.BATTERY_ALERT_FILE).write_text(
        state.battery_alert.model_dump_json(indent=2), encoding="utf-8"
    )
    Path(state.paths.POWER_FILE).write_text(
        state.power.model_dump_json(indent=2), encoding="utf-8"
    )
    Path(state.paths.LOCKING_FILE).write_text(
        state.locking.model_dump_json(indent=2), encoding="utf-8"
    )
    Path(state.paths.TRACKBALLS_FILE).write_text(
        state.trackballs.model_dump_json(indent=2), encoding="utf-8"
    )


def load_staged_state(state: AppState) -> None:
    """Load staged macros and firmware settings from their JSON files.

    Covers the macros plus the battery-alert, power, locking, and trackball
    settings. Missing or unreadable files leave the in-memory defaults
    untouched (empty macro list, disabled alert, default power and locking,
    stock trackball behavior), so a fresh installation boots cleanly.

    Args:
        state: Shared application state to populate.
    """
    macros_file = Path(state.paths.MACROS_FILE)
    if macros_file.is_file():
        try:
            raw = json.loads(macros_file.read_text(encoding="utf-8"))
            state.pending_macros = [MacroDefinition.from_dict(m) for m in raw]
            for macro in state.pending_macros:
                macro.display_name = sanitize_display_name(macro.display_name)
        except (ValueError, KeyError, TypeError) as exc:
            state.logger.warning(f"Ignoring unreadable macros file: {exc}")
    alert_file = Path(state.paths.BATTERY_ALERT_FILE)
    if alert_file.is_file():
        try:
            state.battery_alert = BatteryAlertConfig.model_validate_json(
                alert_file.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            state.logger.warning(f"Ignoring unreadable battery-alert file: {exc}")
    power_file = Path(state.paths.POWER_FILE)
    if power_file.is_file():
        try:
            state.power = PowerConfig.model_validate_json(
                power_file.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            state.logger.warning(f"Ignoring unreadable power settings file: {exc}")
    locking_file = Path(state.paths.LOCKING_FILE)
    if locking_file.is_file():
        try:
            state.locking = LockingConfig.model_validate_json(
                locking_file.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            state.logger.warning(f"Ignoring unreadable locking settings file: {exc}")
    trackballs_file = Path(state.paths.TRACKBALLS_FILE)
    if trackballs_file.is_file():
        try:
            state.trackballs = TrackballConfig.model_validate_json(
                trackballs_file.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            state.logger.warning(
                f"Ignoring unreadable trackball settings file: {exc}"
            )


def split_behavior_list(text: str) -> List[str]:
    """Split a comma-separated behavior list, respecting parentheses.

    Commas inside parenthesized parameter lists (``RGB_COLOR_HSB(0,100,50)``)
    are part of the behavior and never split points; only commas at parenthesis
    depth zero separate behaviors.

    Args:
        text: Raw behavior text as typed in the sequence composer.

    Returns:
        The individual behavior references, whitespace-trimmed, empties dropped.
    """
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def validate_macro_binding(binding: str) -> Optional[str]:
    """Check one devicetree behavior reference used in a macro step.

    Args:
        binding: The step's behavior text, e.g. ``&rgb_ug RGB_COLOR_HSB(0,100,50)``.

    Returns:
        ``None`` when acceptable, otherwise a human-readable problem description.
        Verifies the ``&`` reference shape and, when an ``RGB_COLOR_HSB(h,s,b)``
        call is present, that hue ≤ 360 and saturation/brightness ≤ 100 —
        catching RGB triplets (0–255) mistakenly used where HSB is required.
    """
    text = binding.strip()
    if not text.startswith("&"):
        return f"'{text}' is not a behavior reference (must start with &)"
    hsb = re.search(r"RGB_COLOR_HSB\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", text)
    if hsb is not None:
        hue, sat, bri = (int(g) for g in hsb.groups())
        if hue > 360 or sat > 100 or bri > 100:
            return (
                f"RGB_COLOR_HSB({hue},{sat},{bri}) is out of range: it takes "
                f"HSB — hue 0–360, saturation 0–100, brightness 0–100 — not an "
                f"RGB triplet. Examples: red (0,100,50), yellow (60,100,50), "
                f"green (120,100,50), blue (240,100,50)"
            )
    return None


def find_ble_address(product_hint: str) -> Optional[str]:
    """Resolve the paired keyboard's Bluetooth MAC from the Windows registry.

    Paired BLE devices are enumerated under ``HKLM\\SYSTEM\\CurrentControlSet\\
    Enum\\BTHLE\\DEV_<MAC>``; the friendly name lives on the instance subkey.
    Registry access avoids spawning PowerShell (whose module autoload is broken
    on some machines).

    Args:
        product_hint: Case-insensitive substring of the device's friendly name.

    Returns:
        The MAC formatted ``AA:BB:CC:DD:EE:FF``, or ``None`` when no paired
        device matches (or the platform has no such registry).
    """
    try:
        import winreg
    except ImportError:
        return None
    hint = product_hint.lower()
    try:
        base = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\BTHLE"
        )
    except OSError:
        return None
    with base:
        dev_index = 0
        while True:
            try:
                dev_name = winreg.EnumKey(base, dev_index)
            except OSError:
                return None
            dev_index += 1
            if not dev_name.upper().startswith("DEV_"):
                continue
            try:
                with winreg.OpenKey(base, dev_name) as dev_key:
                    instance = winreg.EnumKey(dev_key, 0)
                    with winreg.OpenKey(dev_key, instance) as inst_key:
                        friendly, _ = winreg.QueryValueEx(inst_key, "FriendlyName")
            except OSError:
                continue
            if hint in str(friendly).lower():
                raw = dev_name[4:]
                if len(raw) == 12:
                    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).upper()
    return None


# --------------------------------------------------------------------------- #
# Bluetooth backends                                                          #
#                                                                             #
# Two implementations of the same four GATT operations: the battle-tested     #
# WinRT one (Windows only) and a cross-platform Bleak one (CoreBluetooth on   #
# macOS, BlueZ on Linux; Bleak wraps the same WinRT stack on Windows). The    #
# public functions below dispatch on ble_backend().                           #
# --------------------------------------------------------------------------- #

BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


def ble_backend() -> str:
    """Which Bluetooth implementation to use: ``winrt`` or ``bleak``.

    Windows defaults to the WinRT backend; every other OS uses Bleak
    (BlueZ on Linux, CoreBluetooth on macOS). Set
    ``KEYMAPPER_BLE_BACKEND=bleak`` (or ``winrt``) to override. Caveat for
    the override on WINDOWS: Bleak's WinRT wrapper can only open devices it
    has discovered, and a bonded, HID-connected keyboard does not advertise
    — so the bleak backend there reaches the keyboard only while it is
    advertising (pairing mode). BlueZ has no such limit: bonded devices are
    connectable by address without advertising, which is what makes this
    backend viable on Linux.
    """
    override = os.environ.get("KEYMAPPER_BLE_BACKEND", "").strip().lower()
    if override in ("winrt", "bleak"):
        return override
    return "winrt" if os.name == "nt" else "bleak"


def parse_capture_payload(raw: bytes) -> Optional[Tuple[int, int, Optional[int]]]:
    """Decode one capture-characteristic payload.

    Layout: press counter u32 LE + key position u16 LE, plus an
    active-layer bitmask u32 LE on current firmware (older builds sent
    6 bytes; runts are dropped).

    Args:
        raw: The characteristic value bytes.

    Returns:
        ``(counter, position, layer_mask)`` — mask ``None`` on old firmware —
        or ``None`` when the payload is too short to be a press.
    """
    if len(raw) < 6:
        return None
    counter = int.from_bytes(raw[0:4], "little")
    position = int.from_bytes(raw[4:6], "little")
    layers = int.from_bytes(raw[6:10], "little") if len(raw) >= 10 else None
    return counter, position, layers


async def find_ble_address_bleak(product_hint: str) -> Optional[str]:
    """Find the keyboard's Bluetooth identifier with a Bleak scan.

    Cross-platform fallback for :func:`find_ble_address`. On Linux, BlueZ
    includes bonded devices in scan results even while connected; on macOS
    a connected, non-advertising keyboard may not appear until it
    re-advertises. The returned identifier is whatever the platform uses to
    open the device (a MAC on Windows/Linux, a CoreBluetooth UUID on macOS)
    and is passed through opaquely.

    Args:
        product_hint: Case-insensitive substring of the device name.

    Returns:
        The platform identifier, or ``None`` when nothing matches.
    """
    try:
        from bleak import BleakScanner  # type: ignore[import-untyped]
    except ImportError:
        return None
    hint = product_hint.lower()
    try:
        devices = await BleakScanner.discover(timeout=5.0)
    except Exception:  # noqa: BLE001 - no adapter / no permission / no scan
        return None
    for device in devices:
        if device.name and hint in device.name.lower():
            return str(device.address)
    return None


async def ensure_ble_identifier(state: AppState) -> Optional[str]:
    """Resolve and cache the keyboard's Bluetooth identifier.

    Windows-registry lookup first (finds a bonded, non-advertising keyboard
    without any scan), then Bleak discovery as the cross-platform fallback.

    Args:
        state: Shared application state (``state.ble_address`` is the cache).

    Returns:
        The identifier, or ``None`` when the keyboard cannot be located.
    """
    if state.ble_address is None:
        state.ble_address = find_ble_address(state.params.device.product_hint)
    if state.ble_address is None and ble_backend() == "bleak":
        state.ble_address = await find_ble_address_bleak(
            state.params.device.product_hint
        )
    return state.ble_address


async def _bleak_open(address: str, **kwargs: Any) -> Any:
    """Connect a BleakClient to the (normally bonded) keyboard."""
    from bleak import BleakClient  # type: ignore[import-untyped]

    client = BleakClient(address, timeout=10.0, **kwargs)
    await client.connect()
    return client


def _bleak_find_char(client: Any, service_uuid: str, char_uuid: str) -> Any:
    """Locate one characteristic in a connected client, or ``None``."""
    for service in client.services:
        if str(service.uuid).lower() != service_uuid:
            continue
        for characteristic in service.characteristics:
            if str(characteristic.uuid).lower() == char_uuid:
                return characteristic
    return None


async def _bleak_read_battery_levels(address: str) -> List[int]:
    """Bleak implementation of :func:`read_battery_levels`."""
    client = await _bleak_open(address)
    try:
        levels: List[int] = []
        for service in client.services:
            if str(service.uuid).lower() != BATTERY_SERVICE_UUID:
                continue
            for characteristic in service.characteristics:
                if str(characteristic.uuid).lower() != BATTERY_LEVEL_CHAR_UUID:
                    continue
                value = await client.read_gatt_char(characteristic)
                if value:
                    levels.append(int(value[0]))
        return levels
    finally:
        await client.disconnect()


async def _bleak_read_capture_press(
    address: str,
) -> Optional[Tuple[int, int, Optional[int]]]:
    """Bleak implementation of :func:`read_capture_press`."""
    client = await _bleak_open(address)
    try:
        characteristic = _bleak_find_char(
            client, CAPTURE_GATT_SERVICE_UUID, CAPTURE_GATT_CHAR_UUID
        )
        if characteristic is None:
            return None
        value = await client.read_gatt_char(characteristic)
        return parse_capture_payload(bytes(value))
    finally:
        await client.disconnect()


async def _bleak_write_trackball_config(address: str, payload: bytes) -> bool:
    """Bleak implementation of :func:`write_trackball_config`."""
    client = await _bleak_open(address)
    try:
        characteristic = _bleak_find_char(
            client, TRACKBALL_GATT_SERVICE_UUID, TRACKBALL_GATT_CHAR_UUID
        )
        if characteristic is None:
            return False
        await client.write_gatt_char(characteristic, bytes(payload), response=True)
        return True
    finally:
        await client.disconnect()


class _BleakWatchHandle:
    """Adapter so :func:`stop_capture_watch` can close a Bleak subscription."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.disconnect())
        else:
            loop.create_task(self._client.disconnect())


async def _bleak_start_capture_watch(state: AppState) -> bool:
    """Bleak implementation of :func:`start_capture_watch`."""
    from bleak import BleakClient  # type: ignore[import-untyped]

    address = await ensure_ble_identifier(state)
    if address is None:
        raise KeyMapperError("keyboard not found among paired Bluetooth devices")

    def _lost(_client: Any) -> None:
        # Keyboard slept or left range: the reconnect logic re-subscribes on
        # the next connect event, exactly like the WinRT path.
        state.capture_watch = None

    client = BleakClient(address, timeout=10.0, disconnected_callback=_lost)
    await client.connect()
    try:
        characteristic = _bleak_find_char(
            client, CAPTURE_GATT_SERVICE_UUID, CAPTURE_GATT_CHAR_UUID
        )
        if characteristic is None or "notify" not in characteristic.properties:
            await client.disconnect()
            return False

        def _on_notify(_sender: Any, data: Any) -> None:
            parsed = parse_capture_payload(bytes(data))
            if parsed is None:
                return
            counter, position, layers = parsed
            state.events.put(
                ConnectionEvent(
                    NotificationKind.CAPTURE_PRESS,
                    {"counter": counter, "position": position, "layers": layers},
                )
            )

        await client.start_notify(characteristic, _on_notify)
        state.capture_watch = (_BleakWatchHandle(client), None, None)
        state.logger.info("Capture press stream subscribed over Bluetooth (bleak)")
        return True
    except Exception:
        await client.disconnect()
        raise


async def read_battery_levels(address: str) -> List[int]:
    """Read every Battery Service level from the keyboard (backend dispatch).

    Args:
        address: The keyboard's Bluetooth identifier.

    Returns:
        Battery percentages in service order (central first).
    """
    if ble_backend() == "bleak":
        return await _bleak_read_battery_levels(address)
    return await _winrt_read_battery_levels(address)


async def _winrt_read_battery_levels(address: str) -> List[int]:
    """Read every Battery Service level from the paired keyboard over BLE.

    Uses the Windows runtime directly (``FromBluetoothAddressAsync``) because a
    paired, HID-connected keyboard does not advertise, so scanner-based clients
    cannot find it. The central half exposes one Battery Service instance; with
    the peripheral battery proxy enabled in KeyMapper firmware, the right half
    appears as an additional instance. Reading works alongside the existing HID
    bond — Windows multiplexes the GATT connection.

    Args:
        address: Bluetooth MAC of the paired keyboard (``AA:BB:CC:DD:EE:FF``).

    Returns:
        Battery percentages in service order (central first). Empty when the
        device exposes no readable battery characteristic.

    Raises:
        KeyMapperError: When the device cannot be opened over Bluetooth, or on
            platforms without the WinRT Bluetooth stack.
    """
    try:
        from winrt.windows.devices.bluetooth import (  # type: ignore[import-untyped]
            BluetoothCacheMode,
            BluetoothLEDevice,
        )
        from winrt.windows.devices.bluetooth.genericattributeprofile import (  # type: ignore[import-untyped]
            GattCharacteristicUuids,
            GattServiceUuids,
        )
        from winrt.windows.storage.streams import DataReader  # type: ignore[import-untyped]
    except ImportError as exc:
        raise KeyMapperError(
            "Bluetooth reads require the Windows Bluetooth stack (WinRT); "
            "battery and capture features are unavailable on this platform"
        ) from exc

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        int(address.replace(":", ""), 16)
    )
    if device is None:
        raise KeyMapperError(f"Bluetooth device {address} could not be opened")
    levels: List[int] = []
    try:
        # Uncached discovery: Windows caches a bonded device's GATT layout, and
        # a service instance added by a firmware update would stay invisible.
        try:
            services = await device.get_gatt_services_for_uuid_with_cache_mode_async(
                GattServiceUuids.battery, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            services = await device.get_gatt_services_for_uuid_async(
                GattServiceUuids.battery
            )
        for i in range(services.services.size):
            service = services.services[i]
            try:
                chars = await service.get_characteristics_for_uuid_with_cache_mode_async(
                    GattCharacteristicUuids.battery_level, BluetoothCacheMode.UNCACHED
                )
            except AttributeError:
                chars = await service.get_characteristics_for_uuid_async(
                    GattCharacteristicUuids.battery_level
                )
            for j in range(chars.characteristics.size):
                characteristic = chars.characteristics[j]
                try:
                    result = await characteristic.read_value_with_cache_mode_async(
                        BluetoothCacheMode.UNCACHED
                    )
                except AttributeError:
                    result = await characteristic.read_value_async()
                reader = DataReader.from_buffer(result.value)
                levels.append(int(reader.read_byte()))
    finally:
        device.close()
    return levels


async def read_capture_press(
    address: str,
) -> Optional[Tuple[int, int, Optional[int]]]:
    """Read the capture characteristic (backend dispatch).

    Args:
        address: The keyboard's Bluetooth identifier.

    Returns:
        ``(press_counter, key_position, layer_mask)`` or ``None`` when the
        firmware does not expose the capture service.
    """
    if ble_backend() == "bleak":
        return await _bleak_read_capture_press(address)
    return await _winrt_read_capture_press(address)


async def _winrt_read_capture_press(
    address: str,
) -> Optional[Tuple[int, int, Optional[int]]]:
    """Read the KeyMapper capture characteristic from the keyboard over BLE.

    KeyMapper firmware publishes the last pressed key position plus a press
    counter on a custom GATT characteristic (service UUID starting
    ``6b65796d-0001``); the app polls it while a capture mode is active.

    Args:
        address: Bluetooth MAC of the paired keyboard.

    Returns:
        Tuple ``(press_counter, key_position, active_layer_mask)`` — the mask
        is ``None`` on firmware predating layer reporting — or ``None`` when
        the firmware does not expose the capture service at all.

    Raises:
        KeyMapperError: When the device cannot be opened over Bluetooth, or on
            platforms without the WinRT Bluetooth stack.
    """
    import uuid as uuid_module

    try:
        from winrt.windows.devices.bluetooth import (  # type: ignore[import-untyped]
            BluetoothCacheMode,
            BluetoothLEDevice,
        )
        from winrt.windows.storage.streams import DataReader  # type: ignore[import-untyped]
    except ImportError as exc:
        raise KeyMapperError(
            "Bluetooth reads require the Windows Bluetooth stack (WinRT); "
            "battery and capture features are unavailable on this platform"
        ) from exc

    service_uuid = uuid_module.UUID(CAPTURE_GATT_SERVICE_UUID)
    char_uuid = uuid_module.UUID(CAPTURE_GATT_CHAR_UUID)

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        int(address.replace(":", ""), 16)
    )
    if device is None:
        raise KeyMapperError(f"Bluetooth device {address} could not be opened")
    try:
        # Uncached discovery: Windows caches a bonded device's GATT layout,
        # and the capture service arrives via a firmware update — a cached
        # lookup would keep reporting it missing forever.
        try:
            services = await device.get_gatt_services_for_uuid_with_cache_mode_async(
                service_uuid, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            services = await device.get_gatt_services_for_uuid_async(service_uuid)
        if services.services.size == 0:
            return None
        service = services.services[0]
        try:
            chars = await service.get_characteristics_for_uuid_with_cache_mode_async(
                char_uuid, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            chars = await service.get_characteristics_for_uuid_async(char_uuid)
        if chars.characteristics.size == 0:
            return None
        characteristic = chars.characteristics[0]
        try:
            result = await characteristic.read_value_with_cache_mode_async(
                BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            result = await characteristic.read_value_async()
        # 0 is GattCommunicationStatus.SUCCESS; anything else has no payload.
        if int(getattr(result, "status", 0)) != 0:
            return None
        reader = DataReader.from_buffer(result.value)
        available = int(reader.unconsumed_buffer_length)
        if available < 6:
            return None
        raw = bytes(int(reader.read_byte()) for _ in range(available))
        counter = int.from_bytes(raw[0:4], "little")
        position = int.from_bytes(raw[4:6], "little")
        layers = int.from_bytes(raw[6:10], "little") if len(raw) >= 10 else None
        return counter, position, layers
    finally:
        device.close()


async def start_capture_watch(state: AppState) -> bool:
    """Subscribe to capture notifications (backend dispatch).

    Args:
        state: Shared application state.

    Returns:
        True when the subscription is live; False when the firmware lacks
        the capture service or notify support.
    """
    if state.capture_watch is not None:
        return True
    if ble_backend() == "bleak":
        return await _bleak_start_capture_watch(state)
    return await _winrt_start_capture_watch(state)


async def _winrt_start_capture_watch(state: AppState) -> bool:
    """Subscribe to the keyboard's capture notifications over BLE.

    Every key press then arrives as a GATT notification within tens of
    milliseconds and is forwarded into ``state.events`` as a
    ``CAPTURE_PRESS`` event (which the UI websocket streams onward). This is
    what makes Cyboard-driven Super Fast Assign precise: polling can tell
    THAT a key was pressed but not WHEN, so a fast-moving user could see a
    press paired with the wrong on-screen target.

    Args:
        state: Shared application state; the live subscription handles are
            stored in ``state.capture_watch``.

    Returns:
        True when the subscription is live; False when the firmware does not
        expose the capture service or has no notify support.

    Raises:
        KeyMapperError: When the keyboard is not paired, cannot be opened, or
            the platform lacks the WinRT Bluetooth stack.
    """
    import uuid as uuid_module

    if state.capture_watch is not None:
        return True
    try:
        from winrt.windows.devices.bluetooth import (  # type: ignore[import-untyped]
            BluetoothCacheMode,
            BluetoothLEDevice,
        )
        from winrt.windows.devices.bluetooth.genericattributeprofile import (  # type: ignore[import-untyped]
            GattCharacteristicProperties,
            GattClientCharacteristicConfigurationDescriptorValue,
        )
        from winrt.windows.storage.streams import DataReader  # type: ignore[import-untyped]
    except ImportError as exc:
        raise KeyMapperError(
            "Bluetooth subscriptions require the Windows Bluetooth stack "
            "(WinRT); live capture is unavailable on this platform"
        ) from exc

    if state.ble_address is None:
        state.ble_address = find_ble_address(state.params.device.product_hint)
    if state.ble_address is None:
        raise KeyMapperError("keyboard not found among paired Bluetooth devices")

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        int(state.ble_address.replace(":", ""), 16)
    )
    if device is None:
        raise KeyMapperError(
            f"Bluetooth device {state.ble_address} could not be opened"
        )
    try:
        service_uuid = uuid_module.UUID(CAPTURE_GATT_SERVICE_UUID)
        char_uuid = uuid_module.UUID(CAPTURE_GATT_CHAR_UUID)
        try:
            services = await device.get_gatt_services_for_uuid_with_cache_mode_async(
                service_uuid, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            services = await device.get_gatt_services_for_uuid_async(service_uuid)
        if services.services.size == 0:
            device.close()
            return False
        service = services.services[0]
        try:
            chars = await service.get_characteristics_for_uuid_with_cache_mode_async(
                char_uuid, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            chars = await service.get_characteristics_for_uuid_async(char_uuid)
        if chars.characteristics.size == 0:
            device.close()
            return False
        characteristic = chars.characteristics[0]
        if not (
            int(characteristic.characteristic_properties)
            & int(GattCharacteristicProperties.NOTIFY)
        ):
            # Firmware predating the notify upgrade: polling still works.
            device.close()
            return False

        def _on_value_changed(sender: Any, args: Any) -> None:
            """Forward one press notification into the event queue."""
            try:
                reader = DataReader.from_buffer(args.characteristic_value)
                available = int(reader.unconsumed_buffer_length)
                raw = bytes(int(reader.read_byte()) for _ in range(available))
            except Exception:  # noqa: BLE001 - a torn read only skips one event
                return
            if len(raw) < 6:
                return
            counter = int.from_bytes(raw[0:4], "little")
            position = int.from_bytes(raw[4:6], "little")
            layers = (
                int.from_bytes(raw[6:10], "little") if len(raw) >= 10 else None
            )
            state.events.put(
                ConnectionEvent(
                    NotificationKind.CAPTURE_PRESS,
                    {"counter": counter, "position": position, "layers": layers},
                )
            )

        token = characteristic.add_value_changed(_on_value_changed)
        status = await characteristic.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
        )
        # 0 is GattCommunicationStatus.SUCCESS.
        if int(status) != 0:
            characteristic.remove_value_changed(token)
            device.close()
            raise KeyMapperError(
                f"could not enable capture notifications (status {int(status)})"
            )
        state.capture_watch = (device, characteristic, token)
        state.logger.info("Capture press stream subscribed over Bluetooth")
        return True
    except Exception:
        device.close()
        raise


async def resync_trackballs(state: AppState, quiet: bool = False) -> bool:
    """Re-apply the staged trackball settings to the keyboard, best effort.

    Called after every (re)connect and periodically while connected: a half
    waking from deep sleep reboots, and any drift between the keyboard's
    live config and the staged settings is healed by re-writing them over
    the live channel whenever the app is running.

    Args:
        state: Shared application state.
        quiet: Suppress the success log line (periodic background syncs).

    Returns:
        True when the settings were applied live; False when they could not
        be (unsupported firmware, keyboard unreachable) — never raises.
    """
    payload = trackball_config_payload(state.trackballs)
    try:
        if state.trackball_writer is not None:
            applied = state.trackball_writer(payload)
        elif state.fake_device is not None:
            # Demo/test mode must never write to a real keyboard.
            return False
        else:
            await ensure_ble_identifier(state)
            if state.ble_address is None:
                return False
            applied = await write_trackball_config(state.ble_address, payload)
        if applied and not quiet:
            state.logger.info("Trackball settings re-synced")
        return bool(applied)
    except Exception as exc:  # noqa: BLE001 - resync is opportunistic
        if not quiet:
            state.logger.warning(f"Trackball re-sync skipped: {exc}")
        return False


def stop_capture_watch(state: AppState) -> None:
    """Tear down the live capture subscription, if any.

    Args:
        state: Shared application state holding the watch handles.
    """
    watch = state.capture_watch
    state.capture_watch = None
    if watch is None:
        return
    device, characteristic, token = watch
    if characteristic is not None and token is not None:
        try:
            characteristic.remove_value_changed(token)
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
    try:
        # WinRT devices and _BleakWatchHandle both expose close().
        device.close()
    except Exception:  # noqa: BLE001 - best-effort teardown
        pass
    state.logger.info("Capture press stream unsubscribed")


async def write_trackball_config(address: str, payload: bytes) -> bool:
    """Write the trackball settings payload (backend dispatch).

    Args:
        address: The keyboard's Bluetooth identifier.
        payload: Payload from :func:`trackball_config_payload`.

    Returns:
        True when the firmware accepted the write; False when it lacks the
        live config service.
    """
    if ble_backend() == "bleak":
        return await _bleak_write_trackball_config(address, payload)
    return await _winrt_write_trackball_config(address, payload)


async def _winrt_write_trackball_config(address: str, payload: bytes) -> bool:
    """Write the trackball settings payload to the keyboard over BLE.

    KeyMapper firmware exposes a writable config characteristic (service UUID
    starting ``6b65796d-0003``); a successful write applies the new trackball
    behavior instantly and the keyboard persists it across restarts.

    Args:
        address: Bluetooth MAC of the paired keyboard.
        payload: Payload from :func:`trackball_config_payload`.

    Returns:
        True when the firmware accepted the write; False when it does not
        expose the config service (builds predating the live channel).

    Raises:
        KeyMapperError: When the device cannot be opened over Bluetooth, the
            firmware rejects the write, or the platform lacks the WinRT
            Bluetooth stack.
    """
    import uuid as uuid_module

    try:
        from winrt.windows.devices.bluetooth import (  # type: ignore[import-untyped]
            BluetoothCacheMode,
            BluetoothLEDevice,
        )
        from winrt.windows.storage.streams import (  # type: ignore[import-untyped]
            DataWriter,
        )
    except ImportError as exc:
        raise KeyMapperError(
            "Bluetooth writes require the Windows Bluetooth stack (WinRT); "
            "live trackball configuration is unavailable on this platform"
        ) from exc

    service_uuid = uuid_module.UUID(TRACKBALL_GATT_SERVICE_UUID)
    char_uuid = uuid_module.UUID(TRACKBALL_GATT_CHAR_UUID)

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        int(address.replace(":", ""), 16)
    )
    if device is None:
        raise KeyMapperError(f"Bluetooth device {address} could not be opened")
    try:
        # Uncached discovery: the config service arrives via a firmware
        # update, and Windows' cached GATT layout would hide it forever.
        try:
            services = await device.get_gatt_services_for_uuid_with_cache_mode_async(
                service_uuid, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            services = await device.get_gatt_services_for_uuid_async(service_uuid)
        if services.services.size == 0:
            return False
        service = services.services[0]
        try:
            chars = await service.get_characteristics_for_uuid_with_cache_mode_async(
                char_uuid, BluetoothCacheMode.UNCACHED
            )
        except AttributeError:
            chars = await service.get_characteristics_for_uuid_async(char_uuid)
        if chars.characteristics.size == 0:
            return False
        characteristic = chars.characteristics[0]
        writer = DataWriter()
        # winrt's projection wants a bytes-like buffer, never a list.
        writer.write_bytes(bytes(payload))
        status = await characteristic.write_value_async(writer.detach_buffer())
        # 0 is GattCommunicationStatus.SUCCESS.
        if int(status) != 0:
            raise KeyMapperError(
                f"keyboard rejected the trackball config write (status {int(status)})"
            )
        return True
    finally:
        device.close()


def run_command(
    args: List[str], cwd: Optional[Path] = None, timeout_s: float = 120.0
) -> tuple[int, str, str]:
    """Run an external command and capture its output.

    Args:
        args: Command and arguments (no shell interpretation).
        cwd: Working directory, or ``None`` for the current one.
        timeout_s: Seconds before the command is killed.

    Returns:
        Tuple of (exit code, stdout text, stderr text). A timeout returns exit
        code 124 with the timeout message in stderr; a missing executable
        returns exit code 127 with the OS error in stderr.
    """
    import subprocess

    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"timeout after {timeout_s}s: {exc}"
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def github_status(runner: Callable[..., tuple[int, str, str]]) -> Dict[str, Any]:
    """Check gh CLI availability and authentication.

    Args:
        runner: Command runner (see :func:`run_command`).

    Returns:
        Mapping with ``available`` (gh runs), ``authenticated`` and ``login``
        (GitHub username or ``None``).
    """
    code, out, _ = runner(["gh", "api", "user", "-q", ".login"], timeout_s=30.0)
    if code == 127:
        return {"available": False, "authenticated": False, "login": None}
    login = out.strip() or None
    return {"available": True, "authenticated": code == 0 and login is not None, "login": login}


def ensure_firmware_repo(
    params: Parameters, workspace_dir: Path, runner: Callable[..., tuple[int, str, str]]
) -> Path:
    """Ensure the firmware GitHub repository exists and is cloned locally.

    Creates the repository from the official template when missing (private by
    default), then clones it under ``workspace_dir/repo`` when no clone exists,
    or fast-forwards the existing clone.

    Args:
        params: Validated configuration (repo and template names).
        workspace_dir: Firmware workspace root.
        runner: Command runner.

    Returns:
        Path of the local clone.

    Raises:
        KeyMapperError: When gh is unauthenticated or any git/gh step fails.
    """
    status = github_status(runner)
    if not status["available"]:
        raise KeyMapperError("GitHub CLI (gh) is not installed or not on PATH")
    if not status["authenticated"]:
        raise KeyMapperError("GitHub CLI is not authenticated; run: gh auth login")
    login = status["login"]
    repo_full = f"{login}/{params.firmware.repo_name}"

    code, _, _ = runner(["gh", "repo", "view", repo_full, "--json", "name"], timeout_s=30.0)
    if code != 0:
        template = f"{params.firmware.template_owner}/{params.firmware.template_repo}"
        code, _, err = runner(
            ["gh", "repo", "create", repo_full, "--template", template, "--private"],
            timeout_s=60.0,
        )
        if code != 0:
            raise KeyMapperError(f"could not create firmware repo from template: {err.strip()}")

    repo_dir = workspace_dir / "repo"
    if not (repo_dir / ".git").is_dir():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        attempts = 0
        while True:
            code, _, err = runner(
                ["gh", "repo", "clone", repo_full, str(repo_dir)], timeout_s=120.0
            )
            if code == 0:
                break
            attempts += 1
            # Template instantiation is asynchronous on GitHub's side; the first
            # clone directly after creation can race it.
            if attempts >= 5:
                raise KeyMapperError(f"could not clone firmware repo: {err.strip()}")
            time.sleep(3.0)
    else:
        code, _, err = runner(["git", "pull", "--ff-only"], cwd=repo_dir, timeout_s=120.0)
        if code != 0:
            raise KeyMapperError(f"could not update firmware repo clone: {err.strip()}")
    return repo_dir


def push_firmware_config(
    repo_dir: Path, workspace_dir: Path, runner: Callable[..., tuple[int, str, str]]
) -> str:
    """Copy generated config files into the clone, commit, and push.

    Commits are authored as the neutral app identity (``GIT_IDENTITY_NAME`` /
    ``GIT_IDENTITY_EMAIL``), overriding the machine's git configuration so no
    personal name or address reaches the repository history.

    Args:
        repo_dir: Local clone of the firmware repository.
        workspace_dir: Workspace holding the generated ``config`` files.
        runner: Command runner.

    Returns:
        The pushed commit SHA.

    Raises:
        KeyMapperError: When any git step fails.
    """
    import shutil

    for name in (
        "imprint.keymap",
        "imprint.conf",
        "imprint_left.overlay",
        "imprint_right.overlay",
    ):
        source = workspace_dir / "config" / name
        if source.is_file():
            shutil.copyfile(source, repo_dir / "config" / name)
    for relative in (
        Path("zephyr") / "module.yml",
        Path("CMakeLists.txt"),
        Path("Kconfig"),
        Path("src") / "battery_alert_blink.c",
        Path("src") / "rgb_remember.c",
        Path("src") / "capture_gatt.c",
        Path("src") / "trackball_runtime.c",
        Path("src") / "underglow_wake_sync.c",
        Path("src") / "numlock_guard.c",
        Path("src") / "battcheck.c",
        Path("dts") / "bindings" / "behaviors" / "zmk,behavior-rgb-remember.yaml",
        Path("dts") / "bindings" / "behaviors" / "zmk,behavior-numlock-guard.yaml",
        Path("dts") / "bindings" / "behaviors" / "zmk,behavior-battcheck.yaml",
        Path("dts")
        / "bindings"
        / "input_processors"
        / "keymapper,input-processor-trackball.yaml",
        Path("dts") / "bindings" / "vendor-prefixes.txt",
    ):
        source = workspace_dir / relative
        if source.is_file():
            target = repo_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    code, _, err = runner(["git", "add", "-A"], cwd=repo_dir, timeout_s=30.0)
    if code != 0:
        raise KeyMapperError(f"git add failed: {err.strip()}")
    code, out, err = runner(["git", "status", "--porcelain"], cwd=repo_dir, timeout_s=30.0)
    if code != 0:
        raise KeyMapperError(f"git status failed: {err.strip()}")
    if out.strip():
        code, _, err = runner(
            [
                "git",
                "-c",
                f"user.name={GIT_IDENTITY_NAME}",
                "-c",
                f"user.email={GIT_IDENTITY_EMAIL}",
                "commit",
                "-m",
                "Update generated keymap and configuration",
            ],
            cwd=repo_dir,
            timeout_s=30.0,
        )
        if code != 0:
            raise KeyMapperError(f"git commit failed: {err.strip()}")
        code, _, err = runner(["git", "push"], cwd=repo_dir, timeout_s=120.0)
        if code != 0:
            raise KeyMapperError(f"git push failed: {err.strip()}")
    code, out, err = runner(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout_s=30.0)
    if code != 0:
        raise KeyMapperError(f"git rev-parse failed: {err.strip()}")
    return out.strip()


def wait_for_build(
    repo_full: str,
    commit_sha: str,
    timeout_s: float,
    runner: Callable[..., tuple[int, str, str]],
    poll_interval_s: float = 10.0,
) -> Dict[str, Any]:
    """Wait for the GitHub Actions build of ``commit_sha`` to finish.

    Args:
        repo_full: ``owner/name`` of the firmware repository.
        commit_sha: Commit whose workflow run to wait for.
        timeout_s: Overall wait budget in seconds.
        runner: Command runner.
        poll_interval_s: Seconds between polls (1–60 sensible; the documented
            build takes ~2 minutes, so 10 s keeps latency and API load balanced).

    Returns:
        Mapping with ``run_id``, ``status``, ``conclusion``.

    Raises:
        KeyMapperError: On timeout or when the run concluded unsuccessfully.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code, out, _ = runner(
            [
                "gh", "run", "list", "--repo", repo_full, "--commit", commit_sha,
                "--json", "databaseId,status,conclusion", "--limit", "1",
            ],
            timeout_s=30.0,
        )
        if code == 0 and out.strip():
            runs = json.loads(out)
            if runs:
                run = runs[0]
                if run.get("status") == "completed":
                    if run.get("conclusion") != "success":
                        raise KeyMapperError(
                            f"firmware build concluded: {run.get('conclusion')} "
                            f"(run {run.get('databaseId')})"
                        )
                    return {
                        "run_id": run["databaseId"],
                        "status": run["status"],
                        "conclusion": run["conclusion"],
                    }
        time.sleep(poll_interval_s)
    raise KeyMapperError(f"firmware build did not complete within {timeout_s}s")


def download_firmware(
    repo_full: str,
    run_id: int,
    dest_dir: Path,
    runner: Callable[..., tuple[int, str, str]],
) -> List[Path]:
    """Download the built firmware artifacts and return the UF2 files.

    Args:
        repo_full: ``owner/name`` of the firmware repository.
        run_id: Workflow run to download artifacts from.
        dest_dir: Destination directory; cleared before downloading because the
            downloader refuses to overwrite files left by earlier runs.
        runner: Command runner.

    Returns:
        Paths of every ``.uf2`` file found in the downloaded artifacts.

    Raises:
        KeyMapperError: When the download fails or produced no UF2 files.
    """
    import shutil

    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    code, _, err = runner(
        ["gh", "run", "download", str(run_id), "--repo", repo_full, "--dir", str(dest_dir)],
        timeout_s=300.0,
    )
    if code != 0:
        raise KeyMapperError(f"artifact download failed: {err.strip()}")
    uf2_files = sorted(dest_dir.rglob("*.uf2"))
    if not uf2_files:
        raise KeyMapperError("artifact download contained no .uf2 files")
    return uf2_files


def find_bootloader_drive(candidates: Optional[List[Path]] = None) -> Optional[Path]:
    """Find the keyboard's UF2 bootloader mass-storage drive.

    A half in bootloader mode (double-tap reset) mounts as a drive containing
    ``INFO_UF2.TXT``; the Imprint reports ``Board-ID: nRF52840-assimilator-ble``.

    Args:
        candidates: Drive roots to probe; ``None`` scans ``D:``–``Z:`` drive
            letters on Windows and the usual removable-media mount points
            (``/media/<user>/*``, ``/run/media/<user>/*``, ``/Volumes/*``)
            elsewhere. Fixed drives are skipped by the marker-file check.

    Returns:
        The drive root, or ``None`` when no bootloader drive is present.
    """
    if candidates is None:
        if os.name == "nt":
            candidates = [Path(f"{letter}:/") for letter in "DEFGHIJKLMNOPQRSTUVWXYZ"]
        else:
            candidates = []
            for base in (Path("/media"), Path("/run/media"), Path("/Volumes")):
                try:
                    if not base.is_dir():
                        continue
                    for entry in base.iterdir():
                        if entry.is_dir():
                            candidates.append(entry)
                            candidates.extend(
                                child for child in entry.iterdir() if child.is_dir()
                            )
                except OSError:
                    continue
    for root in candidates:
        try:
            marker = root / BOOTLOADER_MARKER_FILENAME
            if marker.is_file() and BOOTLOADER_BOARD_SIGNATURE in marker.read_text(
                encoding="utf-8", errors="replace"
            ):
                return root
        except OSError:
            continue
    return None


def flash_uf2(
    uf2_path: Path,
    drive_root: Path,
    disappearance_timeout_s: float = 60.0,
) -> bool:
    """Copy a UF2 onto the bootloader drive and wait for the reboot.

    The bootloader consumes the file and remounts as a keyboard; the drive
    disappearing is the success signal. Copy errors surfaced by the OS at the
    very end of the copy are expected (the device resets before acknowledging)
    and are treated as success when the drive then disappears.

    Args:
        uf2_path: Firmware file to flash.
        drive_root: Bootloader drive root from :func:`find_bootloader_drive`.
        disappearance_timeout_s: Seconds to wait for the drive to vanish.

    Returns:
        ``True`` when the drive disappeared (flash accepted), ``False`` when it
        is still present after the timeout.
    """
    import shutil

    target = drive_root / uf2_path.name
    try:
        shutil.copyfile(uf2_path, target)
    except OSError:
        pass
    deadline = time.monotonic() + disappearance_timeout_s
    while time.monotonic() < deadline:
        try:
            if not (drive_root / BOOTLOADER_MARKER_FILENAME).is_file():
                return True
        except OSError:
            return True
        time.sleep(1.0)
    return False


def create_app(state: AppState) -> FastAPI:
    """Build the FastAPI application serving the KeyMapper API and web UI.

    Args:
        state: Shared application state; mutated by the endpoints and the
            background tasks started in the app's lifespan.

    Returns:
        The configured FastAPI instance (REST under ``/api``, WebSocket at
        ``/ws``, static web UI at ``/`` when the build exists).
    """
    ui_sockets: Set[WebSocket] = set()

    def _require_client() -> StudioClient:
        """Return the live client or fail the request with 409."""
        if state.client is None or not state.client.connected:
            raise HTTPException(status_code=409, detail="no keyboard connected")
        return state.client

    def _keymap_payload() -> Dict[str, Any]:
        """Read the current keymap as a JSON-serializable mapping."""
        return _require_client().get_keymap().to_dict()

    async def _broadcast(message: Dict[str, Any]) -> None:
        """Send ``message`` to every connected UI socket, dropping dead ones."""
        dead: List[WebSocket] = []
        for socket in ui_sockets:
            try:
                await socket.send_json(message)
            except Exception:  # noqa: BLE001 - any send failure means the socket is gone
                dead.append(socket)
        for socket in dead:
            ui_sockets.discard(socket)

    async def _event_pump() -> None:
        """Bridge device notifications from the reader thread to UI sockets."""
        while True:
            try:
                event: ConnectionEvent = state.events.get_nowait()
            except Empty:
                await asyncio.sleep(0.05)
                continue
            if event.kind == NotificationKind.CAPTURE_PRESS:
                # High-frequency, self-contained event: broadcast as a slim
                # message instead of a full state snapshot.
                await _broadcast(
                    {"event": "capture_press", **dict(event.value)}
                )
                continue
            if event.kind == NotificationKind.LOCK_STATE_CHANGED:
                state.lock_state = event.value
            elif event.kind == NotificationKind.UNSAVED_CHANGES_CHANGED:
                state.unsaved_changes = bool(event.value)
            elif event.kind == NotificationKind.CONNECTION_LOST:
                state.logger.warning(f"Keyboard connection lost: {event.value}")
                stale = state.client
                state.client = None
                state.device_info = None
                state.lock_state = None
                state.port_name = None
                if stale is not None:
                    await asyncio.to_thread(stale.close)
            await _broadcast(snapshot_full_state(state))

    async def _trackball_resync_loop() -> None:
        """Periodically re-write the staged trackball settings.

        The keyboard's live config can drift after naps and reboots; while
        the app runs, this quietly restores the programmed behavior within a
        few minutes even when no reconnect event fires.
        """
        while True:
            await asyncio.sleep(TRACKBALL_RESYNC_INTERVAL_S)
            if state.fake_device is None and state.client is not None:
                await resync_trackballs(state, quiet=True)

    async def _reconnect_loop() -> None:
        """Keep trying to attach to the physical keyboard while disconnected."""
        while True:
            if state.fake_device is None and (
                state.client is None or not state.client.connected
            ):
                await asyncio.to_thread(connect_device, state)
                if state.client is not None and state.client.connected:
                    # The keyboard may have rebooted (deep sleep, reflash):
                    # push the staged trackball settings back, best effort.
                    asyncio.create_task(resync_trackballs(state))
                await _broadcast(snapshot_full_state(state))
            await asyncio.sleep(state.params.device.reconnect_interval_s)

    async def _idle_shutdown_watch() -> None:
        """Exit the server after the configured idle period without UI clients.

        The grace period is multiplied by ``PRE_FIRST_CLIENT_GRACE_MULTIPLIER``
        until the first UI client ever connects, so a slow first browser start
        is not cut off. The server never exits while a firmware build/flash job
        is in flight, regardless of UI clients.
        """
        while True:
            await asyncio.sleep(1.0)
            if state.ui_clients > 0 or state.request_shutdown is None:
                continue
            if state.firmware_job.get("phase") not in (None, "done", "error"):
                continue
            grace = state.params.server.ui_disconnect_shutdown_s
            if not state.ever_had_ui_client:
                grace *= PRE_FIRST_CLIENT_GRACE_MULTIPLIER
            idle_s = time.monotonic() - state.last_ui_disconnect_monotonic
            if idle_s >= grace:
                state.logger.info("No UI clients; shutting down")
                state.request_shutdown()
                return

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Start and stop the background tasks with the server."""
        tasks = [
            asyncio.create_task(_event_pump()),
            asyncio.create_task(_reconnect_loop()),
            asyncio.create_task(_trackball_resync_loop()),
            asyncio.create_task(_idle_shutdown_watch()),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            stop_capture_watch(state)

    app = FastAPI(title="KeyMapper", lifespan=_lifespan)

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        """Liveness probe used by the launcher to detect a running server."""
        return {
            "status": "ok",
            "connected": state.client is not None and state.client.connected,
            "fake": state.fake_device is not None,
        }

    @app.get("/api/state")
    def get_state() -> Dict[str, Any]:
        """Current connection/lock/unsaved snapshot (same shape as WS pushes)."""
        return snapshot_full_state(state)

    @app.post("/api/connect")
    def connect_now() -> Dict[str, Any]:
        """Trigger an immediate discovery attempt instead of waiting for the loop."""
        if state.fake_device is None:
            connect_device(state)
        return snapshot_full_state(state)

    @app.get("/api/keymap")
    def get_keymap() -> Dict[str, Any]:
        """Full keymap including unsaved edits (requires unlocked keyboard)."""
        return _keymap_payload()

    @app.get("/api/layouts")
    def get_layouts() -> Dict[str, Any]:
        """All physical layouts plus the active index (requires unlock)."""
        return _require_client().get_physical_layouts().to_dict()

    @app.get("/api/behaviors")
    def get_behaviors() -> List[Dict[str, Any]]:
        """Catalog of every compiled-in behavior with parameter metadata."""
        client = _require_client()
        return [client.get_behavior_details(b).to_dict() for b in client.list_behavior_ids()]

    @app.put("/api/binding")
    def put_binding(body: ApiBindingUpdate) -> Dict[str, Any]:
        """Assign one binding (pending until saved)."""
        _require_client().set_layer_binding(
            body.layer_id,
            body.key_position,
            Binding(body.behavior_id, body.param1, body.param2),
        )
        state.unsaved_changes = True
        return {"ok": True}

    @app.post("/api/bulk_set")
    def bulk_set(body: ApiBulkSet) -> Dict[str, Any]:
        """Assign the same binding to every key of one layer (pending until saved)."""
        client = _require_client()
        keymap = client.get_keymap()
        layer = next((l for l in keymap.layers if l.layer_id == body.layer_id), None)
        if layer is None:
            raise HTTPException(status_code=404, detail="unknown layer")
        binding = Binding(body.behavior_id, body.param1, body.param2)
        for position in range(len(layer.bindings)):
            client.set_layer_binding(body.layer_id, position, binding)
        state.unsaved_changes = True
        return {"ok": True, "positions": len(layer.bindings)}

    @app.post("/api/layer/add")
    def layer_add() -> Dict[str, Any]:
        """Enable one additional (reserved) layer."""
        index, layer = _require_client().add_layer()
        state.unsaved_changes = True
        return {"index": index, "layer": layer.to_dict()}

    @app.post("/api/layer/remove")
    def layer_remove(body: ApiLayerRemove) -> Dict[str, Any]:
        """Disable the layer at the given position (restorable until reset)."""
        _require_client().remove_layer(body.layer_index)
        state.unsaved_changes = True
        return {"ok": True}

    @app.post("/api/layer/restore")
    def layer_restore(body: ApiLayerRestore) -> Dict[str, Any]:
        """Re-enable a previously removed layer at the given position."""
        layer = _require_client().restore_layer(body.layer_id, body.at_index)
        state.unsaved_changes = True
        return {"layer": layer.to_dict()}

    @app.post("/api/layer/move")
    def layer_move(body: ApiLayerMove) -> Dict[str, Any]:
        """Reorder layers; returns the confirmed keymap."""
        keymap = _require_client().move_layer(body.start_index, body.dest_index)
        state.unsaved_changes = True
        return keymap.to_dict()

    @app.put("/api/layer/name")
    def layer_name(body: ApiLayerName) -> Dict[str, Any]:
        """Rename a layer (pending until saved)."""
        _require_client().set_layer_props(body.layer_id, body.name)
        state.unsaved_changes = True
        return {"ok": True}

    @app.post("/api/save")
    def save() -> Dict[str, Any]:
        """Persist pending edits to the keyboard, writing a backup first."""
        backup_path = backup_keymap(state)
        # backup_keymap succeeded, so state.client is connected — no re-check.
        state.client.save_changes()
        state.unsaved_changes = False
        return {"ok": True, "backup": backup_path.name}

    @app.post("/api/discard")
    def discard() -> Dict[str, Any]:
        """Revert pending edits to the last saved state."""
        _require_client().discard_changes()
        state.unsaved_changes = False
        return {"ok": True}

    @app.post("/api/backup")
    def backup_now() -> Dict[str, Any]:
        """Write a backup bundle of the current keyboard state."""
        path = backup_keymap(state)
        return {"ok": True, "backup": path.name}

    @app.get("/api/backups")
    def backups() -> List[Dict[str, Any]]:
        """List available backups, newest first."""
        return list_backups(state.paths.BACKUPS_DIR)

    @app.get("/api/backups/{name}")
    def backup_content(name: str) -> Dict[str, Any]:
        """Return one backup bundle's full content."""
        return load_backup(state.paths.BACKUPS_DIR, name)

    @app.post("/api/backups/delete")
    def backups_delete(body: ApiBackupNames) -> Dict[str, Any]:
        """Delete the named backup files (irreversible)."""
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        deleted = 0
        for name in body.names:
            if "/" in name or "\\" in name or not name.startswith(BACKUP_FILE_PREFIX):
                continue
            path = state.paths.BACKUPS_DIR / name
            if path.is_file():
                path.unlink()
                deleted += 1
        return {"ok": True, "deleted": deleted}

    @app.get("/api/backups/{name}/download")
    def backup_download(name: str) -> Any:
        """Serve one backup as a downloadable KeyMapper backup file."""
        from fastapi.responses import FileResponse

        if "/" in name or "\\" in name or not name.startswith(BACKUP_FILE_PREFIX):
            raise HTTPException(status_code=404, detail="unknown backup")
        path = state.paths.BACKUPS_DIR / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="unknown backup")
        return FileResponse(path, filename=name, media_type="application/json")

    @app.get("/api/backups/{name}/diff")
    def backup_diff(name: str) -> Dict[str, Any]:
        """Differences between a backup and the keyboard's current keymap.

        Compares layer by stable layer ID and key by position; binding texts are
        rendered with the current firmware's behavior names.
        """
        bundle = load_backup(state.paths.BACKUPS_DIR, name)
        client = _require_client()
        current = client.get_keymap()
        name_by_id: Dict[int, str] = {
            b.behavior_id: b.display_name
            for b in (client.get_behavior_details(i) for i in client.list_behavior_ids())
        }
        backup_names = {
            int(b["behavior_id"]): str(b["display_name"])
            for b in bundle.get("behaviors", [])
        }

        def label(binding: Dict[str, Any], names: Dict[int, str]) -> str:
            behavior = names.get(int(binding["behavior_id"]), f"#{binding['behavior_id']}")
            p1, p2 = int(binding.get("param1", 0)), int(binding.get("param2", 0))
            suffix = f" {p1}" if p1 else ""
            suffix += f" {p2}" if p2 else ""
            return behavior + suffix

        current_by_id = {l.layer_id: l for l in current.layers}
        backup_layers = bundle["keymap"]["layers"]
        changed: List[Dict[str, Any]] = []
        layers_only_in_backup: List[str] = []
        for b_layer in backup_layers:
            layer_id = int(b_layer["layer_id"])
            c_layer = current_by_id.get(layer_id)
            if c_layer is None:
                layers_only_in_backup.append(str(b_layer["name"]))
                continue
            for pos, b_binding in enumerate(b_layer["bindings"]):
                if pos >= len(c_layer.bindings):
                    break
                c_binding = c_layer.bindings[pos]
                if (
                    c_binding.behavior_id != int(b_binding["behavior_id"])
                    or c_binding.param1 != int(b_binding.get("param1", 0))
                    or c_binding.param2 != int(b_binding.get("param2", 0))
                ):
                    changed.append(
                        {
                            "layer": c_layer.name or f"layer id {layer_id}",
                            "position": pos,
                            "current": label(c_binding.to_dict(), name_by_id),
                            "backup": label(b_binding, backup_names),
                        }
                    )
        backup_ids = {int(l["layer_id"]) for l in backup_layers}
        layers_only_current = [
            l.name or f"layer id {l.layer_id}"
            for l in current.layers
            if l.layer_id not in backup_ids
        ]
        return {
            "identical": not changed
            and not layers_only_in_backup
            and not layers_only_current,
            "changed": changed,
            "layers_only_in_backup": layers_only_in_backup,
            "layers_only_on_keyboard": layers_only_current,
        }

    @app.post("/api/backups/restore")
    def backup_restore(body: ApiBackupRestore) -> Dict[str, Any]:
        """Re-apply a backup's bindings and layer names to the keyboard.

        Changes are applied as PENDING edits (review in the editor, then Save).
        Layers are matched by stable layer ID; layers missing from the current
        keymap are skipped and reported.
        """
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        bundle = load_backup(state.paths.BACKUPS_DIR, body.name)
        client = _require_client()
        current = client.get_keymap()
        current_by_id = {l.layer_id: l for l in current.layers}
        applied = 0
        skipped = 0
        skipped_layers: List[str] = []
        for b_layer in bundle["keymap"]["layers"]:
            layer_id = int(b_layer["layer_id"])
            c_layer = current_by_id.get(layer_id)
            if c_layer is None:
                skipped_layers.append(str(b_layer["name"]))
                continue
            for pos, b_binding in enumerate(b_layer["bindings"]):
                if pos >= len(c_layer.bindings):
                    break
                target = Binding(
                    int(b_binding["behavior_id"]),
                    int(b_binding.get("param1", 0)),
                    int(b_binding.get("param2", 0)),
                )
                existing = c_layer.bindings[pos]
                if (
                    existing.behavior_id == target.behavior_id
                    and existing.param1 == target.param1
                    and existing.param2 == target.param2
                ):
                    continue
                try:
                    client.set_layer_binding(layer_id, pos, target)
                    applied += 1
                except StudioRpcError:
                    skipped += 1
            backup_name = str(b_layer.get("name", ""))
            if backup_name and backup_name != c_layer.name:
                try:
                    client.set_layer_props(layer_id, backup_name)
                except StudioRpcError:
                    skipped += 1
        state.unsaved_changes = True
        return {
            "ok": True,
            "applied": applied,
            "skipped_bindings": skipped,
            "skipped_layers": skipped_layers,
        }

    @app.get("/manual")
    def manual() -> Any:
        """Serve the full KeyMapper manual."""
        from fastapi.responses import FileResponse

        manual_path = Path(state.paths.MANUAL_FILE)
        if not manual_path.is_file():
            raise HTTPException(status_code=404, detail="manual not built")
        return FileResponse(manual_path, media_type="text/html")

    @app.post("/api/reset_settings")
    def reset_settings(body: ApiConfirm) -> Dict[str, Any]:
        """Erase Studio-saved settings after taking a fresh backup.

        Destructive: the keyboard falls back to the keymap compiled into its
        firmware. A backup is always written immediately before the reset.
        """
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        backup_path = backup_keymap(state)
        state.client.reset_settings()
        state.unsaved_changes = False
        return {"ok": True, "backup": backup_path.name}

    @app.post("/api/shutdown")
    def shutdown() -> Dict[str, Any]:
        """Ask the server to exit gracefully."""
        if state.request_shutdown is not None:
            state.request_shutdown()
        return {"ok": True}

    @app.post("/api/debug/unlock")
    def debug_unlock() -> Dict[str, Any]:
        """Demo mode only: simulate holding the physical unlock combo.

        Available exclusively when the in-process fake keyboard is active; on a
        real keyboard unlocking is physical by design and this returns 404.
        """
        if state.fake_device is None:
            raise HTTPException(status_code=404, detail="not available on real hardware")
        state.fake_device.press_unlock_combo()
        return {"ok": True}

    def _runner() -> Callable[..., tuple[int, str, str]]:
        """Return the injected process runner or the real subprocess runner."""
        return state.process_runner if state.process_runner is not None else run_command

    def _job_active() -> bool:
        """Whether a firmware background job is currently running."""
        return state.firmware_job.get("phase") not in (None, "done", "error")

    def _resolve_generation_backup(backup_name: Optional[str]) -> Dict[str, Any]:
        """Pick the backup bundle a firmware generation should bake in.

        Preference order: the explicitly named backup; a fresh backup from the
        connected keyboard; the newest existing backup file.
        """
        backups_dir = state.paths.BACKUPS_DIR
        if backup_name is not None:
            return load_backup(backups_dir, backup_name)
        if state.client is not None and state.client.connected:
            try:
                return json.loads(backup_keymap(state).read_text(encoding="utf-8"))
            except (StudioLockedError, StudioTimeoutError, HTTPException):
                pass
        existing = list_backups(backups_dir)
        if not existing:
            raise HTTPException(
                status_code=409,
                detail="no backup available; connect and unlock the keyboard first",
            )
        return load_backup(backups_dir, existing[0]["name"])

    @app.post("/api/firmware/github_login")
    def github_login() -> Dict[str, Any]:
        """Open a GitHub sign-in (gh CLI web flow) in a new console window.

        The gh CLI stores the resulting credentials in the user's profile, so
        the login survives restarts; KeyMapper itself never sees or stores tokens
        and derives the account dynamically, making the app GitHub-account
        agnostic. The UI polls the wizard status until authentication appears.
        """
        import subprocess

        try:
            subprocess.Popen(
                ["gh", "auth", "login", "--web", "--git-protocol", "https"],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=409,
                detail="GitHub CLI (gh) is not installed; get it from cli.github.com",
            )
        return {"ok": True}

    @app.get("/api/firmware/presets")
    def firmware_presets() -> List[Dict[str, Any]]:
        """Presets for the composer, recomputed on every call.

        Static presets: € plus the Shift-paired accents. Dynamic presets: one
        To-layer and one Momentary-layer color sequence per current keyboard
        layer, using the color a staged sequence already assigns to that layer
        (or a default palette entry) — so they always mirror the current layers
        and colors.
        """
        # Default per-layer hues: blue, red, green, yellow, purple, cyan,
        # orange, magenta; repeating for higher layers.
        palette = [240, 0, 120, 60, 280, 180, 30, 320]
        presets = list(preset_macros())
        for layer in _current_layers_for_presets():
            index = int(layer["index"])
            name = str(layer["name"]) or f"layer {index}"
            color = _staged_layer_color(index) or (
                palette[index % len(palette)],
                100,
                50,
            )
            hue, sat, bri = color
            presets.append(
                layer_color_macro(
                    f"to_layer_{index}",
                    f"To {name} + color",
                    index,
                    hue,
                    sat,
                    bri,
                )
            )
            presets.append(
                mo_layer_color_macro(
                    f"mom_layer_{index}",
                    f"Hold {name} + color",
                    index,
                    hue,
                    sat,
                    bri,
                )
            )
        return [m.to_dict() for m in presets]

    @app.get("/api/firmware/macros")
    def firmware_macros() -> List[Dict[str, Any]]:
        """Macros currently staged for the next firmware generation."""
        return [m.to_dict() for m in state.pending_macros]

    @app.post("/api/firmware/macros")
    def firmware_set_macros(body: ApiMacros) -> Dict[str, Any]:
        """Stage the macro set to compile into the next firmware."""
        macros: List[MacroDefinition] = []
        seen: Set[str] = set()
        for raw in body.macros:
            macro = MacroDefinition.from_dict(raw)
            node = sanitize_dts_node_name(macro.node_name)
            if node in seen:
                raise HTTPException(status_code=400, detail=f"duplicate macro name '{node}'")
            seen.add(node)
            macro.node_name = node
            # Staged names must equal what the firmware will report back
            # (display-name matching links keys to sequences), so the same
            # build-safety sanitization is applied here, not only at render.
            macro.display_name = sanitize_display_name(macro.display_name)
            if not macro.steps:
                raise HTTPException(status_code=400, detail=f"macro '{node}' has no steps")
            if not (0 <= macro.wait_ms <= MACRO_TIMING_MAX_MS) or not (
                0 <= macro.tap_ms <= MACRO_TIMING_MAX_MS
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"macro '{node}': wait/tap times must be 0–{MACRO_TIMING_MAX_MS} ms",
                )
            def _expand(step_list: List[MacroStep], node: str) -> List[MacroStep]:
                """Validate one step list, splitting comma-joined behaviors."""
                expanded: List[MacroStep] = []
                for step in step_list:
                    if step.kind in (MacroStepKind.WAIT_MS, MacroStepKind.TAP_MS):
                        if not (0 <= step.value <= MACRO_TIMING_MAX_MS):
                            raise HTTPException(
                                status_code=400,
                                detail=f"macro '{node}': timing steps must be "
                                f"0–{MACRO_TIMING_MAX_MS} ms",
                            )
                        expanded.append(step)
                        continue
                    if step.kind not in (
                        MacroStepKind.TAP,
                        MacroStepKind.PRESS,
                        MacroStepKind.RELEASE,
                    ):
                        expanded.append(step)
                        continue
                    # A comma-separated list of behaviors in one step is a common
                    # way to write "do these in order"; devicetree forbids commas
                    # inside a cell, so each behavior becomes its own step.
                    for part in split_behavior_list(step.binding):
                        problem = validate_macro_binding(part)
                        if problem is not None:
                            raise HTTPException(
                                status_code=400,
                                detail=f"macro '{node}', step "
                                f"'{step.kind.value}': {problem}",
                            )
                        expanded.append(MacroStep(kind=step.kind, binding=part, value=0))
                return expanded

            macro.steps = _expand(macro.steps, node)
            macro.shifted_steps = _expand(macro.shifted_steps, node)
            if not macro.steps:
                raise HTTPException(status_code=400, detail=f"macro '{node}' has no steps")
            macros.append(macro)
        state.pending_macros = macros
        persist_staged_state(state)
        return {"ok": True, "count": len(macros)}

    @app.get("/api/battery")
    async def battery() -> Dict[str, Any]:
        """Battery percentages of both halves, read over the BLE bond.

        The central half's level is always available on BLE-paired keyboards;
        the right half's appears once firmware with the battery proxy is
        flashed. Independent of the USB configuration link.
        """
        if state.battery_reader is not None:
            levels = state.battery_reader()
        else:
            await ensure_ble_identifier(state)
            if state.ble_address is None:
                return {
                    "halves": [],
                    "detail": "keyboard not found among paired Bluetooth devices",
                }
            try:
                levels = await read_battery_levels(state.ble_address)
            except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
                return {"halves": [], "detail": f"battery read failed: {exc}"}
        labels = ["Left (central)", "Right"]
        halves = [
            {"label": labels[i] if i < len(labels) else f"Peripheral {i}", "percent": p}
            for i, p in enumerate(levels)
        ]
        detail: Optional[str] = None
        if not halves:
            detail = "no battery service readable over Bluetooth"
        elif len(halves) == 1:
            detail = (
                "right-half battery becomes readable after flashing the next "
                "KeyMapper firmware (adds the battery proxy)"
            )
        return {"halves": halves, "detail": detail}

    @app.get("/api/capture/press")
    async def capture_press() -> Dict[str, Any]:
        """Last key pressed on the keyboard itself, read over the BLE bond.

        Returns a press counter and the key position; the UI polls this while
        a capture mode (Super Fast Assign, "Press a key...") is active and
        reacts when the counter changes. ``supported`` is false on firmware
        without the KeyMapper capture service.
        """
        if state.capture_reader is not None:
            try:
                reading = state.capture_reader()
            except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
                return {"supported": False, "detail": f"capture read failed: {exc}"}
        else:
            if state.ble_address is None:
                state.ble_address = find_ble_address(state.params.device.product_hint)
            if state.ble_address is None:
                return {
                    "supported": False,
                    "detail": "keyboard not found among paired Bluetooth devices",
                }
            try:
                reading = await read_capture_press(state.ble_address)
            except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
                return {"supported": False, "detail": f"capture read failed: {exc}"}
        if reading is None:
            return {
                "supported": False,
                "detail": (
                    "this firmware does not expose the capture service yet - "
                    "build and flash the latest KeyMapper firmware to assign "
                    "keys by pressing them on the keyboard"
                ),
            }
        counter, position = reading[0], reading[1]
        layers = reading[2] if len(reading) > 2 else None
        return {
            "supported": True,
            "counter": counter,
            "position": position,
            "layers": layers,
        }

    @app.post("/api/capture/watch")
    async def capture_watch(body: Dict[str, Any]) -> Dict[str, Any]:
        """Start or stop the live capture press stream.

        While on, every key press on the keyboard is pushed to the UI over
        the websocket as a ``capture_press`` event — precise pairing for
        Super Fast Assign. Returns whether the stream is live; firmware
        without notify support reports ``supported: false`` and the UI falls
        back to polling.
        """
        turn_on = bool(body.get("on", True))
        if not turn_on:
            stop_capture_watch(state)
            return {"supported": False, "detail": "watch stopped"}
        if state.capture_watch_starter is not None:
            supported = state.capture_watch_starter()
            return {"supported": supported, "detail": None}
        try:
            supported = await start_capture_watch(state)
        except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
            return {"supported": False, "detail": f"capture watch failed: {exc}"}
        return {
            "supported": supported,
            "detail": None
            if supported
            else "firmware lacks capture notifications - polling instead",
        }

    def _current_layers_for_presets() -> List[Dict[str, Any]]:
        """Layers used to derive per-layer sequence presets.

        Prefers the live keyboard (connected and unlocked); falls back to the
        newest backup file; empty when neither is available.
        """
        if state.client is not None and state.client.connected:
            try:
                keymap = state.client.get_keymap()
                return [
                    {"index": i, "name": l.name} for i, l in enumerate(keymap.layers)
                ]
            except (StudioLockedError, StudioTimeoutError):
                pass
        existing = list_backups(state.paths.BACKUPS_DIR)
        if existing:
            bundle = load_backup(state.paths.BACKUPS_DIR, existing[0]["name"])
            return [
                {"index": i, "name": str(l.get("name", ""))}
                for i, l in enumerate(bundle["keymap"]["layers"])
            ]
        return []

    def _staged_layer_color(layer_index: int) -> Optional[Tuple[int, int, int]]:
        """Color already used by a staged sequence targeting ``layer_index``."""
        layer_re = re.compile(rf"&(?:to|mo)\s+{layer_index}\b")
        color_re = re.compile(r"RGB_COLOR_HSB\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
        for macro in state.pending_macros:
            steps = [*macro.steps, *macro.shifted_steps]
            if any(layer_re.search(s.binding) for s in steps):
                for s in steps:
                    match = color_re.search(s.binding)
                    if match:
                        return tuple(int(g) for g in match.groups())  # type: ignore[return-value]
        return None

    @app.post("/api/firmware/layer_color")
    def set_layer_color(body: ApiLayerColor) -> Dict[str, Any]:
        """Recolor every staged sequence that switches to the given layer.

        Rewrites the ``RGB_COLOR_HSB`` triple in each staged macro whose steps
        reference ``&to <layer>`` or ``&mo <layer>``. Takes effect on the
        keyboard after the next firmware build + flash.
        """
        layer_re = re.compile(rf"&(?:to|mo)\s+{body.layer}\b")
        color_re = re.compile(r"RGB_COLOR_HSB\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)")
        replacement = f"RGB_COLOR_HSB({body.hue},{body.saturation},{body.brightness})"
        updated = 0
        for macro in state.pending_macros:
            steps = [*macro.steps, *macro.shifted_steps]
            if not any(layer_re.search(s.binding) for s in steps):
                continue
            for step in steps:
                new_binding, n = color_re.subn(replacement, step.binding)
                if n:
                    step.binding = new_binding
                    updated += n
        persist_staged_state(state)
        return {"ok": True, "updated": updated}

    @app.post("/api/firmware/sequence_brightness")
    def sequence_brightness(body: ApiBrightness) -> Dict[str, Any]:
        """Rewrite the brightness of every color used by the staged sequences.

        Each ``RGB_COLOR_HSB(h,s,b)`` in the staged macros (plain and shifted
        steps) keeps its hue and saturation and gets the new brightness. Takes
        effect on the keyboard after the next firmware build + flash.
        """
        pattern = re.compile(r"RGB_COLOR_HSB\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
        updated = 0
        for macro in state.pending_macros:
            for step in [*macro.steps, *macro.shifted_steps]:
                new_binding, n = pattern.subn(
                    lambda m: f"RGB_COLOR_HSB({m.group(1)},{m.group(2)},{body.brightness})",
                    step.binding,
                )
                if n:
                    step.binding = new_binding
                    updated += n
        persist_staged_state(state)
        state.logger.info(
            f"Sequence brightness set to {body.brightness} ({updated} colors updated)"
        )
        return {"ok": True, "updated": updated, "brightness": body.brightness}

    @app.get("/api/firmware/battery_alert")
    def get_battery_alert() -> Dict[str, Any]:
        """Current low-battery blink settings (compiled into the next firmware)."""
        return state.battery_alert.model_dump()

    @app.get("/api/firmware/trackballs")
    def get_trackballs() -> Dict[str, Any]:
        """Current trackball settings (compiled into the next firmware)."""
        return state.trackballs.model_dump()

    @app.put("/api/firmware/trackballs")
    async def put_trackballs(body: TrackballConfig) -> Dict[str, Any]:
        """Update and persist the trackball settings, applying them live.

        The settings are always staged for the next firmware build; when the
        connected firmware exposes the live config channel, they are also
        written to the keyboard over Bluetooth and take effect immediately.
        A failed or unsupported live write never fails the save.
        """
        state.trackballs = body
        persist_staged_state(state)
        state.logger.info("Trackball settings updated")
        applied_live = False
        detail: Optional[str] = None
        payload = trackball_config_payload(body)
        if state.trackball_writer is not None:
            try:
                applied_live = state.trackball_writer(payload)
                if not applied_live:
                    detail = (
                        "firmware lacks the live trackball channel - settings "
                        "apply after the next build + flash"
                    )
            except Exception as exc:  # noqa: BLE001 - report, never fail save
                detail = f"live apply failed: {exc}"
        elif state.fake_device is not None:
            # Demo/test mode must NEVER reach for real hardware: the
            # fallback below scans paired Bluetooth devices and would write
            # this (possibly test) config onto a real keyboard.
            detail = "demo mode - settings apply after the next build + flash"
        else:
            await ensure_ble_identifier(state)
            if state.ble_address is None:
                detail = (
                    "keyboard not found among paired Bluetooth devices - "
                    "settings apply after the next build + flash"
                )
            else:
                try:
                    applied_live = await write_trackball_config(
                        state.ble_address, payload
                    )
                    if not applied_live:
                        detail = (
                            "this firmware predates the live trackball channel "
                            "- settings apply after the next build + flash"
                        )
                except Exception as exc:  # noqa: BLE001 - report, never fail save
                    detail = f"live apply failed: {exc}"
        result = state.trackballs.model_dump()
        result["applied_live"] = applied_live
        result["detail"] = detail
        return result

    @app.get("/api/firmware/locking")
    def get_locking() -> Dict[str, Any]:
        """Current Studio-locking setting (compiled into the next firmware)."""
        return state.locking.model_dump()

    @app.put("/api/firmware/locking")
    def put_locking(body: LockingConfig) -> Dict[str, Any]:
        """Update and persist the Studio-locking setting."""
        state.locking = body
        persist_staged_state(state)
        state.logger.info("Studio-locking setting updated")
        return state.locking.model_dump()

    @app.get("/api/firmware/power")
    def get_power() -> Dict[str, Any]:
        """Current idle / deep-sleep settings (compiled into the next firmware)."""
        return state.power.model_dump()

    @app.put("/api/firmware/power")
    def put_power(body: PowerConfig) -> Dict[str, Any]:
        """Update and persist the idle / deep-sleep settings."""
        state.power = body
        persist_staged_state(state)
        state.logger.info("Power settings updated")
        return state.power.model_dump()

    @app.put("/api/firmware/battery_alert")
    def put_battery_alert(body: BatteryAlertConfig) -> Dict[str, Any]:
        """Update and persist the low-battery blink settings."""
        state.battery_alert = body
        persist_staged_state(state)
        return {"ok": True}

    @app.get("/api/firmware/status")
    def firmware_status() -> Dict[str, Any]:
        """Wizard status: background job, GitHub auth, bootloader drive."""
        candidates = state.drive_probe() if state.drive_probe is not None else None
        drive = find_bootloader_drive(candidates)
        job = dict(state.firmware_job)
        if not job.get("uf2_files") and job.get("phase") in (None, "done", "error"):
            # The job dict lives in memory only; firmware downloaded before a
            # server restart is still on disk and still flashable. Never
            # surface stale files while a new build is in flight.
            artifacts = state.paths.FIRMWARE_WORKSPACE_DIR / "artifacts"
            on_disk = sorted(str(p) for p in artifacts.rglob("*.uf2"))
            if on_disk:
                job["uf2_files"] = on_disk
                job.setdefault("phase", "done")
                job.setdefault("detail", "firmware ready (from an earlier build)")
        return {
            "job": job,
            "github": github_status(_runner()),
            "bootloader_drive": str(drive) if drive is not None else None,
            "staged_macros": len(state.pending_macros),
        }

    @app.post("/api/firmware/generate")
    def firmware_generate(body: ApiGenerate) -> Dict[str, Any]:
        """Generate the firmware configuration from a backup plus staged macros."""
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        backup = _resolve_generation_backup(body.backup_name)
        result = materialize_firmware_workspace(
            state.paths.FIRMWARE_WORKSPACE_DIR,
            backup,
            state.pending_macros,
            state.params,
            state.battery_alert,
            state.power,
            state.locking,
            state.trackballs,
        )
        state.logger.info("Firmware configuration generated")
        return result

    @app.post("/api/firmware/build")
    def firmware_build(body: ApiConfirm) -> Dict[str, Any]:
        """Push the generated config to GitHub and build it (background job)."""
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        if _job_active():
            raise HTTPException(status_code=409, detail="a firmware job is already running")
        workspace = state.paths.FIRMWARE_WORKSPACE_DIR
        if not (workspace / "config" / "imprint.keymap").is_file():
            raise HTTPException(status_code=409, detail="generate the configuration first")
        runner = _runner()

        def _build_job() -> None:
            """Repo → push → Actions build → artifact download, with status updates."""
            try:
                state.firmware_job.update(phase="repo", detail="preparing repository", error=None)
                repo_dir = ensure_firmware_repo(state.params, workspace, runner)
                login = github_status(runner)["login"]
                repo_full = f"{login}/{state.params.firmware.repo_name}"
                state.firmware_job.update(phase="push", detail="pushing configuration")
                sha = push_firmware_config(repo_dir, workspace, runner)
                state.firmware_job.update(
                    phase="build", detail=f"building commit {sha[:10]} on GitHub Actions"
                )
                run = wait_for_build(
                    repo_full, sha, state.params.firmware.build_timeout_s, runner
                )
                state.firmware_job.update(phase="download", detail="downloading firmware")
                uf2_files = download_firmware(
                    repo_full, run["run_id"], workspace / "artifacts", runner
                )
                state.firmware_job.update(
                    phase="done",
                    detail="firmware ready",
                    uf2_files=[str(p) for p in uf2_files],
                )
                state.logger.info(f"Firmware built: {[p.name for p in uf2_files]}")
            except Exception as exc:  # noqa: BLE001 - job surface: report, don't crash the server
                state.firmware_job.update(phase="error", error=str(exc))
                state.logger.error(f"Firmware build failed: {exc}")

        state.firmware_job = {"phase": "starting", "detail": "", "error": None, "uf2_files": []}
        threading.Thread(target=_build_job, name="FirmwareBuild", daemon=True).start()
        return {"ok": True}

    @app.post("/api/firmware/flash")
    def firmware_flash(body: ApiFlashFile) -> Dict[str, Any]:
        """Flash one downloaded UF2 onto the bootloader drive currently present."""
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        if "/" in body.file or "\\" in body.file:
            raise HTTPException(status_code=404, detail="unknown firmware file")
        artifacts = state.paths.FIRMWARE_WORKSPACE_DIR / "artifacts"
        matches = [p for p in artifacts.rglob("*.uf2") if p.name == body.file]
        if not matches:
            raise HTTPException(status_code=404, detail="unknown firmware file")
        candidates = state.drive_probe() if state.drive_probe is not None else None
        drive = find_bootloader_drive(candidates)
        if drive is None:
            raise HTTPException(
                status_code=409,
                detail="no bootloader drive found; double-tap the reset button first",
            )
        flashed = flash_uf2(matches[0], drive)
        if not flashed:
            raise HTTPException(status_code=502, detail="drive did not accept the firmware")
        state.logger.info(f"Flashed {body.file}")
        return {"ok": True}

    @app.post("/api/firmware/finalize")
    def firmware_finalize(body: ApiConfirm) -> Dict[str, Any]:
        """After flashing both halves: clear the old stored layout and verify.

        The keyboard-stored Studio layout survives reflashing and would shadow
        the newly baked-in keymap; this clears it (a fresh backup is written
        first) and returns the resulting keymap for verification.
        """
        if not body.confirm:
            raise HTTPException(status_code=400, detail="confirmation required")
        backup_path = backup_keymap(state)
        try:
            state.client.reset_settings()
        except KeyMapperError:
            # The erase can outlast even the slow-RPC budget, or its response
            # frame can be lost while the keyboard is flash-bound. Probe the
            # link; if it is alive, run the reset once more (idempotent) so a
            # lost response does not fail the whole finish.
            state.logger.warning(
                "reset_settings timed out; probing the link and retrying once"
            )
            state.client.get_keymap()
            state.client.reset_settings()
        keymap = state.client.get_keymap()
        state.unsaved_changes = False
        return {"ok": True, "backup": backup_path.name, "keymap": keymap.to_dict()}

    @app.exception_handler(StudioLockedError)
    async def _locked_handler(_: Any, exc: StudioLockedError) -> Any:
        """Map locked-keyboard failures to HTTP 423 (Locked)."""
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=423, content={"detail": str(exc)})

    @app.exception_handler(StudioTimeoutError)
    async def _timeout_handler(_: Any, exc: StudioTimeoutError) -> Any:
        """Map device timeouts to HTTP 504."""
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=504, content={"detail": str(exc)})

    @app.exception_handler(KeyMapperError)
    async def _keymap_error_handler(_: Any, exc: KeyMapperError) -> Any:
        """Map remaining protocol errors to HTTP 502."""
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.websocket("/ws")
    async def websocket_endpoint(socket: WebSocket) -> None:
        """UI event stream: pushes state snapshots; receives heartbeats."""
        await socket.accept()
        ui_sockets.add(socket)
        state.ui_clients += 1
        state.ever_had_ui_client = True
        try:
            await socket.send_json(snapshot_full_state(state))
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            ui_sockets.discard(socket)
            state.ui_clients -= 1
            state.last_ui_disconnect_monotonic = time.monotonic()

    web_dir = state.paths.WEB_DIR
    if (web_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


def run_server() -> None:
    """Boot the KeyMapper backend: paths, config, logger, device, HTTP server.

    Honors ``KEYMAPPER_FAKE_DEVICE=1`` for demo mode. Blocks until the server exits
    (idle shutdown, ``/api/shutdown``, or Ctrl+C).
    """
    import uvicorn

    paths = resolve_core_paths()
    params = ingest_configuration(paths.CONFIG_FILE)
    logger = init_logger(paths.LOGS_DIR)
    state = AppState(params=params, paths=paths, logger=logger)
    load_staged_state(state)

    if os.environ.get(FAKE_DEVICE_ENV_VAR) == "1":
        start_fake_device(state)
    elif not connect_device(state):
        logger.info("No keyboard found yet; scanning continues in the background")

    app = create_app(state)
    config = uvicorn.Config(
        app,
        host=params.server.host,
        port=params.server.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    def _request_shutdown() -> None:
        """Flag the uvicorn server to exit its serve loop."""
        server.should_exit = True

    state.request_shutdown = _request_shutdown
    logger.info(f"KeyMapper server listening on http://{params.server.host}:{params.server.port}")
    try:
        server.run()
    finally:
        if state.client is not None:
            state.client.close()
        if state.fake_device is not None:
            state.fake_device.stop()
        logger.info("KeyMapper server stopped")
        logger.stop()
