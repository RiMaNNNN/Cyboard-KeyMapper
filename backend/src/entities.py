"""Entities for the KeyMapper backend.

This module owns every class, enum, dataclass, and module-level constant used by the
backend, per the project coding rules. It contains:

- ``Framing`` — byte-level frame encoder/decoder for the ZMK Studio RPC transport.
- ``ConfigSection`` / per-section key enums — the declared vocabulary of ``config.yaml``.
- ``Parameters`` (pydantic) — the validated, typed view of every user-tunable value.
- Protocol dataclasses (``DeviceInfo``, ``BehaviorDetails``, ``Binding``, ``Layer``,
  ``Keymap``, ``PhysicalLayout`` …) mirroring the wire messages in plain Python.
- Transports (``SerialTransport``, ``LoopbackTransport``) and the ``StudioClient``
  that speaks the framed-protobuf RPC protocol.
- ``FakeImprint`` — an in-process device implementing the real wire protocol for
  offline tests and demo mode.
- Error types and the ``AppState`` container shared by the API layer.

Third-party dependencies: ``pyserial``, ``pydantic``, ``protobuf`` (via the generated
``zmk_proto`` package).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from queue import Empty, Queue
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

import serial  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, model_validator

from zmk_proto import behaviors_pb2, core_pb2, keymap_pb2, meta_pb2, studio_pb2


# Environment variable that switches the backend to the in-process fake keyboard
# (demo mode / end-to-end tests without hardware). Any value other than "1" is off.
FAKE_DEVICE_ENV_VAR: str = "KEYMAPPER_FAKE_DEVICE"

# Multiplier applied to the idle-shutdown grace period before the first UI client
# ever connects, so a slow first browser start is not cut off.
PRE_FIRST_CLIENT_GRACE_MULTIPLIER: int = 10

# File-name pattern pieces for backup bundles (local machine time in names;
# the bundle's created_utc field stays UTC).
BACKUP_FILE_PREFIX: str = "keymap_backup_"
BACKUP_FILE_SUFFIX: str = ".json"
BACKUP_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"

# UF2 bootloader drive identification: marker file present at the drive root,
# whose text must contain the board-family string "nRF52840" (the Imprint
# reports "Board-ID: nRF52840-assimilator-ble").
BOOTLOADER_MARKER_FILENAME: str = "INFO_UF2.TXT"
BOOTLOADER_BOARD_SIGNATURE: str = "nRF52840"

# Upper bound in milliseconds accepted for macro pacing values (wait/tap times
# and wait_ms/tap_ms steps); devicetree cells are unsigned 32-bit, so negative
# values would silently wrap and are rejected at the API boundary.
MACRO_TIMING_MAX_MS: int = 5000

# Minimum timeout in seconds for RPCs that rewrite large parts of the settings
# flash (reset_settings, save_changes). Erasing or persisting a full 32-layer
# keymap performs thousands of slow flash operations and routinely exceeds the
# ordinary request timeout.
SLOW_RPC_TIMEOUT_S: float = 90.0

# Git identity for commits the app creates (firmware repository pushes). A
# neutral app identity keeps personal names and addresses out of repository
# history regardless of the machine's global git configuration.
GIT_IDENTITY_NAME: str = "KeyMapper"
GIT_IDENTITY_EMAIL: str = "keymapper@users.noreply.github.com"

# KeyMapper's custom BLE GATT identifiers, shared verbatim between the
# firmware templates and the desktop reader/writer code. Changing one side
# alone breaks app-firmware compatibility.
CAPTURE_GATT_SERVICE_UUID: str = "6b65796d-0001-4b4d-8000-000000000001"
CAPTURE_GATT_CHAR_UUID: str = "6b65796d-0002-4b4d-8000-000000000001"
TRACKBALL_GATT_SERVICE_UUID: str = "6b65796d-0003-4b4d-8000-000000000001"
TRACKBALL_GATT_CHAR_UUID: str = "6b65796d-0004-4b4d-8000-000000000001"

# First byte of the trackball config GATT payload; bumped only when the
# payload layout changes incompatibly.
TRACKBALL_CONFIG_VERSION: int = 1

# Seconds between periodic background re-writes of the staged trackball
# settings while the keyboard is connected (self-healing against config
# drift after naps and reboots).
TRACKBALL_RESYNC_INTERVAL_S: float = 180.0


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #
class KeyMapperError(Exception):
    """Base class for every error raised by the KeyMapper backend."""


class StudioTimeoutError(KeyMapperError):
    """The keyboard did not answer an RPC within the configured timeout."""


class StudioLockedError(KeyMapperError):
    """The keyboard rejected a secured RPC because ZMK Studio access is locked."""


class StudioRpcError(KeyMapperError):
    """The keyboard answered an RPC with a protocol-level error condition.

    The ``condition`` attribute carries the ``zmk.meta.ErrorConditions`` enum value
    (GENERIC, RPC_NOT_FOUND, MSG_DECODE_FAILED, MSG_ENCODE_FAILED) or a
    subsystem-specific error-code name when the failure came from a typed response.
    """

    def __init__(self, condition: str) -> None:
        super().__init__(f"Studio RPC error: {condition}")
        self.condition = condition


# --------------------------------------------------------------------------- #
# Frame codec                                                                 #
# --------------------------------------------------------------------------- #
class Framing:
    """Byte-level codec for the ZMK Studio RPC framing.

    Wire format: each protobuf payload is wrapped as ``SOF payload EOF`` where any
    payload byte equal to SOF/ESC/EOF is prefixed with ESC. Values are fixed by the
    firmware (``zmk/app/src/studio/msg_framing.c``): SOF=0xAB, ESC=0xAC, EOF=0xAD.
    """

    SOF: int = 0xAB
    ESC: int = 0xAC
    EOF: int = 0xAD

    @staticmethod
    def encode(payload: bytes) -> bytes:
        """Wrap ``payload`` in a frame, escaping reserved bytes.

        Args:
            payload: Raw protobuf-encoded message bytes.

        Returns:
            The framed byte string ready to be written to the transport.
        """
        out = bytearray([Framing.SOF])
        for b in payload:
            if b in (Framing.SOF, Framing.ESC, Framing.EOF):
                out.append(Framing.ESC)
            out.append(b)
        out.append(Framing.EOF)
        return bytes(out)

    class Decoder:
        """Incremental frame decoder (three-state machine).

        Feed arbitrary byte chunks; complete de-escaped payloads are returned in
        order. Stray bytes outside a frame are ignored; an unexpected SOF inside a
        frame restarts the frame (matching the reference client's error recovery).
        """

        _IDLE: int = 0
        _DATA: int = 1
        _ESCAPED: int = 2

        def __init__(self) -> None:
            self._state: int = Framing.Decoder._IDLE
            self._buf: bytearray = bytearray()

        def feed(self, chunk: bytes) -> List[bytes]:
            """Consume ``chunk`` and return every payload completed by it.

            Args:
                chunk: Any number of raw transport bytes (may split frames anywhere).

            Returns:
                List of complete, de-escaped payloads (possibly empty).
            """
            frames: List[bytes] = []
            for b in chunk:
                if self._state == Framing.Decoder._IDLE:
                    if b == Framing.SOF:
                        self._buf.clear()
                        self._state = Framing.Decoder._DATA
                elif self._state == Framing.Decoder._DATA:
                    if b == Framing.ESC:
                        self._state = Framing.Decoder._ESCAPED
                    elif b == Framing.EOF:
                        frames.append(bytes(self._buf))
                        self._state = Framing.Decoder._IDLE
                    elif b == Framing.SOF:
                        self._buf.clear()
                    else:
                        self._buf.append(b)
                else:  # _ESCAPED
                    self._buf.append(b)
                    self._state = Framing.Decoder._DATA
            return frames


# --------------------------------------------------------------------------- #
# Configuration vocabulary and validated parameters                           #
# --------------------------------------------------------------------------- #
class ConfigSection(str, Enum):
    """Top-level sections of ``config.yaml``."""

    SERVER = "server"
    DEVICE = "device"
    BACKUP = "backup"
    FIRMWARE = "firmware"


class ServerConfigKey(str, Enum):
    """Keys of the ``server`` section of ``config.yaml``."""

    HOST = "host"
    PORT = "port"
    UI_DISCONNECT_SHUTDOWN_S = "ui_disconnect_shutdown_s"


class DeviceConfigKey(str, Enum):
    """Keys of the ``device`` section of ``config.yaml``."""

    USB_VID = "usb_vid"
    USB_PID = "usb_pid"
    PRODUCT_HINT = "product_hint"
    RPC_TIMEOUT_S = "rpc_timeout_s"
    RECONNECT_INTERVAL_S = "reconnect_interval_s"


class BackupConfigKey(str, Enum):
    """Keys of the ``backup`` section of ``config.yaml``."""

    KEEP_LAST = "keep_last"


class FirmwareConfigKey(str, Enum):
    """Keys of the ``firmware`` section of ``config.yaml``."""

    REPO_NAME = "repo_name"
    TEMPLATE_OWNER = "template_owner"
    TEMPLATE_REPO = "template_repo"
    KEYBOARD_TAG = "keyboard_tag"
    BUILD_TIMEOUT_S = "build_timeout_s"
    BEHAVIORS_QUEUE_SLACK = "behaviors_queue_slack"


class ServerParameters(BaseModel):
    """Validated ``server`` section.

    Attributes:
        host: Bind address for the local HTTP server. Loopback keeps the keyboard
            API private to this machine; expose beyond 127.0.0.1 only knowingly.
        port: TCP port for the HTTP server (1024–65535).
        ui_disconnect_shutdown_s: Grace period in seconds with zero connected UI
            clients after which the server exits by itself (5–3600). Short values
            close the server quickly after the browser tab closes; long values
            tolerate slow tab reloads without a restart.
    """

    host: str = Field(min_length=1)
    port: int = Field(ge=1024, le=65535)
    ui_disconnect_shutdown_s: float = Field(ge=5.0, le=3600.0)


class DeviceParameters(BaseModel):
    """Validated ``device`` section.

    Attributes:
        usb_vid: USB vendor ID of the keyboard's Studio serial interface. ZMK's
            default is 0x1D50 (OpenMoko); the Imprint does not override it.
        usb_pid: USB product ID; ZMK's default is 0x615E.
        product_hint: Substring matched (case-insensitive) against the port's
            product/description string as a secondary discovery signal.
        rpc_timeout_s: Per-request timeout in seconds (0.5–60). The keyboard
            normally answers within milliseconds; raise only when debugging.
        reconnect_interval_s: Delay in seconds between automatic discovery scans
            while no keyboard is connected (0.5–60).
    """

    usb_vid: int = Field(ge=0, le=0xFFFF)
    usb_pid: int = Field(ge=0, le=0xFFFF)
    product_hint: str
    rpc_timeout_s: float = Field(ge=0.5, le=60.0)
    reconnect_interval_s: float = Field(ge=0.5, le=60.0)


class BackupParameters(BaseModel):
    """Validated ``backup`` section.

    Attributes:
        keep_last: Number of most-recent backup files retained during pruning
            (1–10000). Older files beyond this count are deleted after each new
            backup is written.
    """

    keep_last: int = Field(ge=1, le=10000)


class FirmwareParameters(BaseModel):
    """Validated ``firmware`` section.

    Attributes:
        repo_name: Name of the user's GitHub repository that hosts the generated
            firmware config (created from the template when missing).
        template_owner: GitHub owner of the upstream user-config template.
        template_repo: Name of the upstream user-config template repository.
        keyboard_tag: Git tag of the Cyboard ``zmk-keyboards`` module pinned by the
            template's ``west.yml``; recorded in generated files for traceability.
        build_timeout_s: Maximum seconds to wait for a GitHub Actions firmware
            build before giving up (60–7200; the documented build takes ~2 min).
        behaviors_queue_slack: Extra behavior-queue slots added on top of the
            computed macro requirement when sizing CONFIG_ZMK_BEHAVIORS_QUEUE_SIZE
            (0–512). Each tap step consumes 2 slots at runtime; slack absorbs
            other queued behaviors firing while a macro plays.
    """

    repo_name: str = Field(min_length=1)
    template_owner: str = Field(min_length=1)
    template_repo: str = Field(min_length=1)
    keyboard_tag: str = Field(min_length=1)
    build_timeout_s: float = Field(ge=60.0, le=7200.0)
    behaviors_queue_slack: int = Field(ge=0, le=512)


class Parameters(BaseModel):
    """Validated, typed view of the entire ``config.yaml``.

    Attributes:
        server: See :class:`ServerParameters`.
        device: See :class:`DeviceParameters`.
        backup: See :class:`BackupParameters`.
        firmware: See :class:`FirmwareParameters`.
    """

    server: ServerParameters
    device: DeviceParameters
    backup: BackupParameters
    firmware: FirmwareParameters


class BatteryAlertConfig(BaseModel):
    """Low-battery underglow blink settings, compiled into generated firmware.

    Each keyboard half monitors its own battery; the half whose charge drops to
    ``threshold_percent`` or below blinks its underglow ``blink_count`` times in
    the configured color, then restores the previous color and on/off state,
    repeating at most once per ``interval_minutes``.

    Attributes:
        enabled: Whether the feature is compiled active into the firmware.
        threshold_percent: Battery charge at or below which the alert fires
            (1–99). Battery readings arrive about once a minute.
        blink_count: Number of on/off blinks per alert (1–20).
        hue: Alert color hue in degrees (0–360; 0 red, 120 green, 240 blue).
        saturation: Alert color saturation percent (0–100).
        brightness: Alert color brightness percent (0–100; the Imprint's
            firmware caps effective brightness at 50).
        interval_minutes: Minimum minutes between alerts (1–1440).
        battcheck_ms: How long the BattCheck behavior (``&batt_chk``) shows
            each half's battery charge as a color (250–60000 ms, default
            2000). BattCheck is always compiled in; assign it to a key to
            use it. 0% = red, sweeping through orange and yellow to 100% =
            green; afterwards the previous color, effect, and on/off state
            are restored.
    """

    enabled: bool = False
    threshold_percent: int = Field(default=10, ge=1, le=99)
    blink_count: int = Field(default=3, ge=1, le=20)
    hue: int = Field(default=359, ge=0, le=360)
    saturation: int = Field(default=90, ge=0, le=100)
    brightness: int = Field(default=50, ge=0, le=100)
    interval_minutes: int = Field(default=2, ge=1, le=1440)
    battcheck_ms: int = Field(default=2000, ge=250, le=60000)


class PowerConfig(BaseModel):
    """Idle and deep-sleep power settings, compiled into generated firmware.

    These map to ZMK's built-in power management: after ``idle_seconds``
    without a key press a half goes idle (and turns its LEDs off when
    ``rgb_off_when_idle``); after ``deep_sleep_minutes`` of inactivity —
    counted from the last key press, like the idle timer — it powers down
    completely when ``deep_sleep_enabled`` (any key press wakes it, with a
    short reconnect delay). ``rgb_off_when_unplugged`` keeps the
    underglow off whenever a half runs on battery, the single biggest battery
    saver. Applied at the next firmware build + flash; both halves share the
    same settings.

    Attributes:
        idle_seconds: Seconds without activity before a half goes idle
            (5-7200; ZMK default 30).
        deep_sleep_enabled: Whether halves power off after prolonged
            inactivity.
        deep_sleep_minutes: Minutes of inactivity before deep sleep
            (1-1440; ZMK default 15). Counted from the last activity, not
            from idle onset.
        rgb_off_when_idle: Turn the underglow off while idle, back on at the
            next key press.
        rgb_off_when_unplugged: Turn the underglow off whenever the half has
            no USB power (battery operation).
    """

    idle_seconds: int = Field(default=30, ge=5, le=7200)
    deep_sleep_enabled: bool = True
    deep_sleep_minutes: int = Field(default=15, ge=1, le=1440)
    rgb_off_when_idle: bool = True
    rgb_off_when_unplugged: bool = False

    @model_validator(mode="after")
    def _sleep_after_idle(self) -> "PowerConfig":
        """Reject deep-sleep timeouts at or below the idle timeout.

        Both timers count from the last key press, so a shorter sleep timeout
        would put the half straight to sleep without ever idling — never what
        a user means.

        Returns:
            The validated instance.

        Raises:
            ValueError: When deep sleep would fire before (or with) idle.
        """
        if self.deep_sleep_enabled and self.deep_sleep_minutes * 60 <= self.idle_seconds:
            raise ValueError(
                "deep_sleep_minutes must exceed idle_seconds: deep sleep is "
                "meant to follow the idle stage, not preempt it"
            )
        return self


class TrackballMode(str, Enum):
    """What a trackball does when rolled.

    ``MOUSE`` moves the pointer; ``SCROLL_VERTICAL`` and ``SCROLL_HORIZONTAL``
    turn motion into wheel events (vertical uses the ball's Y axis, horizontal
    maps it onto the horizontal wheel); ``DISABLED`` turns the ball off
    entirely (its input listener is compiled out).
    """

    MOUSE = "mouse"
    SCROLL_VERTICAL = "scroll_vertical"
    SCROLL_HORIZONTAL = "scroll_horizontal"
    DISABLED = "disabled"


class TrackballSideConfig(BaseModel):
    """One trackball's behavior, compiled into generated firmware.

    Attributes:
        installed: Whether this half physically has a trackball. Uninstalled
            sides are compiled off and hidden in the editor (the keyboard
            cannot report trackball presence over the configuration link, so
            this is a user-maintained flag; the Imprint ships with up to one
            per half).
        mode: What rolling the ball does (pointer, scrolling, or nothing).
        speed_percent: Motion speed as a percentage of the sensor's raw rate
            (6-1600). Compiled as a ``&zip_xy_scaler`` multiplier/divisor
            fraction, so the effective value is the closest fraction with
            both terms at most 16; 100 is unscaled, the stock left-scroll
            setup uses 33 (one third).
        natural_direction: Invert the scroll axis so the view follows the top
            of the ball (the stock behavior for the left trackball). Ignored
            in MOUSE mode.
    """

    installed: bool = True
    mode: TrackballMode = TrackballMode.MOUSE
    speed_percent: int = Field(default=100, ge=6, le=1600)
    natural_direction: bool = False


class TrackballConfig(BaseModel):
    """Both trackballs plus the shared sensor responsiveness.

    Defaults reproduce the official template's behavior exactly: the left
    ball scrolls vertically at one-third speed with the natural direction,
    the right ball moves the pointer at full speed, and the sensors report
    at the shield's stock 8 ms minimum interval.

    Attributes:
        left: Left-half trackball (the ``trackball_central`` sensor).
        right: Right-half trackball (the ``trackball_peripheral`` sensor,
            relayed to the left half over the split link).
        responsiveness_ms: Minimum milliseconds between motion reports
            (0-100). One value for both sensors — it is a firmware build
            option (``CONFIG_PMW3610_REPORT_INTERVAL_MIN``), not a per-side
            devicetree property. Lower is more responsive but uses more
            battery and Bluetooth airtime; the stock value is 8.
        wake_check_ms: How often a RESTING sensor checks for motion, in
            milliseconds (10-2550, rounded to tens; both sensors). This is
            the wake-up lag: with the stock tiered rest modes a long-idle
            ball can take up to half a second to notice movement. Lower
            values wake instantly but drain the battery faster. ``None``
            keeps the stock tiers (40/100/500 ms).
        awake_after_motion_ms: How long the sensor stays at full speed after
            the last motion before starting to rest (32-8160 ms, rounded to
            the driver's 32 ms granularity). ``None`` keeps the stock 128 ms.
        force_awake: Never let the sensors rest at all — the "infinite stay
            awake": zero wake-up lag, at the highest sensor battery cost.
            The keyboard-level idle and deep-sleep timeouts still apply, so
            the half as a whole still sleeps. Compiled as the ``force-awake``
            devicetree property via per-half overlay files.
    """

    left: TrackballSideConfig = Field(
        default_factory=lambda: TrackballSideConfig(
            mode=TrackballMode.SCROLL_VERTICAL,
            speed_percent=33,
            natural_direction=True,
        )
    )
    right: TrackballSideConfig = Field(default_factory=TrackballSideConfig)
    responsiveness_ms: int = Field(default=8, ge=0, le=100)
    wake_check_ms: Optional[int] = Field(default=None, ge=10, le=2550)
    awake_after_motion_ms: Optional[int] = Field(default=None, ge=32, le=8160)
    force_awake: bool = False


class LockingConfig(BaseModel):
    """Studio-locking setting, compiled into generated firmware.

    ZMK Studio's locking makes the keyboard boot "locked": no configuration
    link (KeyMapper or Studio) can read or change the keymap until the two
    physical keys at the factory A and F positions are held for about three
    seconds — every single boot. KeyMapper builds firmware with locking
    DISABLED by default (advised: the lock only guards against configuration
    changes from the host, not against typing), but a user who wants the
    stock behavior back can re-enable it here.

    Attributes:
        studio_locking_enabled: Compile the firmware with ZMK Studio locking
            active. When true, KeyMapper needs the physical A+F unlock after
            every keyboard restart before it can edit the keymap.
    """

    studio_locking_enabled: bool = False


# --------------------------------------------------------------------------- #
# Protocol dataclasses                                                        #
# --------------------------------------------------------------------------- #
class LockState(IntEnum):
    """Studio access lock state as reported by the keyboard."""

    LOCKED = 0
    UNLOCKED = 1


class NotificationKind(str, Enum):
    """Kinds of unsolicited events pushed by the keyboard or the connection."""

    LOCK_STATE_CHANGED = "lock_state_changed"
    UNSAVED_CHANGES_CHANGED = "unsaved_changes_changed"
    CONNECTION_LOST = "connection_lost"
    CAPTURE_PRESS = "capture_press"


@dataclass
class ConnectionEvent:
    """One unsolicited event delivered to notification subscribers.

    Attributes:
        kind: Which event occurred.
        value: Event payload — ``LockState`` for lock changes, ``bool`` for
            unsaved-changes changes, ``str`` (reason) for connection loss.
    """

    kind: NotificationKind
    value: Any


@dataclass
class DeviceInfo:
    """Identity of the connected keyboard.

    Attributes:
        name: Product name reported by the firmware (e.g. ``Imprint``).
        serial_number: Firmware-reported serial number, hex-encoded.
    """

    name: str
    serial_number: str

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this device identity."""
        return {"name": self.name, "serial_number": self.serial_number}


@dataclass
class ParamDomain:
    """Domain of one behavior parameter as advertised by the keyboard.

    Attributes:
        name: Human-readable label for the parameter value set.
        kind: One of ``nil``, ``constant``, ``range``, ``hid_usage``, ``layer_id``.
        constant: Fixed value when ``kind == "constant"``, else ``None``.
        range_min: Lower bound when ``kind == "range"``, else ``None``.
        range_max: Upper bound when ``kind == "range"``, else ``None``.
        keyboard_max: Highest keyboard HID usage when ``kind == "hid_usage"``.
        consumer_max: Highest consumer HID usage when ``kind == "hid_usage"``.
    """

    name: str
    kind: str
    constant: Optional[int] = None
    range_min: Optional[int] = None
    range_max: Optional[int] = None
    keyboard_max: Optional[int] = None
    consumer_max: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this parameter domain."""
        return {
            "name": self.name,
            "kind": self.kind,
            "constant": self.constant,
            "range_min": self.range_min,
            "range_max": self.range_max,
            "keyboard_max": self.keyboard_max,
            "consumer_max": self.consumer_max,
        }


@dataclass
class BehaviorDetails:
    """One behavior compiled into the firmware, with its parameter metadata.

    Attributes:
        behavior_id: Stable wire identifier (crc16 of the firmware device name).
        display_name: Human-readable behavior name reported by the firmware.
        param_sets: Alternative (param1-domain, param2-domain) combinations; a
            binding is valid when its params fit one combination. Each element is
            a tuple ``(param1_domains, param2_domains)``.
    """

    behavior_id: int
    display_name: str
    param_sets: List[Tuple[List[ParamDomain], List[ParamDomain]]]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this behavior."""
        return {
            "behavior_id": self.behavior_id,
            "display_name": self.display_name,
            "param_sets": [
                {
                    "param1": [d.to_dict() for d in p1],
                    "param2": [d.to_dict() for d in p2],
                }
                for (p1, p2) in self.param_sets
            ],
        }


@dataclass
class Binding:
    """One key binding: a behavior reference plus its two parameters.

    Attributes:
        behavior_id: Wire identifier of the bound behavior.
        param1: First behavior parameter (meaning depends on the behavior).
        param2: Second behavior parameter (meaning depends on the behavior).
    """

    behavior_id: int
    param1: int = 0
    param2: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this binding."""
        return {"behavior_id": self.behavior_id, "param1": self.param1, "param2": self.param2}


@dataclass
class Layer:
    """One keymap layer.

    Attributes:
        layer_id: Stable layer identifier (survives reordering).
        name: Display name of the layer.
        bindings: One :class:`Binding` per key position of the active layout.
    """

    layer_id: int
    name: str
    bindings: List[Binding]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this layer."""
        return {
            "layer_id": self.layer_id,
            "name": self.name,
            "bindings": [b.to_dict() for b in self.bindings],
        }


@dataclass
class Keymap:
    """The full keymap as reported by the keyboard.

    Attributes:
        layers: Enabled layers in top-to-bottom precedence order.
        available_layers: How many additional layers can still be enabled.
        max_layer_name_length: Firmware limit for layer display names.
    """

    layers: List[Layer]
    available_layers: int
    max_layer_name_length: int

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this keymap."""
        return {
            "layers": [l.to_dict() for l in self.layers],
            "available_layers": self.available_layers,
            "max_layer_name_length": self.max_layer_name_length,
        }


@dataclass
class PhysicalKey:
    """Geometry of one physical key in centi-keyunits (100 = one key width).

    Attributes:
        width: Key width.
        height: Key height.
        x: Left edge of the key.
        y: Top edge of the key.
        r: Rotation in centi-degrees.
        rx: Rotation origin X.
        ry: Rotation origin Y.
    """

    width: int
    height: int
    x: int
    y: int
    r: int = 0
    rx: int = 0
    ry: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this key's geometry."""
        return {
            "width": self.width,
            "height": self.height,
            "x": self.x,
            "y": self.y,
            "r": self.r,
            "rx": self.rx,
            "ry": self.ry,
        }


@dataclass
class PhysicalLayout:
    """One selectable physical layout variant.

    Attributes:
        name: Layout display name (e.g. ``Function Row, Full Bottom Row``).
        keys: Geometry of every key; list index == key position in bindings.
    """

    name: str
    keys: List[PhysicalKey]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this layout."""
        return {"name": self.name, "keys": [k.to_dict() for k in self.keys]}


@dataclass
class PhysicalLayouts:
    """All physical layouts plus which one is active.

    Attributes:
        active_index: Index into ``layouts`` of the active layout.
        layouts: Every layout variant compiled into the firmware.
    """

    active_index: int
    layouts: List[PhysicalLayout]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of all layouts."""
        return {
            "active_index": self.active_index,
            "layouts": [l.to_dict() for l in self.layouts],
        }


# --------------------------------------------------------------------------- #
# Macro model (compiled into firmware by the generator)                       #
# --------------------------------------------------------------------------- #
class MacroStepKind(str, Enum):
    """Kinds of steps composable in a macro sequence."""

    TAP = "tap"
    PRESS = "press"
    RELEASE = "release"
    WAIT_MS = "wait_ms"
    TAP_MS = "tap_ms"
    PAUSE_FOR_RELEASE = "pause_for_release"


@dataclass
class MacroStep:
    """One step of a macro sequence.

    Attributes:
        kind: What the step does — tap/press/release execute ``binding``;
            wait_ms/tap_ms change subsequent timing using ``value`` milliseconds;
            pause_for_release splits the macro across key press and key release.
        binding: Devicetree binding text for tap/press/release steps, e.g.
            ``&kp KP_N0`` or ``&rgb_ug RGB_COLOR_HSB(60,100,100)``; empty for
            timing/pause steps.
        value: Milliseconds for wait_ms/tap_ms steps; 0 otherwise.
    """

    kind: MacroStepKind
    binding: str = ""
    value: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this step."""
        return {"kind": self.kind.value, "binding": self.binding, "value": self.value}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MacroStep":
        """Rebuild a :class:`MacroStep` from :meth:`to_dict` output.

        Args:
            data: Mapping with ``kind`` and optional ``binding``/``value``.

        Returns:
            The reconstructed step.
        """
        return MacroStep(
            kind=MacroStepKind(str(data["kind"])),
            binding=str(data.get("binding", "")),
            value=int(data.get("value", 0)),
        )


@dataclass
class MacroDefinition:
    """One user-defined macro to be compiled into the firmware.

    Attributes:
        node_name: Devicetree node label (lowercase, ``[a-z0-9_]``, unique).
        display_name: Human-readable name shown by Studio-compatible clients.
        steps: Ordered steps executed when the macro fires.
        wait_ms: Delay between queued behaviors in milliseconds (0–5000).
            Alt-code entry needs ~30 ms for the host to keep up; pure
            layer/underglow macros run best at 0.
        tap_ms: Press-to-release time of tapped behaviors in milliseconds
            (0–5000). 30 ms is safe for host-visible key taps; 0 for
            instantaneous internal behaviors.
        shifted_steps: When non-empty, the behavior compiles as a Shift pair
            (mod-morph): ``steps`` run on a plain press, ``shifted_steps`` run
            when Shift is held — with the physically-held Shift masked so it
            cannot corrupt the typed output (essential for Alt-codes, where a
            real Shift would turn keypad digits into navigation keys).
    """

    node_name: str
    display_name: str
    steps: List[MacroStep]
    wait_ms: int = 30
    tap_ms: int = 30
    shifted_steps: List[MacroStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of this macro."""
        return {
            "node_name": self.node_name,
            "display_name": self.display_name,
            "steps": [s.to_dict() for s in self.steps],
            "wait_ms": self.wait_ms,
            "tap_ms": self.tap_ms,
            "shifted_steps": [s.to_dict() for s in self.shifted_steps],
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MacroDefinition":
        """Rebuild a :class:`MacroDefinition` from :meth:`to_dict` output.

        Args:
            data: Mapping with ``node_name``, ``display_name``, ``steps`` list and
                optional ``wait_ms``/``tap_ms`` integers and ``shifted_steps``.

        Returns:
            The reconstructed macro definition.
        """
        return MacroDefinition(
            node_name=str(data["node_name"]),
            display_name=str(data["display_name"]),
            steps=[MacroStep.from_dict(s) for s in data.get("steps", [])],
            wait_ms=int(data.get("wait_ms", 30)),
            tap_ms=int(data.get("tap_ms", 30)),
            shifted_steps=[MacroStep.from_dict(s) for s in data.get("shifted_steps", [])],
        )

    def queue_slots(self) -> int:
        """Return how many behavior-queue slots this macro consumes when it fires.

        Tap steps cost 2 slots (press event + release event); press, release and
        timing/pause controls cost 1 slot each, matching the firmware's queue
        accounting. For a Shift pair only one variant fires at a time, so the
        larger of the two counts.

        Returns:
            Total queue slots required to enqueue the whole macro at once.
        """

        def _slots(step_list: List[MacroStep]) -> int:
            return sum(2 if s.kind == MacroStepKind.TAP else 1 for s in step_list)

        return max(_slots(self.steps), _slots(self.shifted_steps))


# --------------------------------------------------------------------------- #
# Devicetree mapping tables (firmware generator)                              #
# --------------------------------------------------------------------------- #
class DtsMaps:
    """Constant lookup tables mapping wire-level data to devicetree source.

    ``PHYSICAL_LAYOUT_LABELS`` keys are lower-cased Studio display names of the
    Imprint's physical layouts (from ``imprint-layouts.dtsi``); values are the
    devicetree node labels referenced by ``chosen { zmk,physical-layout … }``.

    ``BEHAVIOR_TOKENS`` keys are lower-cased behavior display names as reported
    over the Studio protocol; values are ``(dts_reference, param_kinds)`` where
    ``param_kinds`` is a tuple with one entry per keymap cell the reference
    takes: ``"keycode"`` (HID-usage decoding), ``"layer"`` (wire value is a
    stable layer ID that must be translated to the baked layer position), or
    ``"raw"`` (emit the number unchanged).

    ``KEYBOARD_USAGE_NAMES`` / ``CONSUMER_USAGE_NAMES`` map HID usage IDs to the
    canonical ZMK keycode names from ``dt-bindings/zmk/keys.h``; unmapped usages
    render as hex literals, which compile identically.

    ``MOD_BIT_WRAPPERS`` maps ZMK implicit-modifier bits (bits 24–31 of a &kp
    parameter) to the keycode wrapper macros that re-encode them in source form.
    """

    PHYSICAL_LAYOUT_LABELS: Dict[str, str] = {
        "imprint function row (5-key bottom row)": "physical_layout_imprint_function_row_full_bottom_row",
        "imprint function row (2-key bottom row)": "physical_layout_imprint_function_row",
        "imprint function row (no bottom row)": "physical_layout_imprint_function_row_no_bottom_row",
        "imprint number row (5-key bottom row)": "physical_layout_imprint_number_row_full_bottom_row",
        "imprint number row (2-key bottom row)": "physical_layout_imprint_number_row",
        "imprint number row (no bottom row)": "physical_layout_imprint_number_row_no_bottom_row",
        "imprint letters only (5-key bottom row)": "physical_layout_imprint_letters_only_full_bottom_row",
        "imprint letters only (2-key bottom row)": "physical_layout_imprint_letters_only",
        "imprint letters only (no bottom row)": "physical_layout_imprint_letters_only_no_bottom_row",
    }

    BEHAVIOR_TOKENS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
        "key press": ("&kp", ("keycode",)),
        "key toggle": ("&kt", ("keycode",)),
        "mod-tap": ("&mt", ("keycode", "keycode")),
        "layer-tap": ("&lt", ("layer", "keycode")),
        "momentary layer": ("&mo", ("layer",)),
        "to layer": ("&to", ("layer",)),
        "toggle layer": ("&tog", ("layer",)),
        "sticky key": ("&sk", ("keycode",)),
        "sticky layer": ("&sl", ("layer",)),
        "transparent": ("&trans", ()),
        "none": ("&none", ()),
        "caps word": ("&caps_word", ()),
        "key repeat": ("&key_repeat", ()),
        "underglow": ("&rgb_ug", ("raw", "raw")),
        "rgb underglow": ("&rgb_ug", ("raw", "raw")),
        "backlight": ("&bl", ("raw", "raw")),
        "bluetooth": ("&bt", ("raw", "raw")),
        "output selection": ("&out", ("raw",)),
        "outputs": ("&out", ("raw",)),
        "external power": ("&ext_power", ("raw",)),
        "reset": ("&sys_reset", ()),
        "bootloader": ("&bootloader", ()),
        "studio unlock": ("&studio_unlock", ()),
        "soft off": ("&soft_off", ()),
        "grave escape": ("&gresc", ()),
        "mouse button press": ("&mkp", ("raw",)),
        "mouse key press": ("&mkp", ("raw",)),
        "mouse move": ("&mmv", ("raw",)),
        "mouse scroll": ("&msc", ("raw",)),
        "rgb save/restore": ("&rgb_mem", ()),
        "numlock guard": ("&nl_guard", ()),
        # The node name must stay within 8 characters: ZMK's BLE split relay
        # truncates longer behavior names and the peripheral half then never
        # finds the behavior (exact-name lookup).
        "battcheck": ("&batt_chk", ()),
        "battery check": ("&batt_chk", ()),
    }

    KEYBOARD_USAGE_NAMES: Dict[int, str] = {
        **{0x04 + i: chr(ord("A") + i) for i in range(26)},
        **{0x1E + i: f"N{i + 1}" for i in range(9)},
        0x27: "N0",
        0x28: "RET",
        0x29: "ESC",
        0x2A: "BSPC",
        0x2B: "TAB",
        0x2C: "SPACE",
        0x2D: "MINUS",
        0x2E: "EQUAL",
        0x2F: "LBKT",
        0x30: "RBKT",
        0x31: "BSLH",
        0x32: "NON_US_HASH",
        0x33: "SEMI",
        0x34: "SQT",
        0x35: "GRAVE",
        0x36: "COMMA",
        0x37: "DOT",
        0x38: "FSLH",
        0x39: "CAPS",
        **{0x3A + i: f"F{i + 1}" for i in range(12)},
        0x46: "PSCRN",
        0x47: "SLCK",
        0x48: "PAUSE_BREAK",
        0x49: "INS",
        0x4A: "HOME",
        0x4B: "PG_UP",
        0x4C: "DEL",
        0x4D: "END",
        0x4E: "PG_DN",
        0x4F: "RIGHT",
        0x50: "LEFT",
        0x51: "DOWN",
        0x52: "UP",
        0x53: "KP_NUM",
        0x54: "KP_SLASH",
        0x55: "KP_MULTIPLY",
        0x56: "KP_MINUS",
        0x57: "KP_PLUS",
        0x58: "KP_ENTER",
        **{0x59 + i: f"KP_N{i + 1}" for i in range(9)},
        0x62: "KP_N0",
        0x63: "KP_DOT",
        0x64: "NON_US_BSLH",
        0x65: "K_APP",
        0x67: "KP_EQUAL",
        **{0x68 + i: f"F{i + 13}" for i in range(12)},
        0xE0: "LCTRL",
        0xE1: "LSHFT",
        0xE2: "LALT",
        0xE3: "LGUI",
        0xE4: "RCTRL",
        0xE5: "RSHFT",
        0xE6: "RALT",
        0xE7: "RGUI",
    }

    CONSUMER_USAGE_NAMES: Dict[int, str] = {
        0x30: "C_PWR",
        0x32: "C_SLEEP",
        0x40: "C_MENU",
        0x61: "C_CAPTIONS",
        0x6F: "C_BRI_INC",
        0x70: "C_BRI_DEC",
        0x73: "C_BRI_MIN",
        0x74: "C_BRI_MAX",
        0x75: "C_BRI_AUTO",
        0xB0: "C_PLAY",
        0xB1: "C_PAUSE",
        0xB2: "C_REC",
        0xB3: "C_FF",
        0xB4: "C_RW",
        0xB5: "C_NEXT",
        0xB6: "C_PREV",
        0xB7: "C_STOP",
        0xB8: "C_EJECT",
        0xCD: "C_PP",
        0xE2: "C_MUTE",
        0xE9: "C_VOL_UP",
        0xEA: "C_VOL_DN",
        0x184: "C_AL_WORD",
        0x185: "C_AL_TEXT_EDITOR",
        0x186: "C_AL_SHEET",
        0x187: "C_AL_GRAPHICS_EDITOR",
        0x188: "C_AL_PRESENTATION",
        0x189: "C_AL_DB",
        0x18A: "C_AL_MAIL",
        0x18B: "C_AL_NEWS",
        0x18C: "C_AL_VOICEMAIL",
        0x18D: "C_AL_CONTACTS",
        0x18E: "C_AL_CAL",
        0x18F: "C_AL_TASK_MANAGER",
        0x190: "C_AL_JOURNAL",
        0x191: "C_AL_FINANCE",
        0x192: "C_AL_CALC",
        0x193: "C_AL_AV_CAPTURE_PLAYBACK",
        0x194: "C_AL_MY_COMPUTER",
        0x196: "C_AL_WWW",
        0x199: "C_AL_CHAT",
        0x19C: "C_AL_LOGOFF",
        0x19E: "C_AL_LOCK",
        0x19F: "C_AL_CONTROL_PANEL",
        0x1A2: "C_AL_SELECT_TASK",
        0x1A3: "C_AL_NEXT_TASK",
        0x1A4: "C_AL_PREV_TASK",
        0x1A6: "C_AL_HELP",
        0x1A7: "C_AL_DOCS",
        0x1AB: "C_AL_SPELL",
        0x1AE: "C_AL_KEYBOARD_LAYOUT",
        0x201: "C_AC_NEW",
        0x202: "C_AC_OPEN",
        0x203: "C_AC_CLOSE",
        0x204: "C_AC_EXIT",
        0x207: "C_AC_SAVE",
        0x208: "C_AC_PRINT",
        0x209: "C_AC_PROPS",
        0x21A: "C_AC_UNDO",
        0x21B: "C_AC_COPY",
        0x21C: "C_AC_CUT",
        0x21D: "C_AC_PASTE",
        0x21F: "C_AC_FIND",
        0x221: "C_AC_SEARCH",
        0x222: "C_AC_GOTO",
        0x223: "C_AC_HOME",
        0x224: "C_AC_BACK",
        0x225: "C_AC_FORWARD",
        0x226: "C_AC_STOP",
        0x227: "C_AC_REFRESH",
        0x22A: "C_AC_BOOKMARKS",
        0x22D: "C_AC_ZOOM_IN",
        0x22E: "C_AC_ZOOM_OUT",
        0x233: "C_AC_SCROLL_UP",
        0x234: "C_AC_SCROLL_DOWN",
        0x25F: "C_AC_CANCEL",
    }

    MOD_BIT_WRAPPERS: Dict[int, str] = {
        0x01: "LC",
        0x02: "LS",
        0x04: "LA",
        0x08: "LG",
        0x10: "RC",
        0x20: "RS",
        0x40: "RA",
        0x80: "RG",
    }

    HID_PAGE_KEYBOARD: int = 0x07
    HID_PAGE_CONSUMER: int = 0x0C


# --------------------------------------------------------------------------- #
# Firmware source templates                                                   #
# --------------------------------------------------------------------------- #
class FirmwareTemplates:
    """Constant source fragments used by the firmware generator.

    ``KEYMAP_INCLUDES`` mirrors the include set of the official user-config
    template so generated keymaps compile against the same headers.

    ``CONF_HEADER`` documents the generated ``imprint.conf``.
    """

    KEYMAP_INCLUDES: str = (
        "#include <input/processors.dtsi>\n"
        "#include <dt-bindings/zmk/input_transform.h>\n"
        "#include <behaviors.dtsi>\n"
        "#include <dt-bindings/zmk/bt.h>\n"
        "#include <dt-bindings/zmk/keys.h>\n"
        "#include <dt-bindings/zmk/modifiers.h>\n"
        "#include <dt-bindings/zmk/pointing.h>\n"
        "#include <dt-bindings/zmk/rgb.h>\n"
        "#include <dt-bindings/zmk/outputs.h>\n"
    )

    CONF_HEADER: str = (
        "# Generated by KeyMapper. Studio locking follows the Advanced-tab setting\n"
        "# (disabled by default, so the keyboard never asks for an unlock combo);\n"
        "# the behavior queue is sized for the combined need of all generated macros.\n"
    )

    MODULE_YML: str = (
        "name: keymap-extras\n"
        "build:\n"
        "  cmake: .\n"
        "  kconfig: Kconfig\n"
        "  settings:\n"
        "    dts_root: .\n"
    )

    MODULE_CMAKE: str = (
        "target_sources_ifdef(CONFIG_KEYMAP_BATTERY_ALERT app PRIVATE "
        "src/battery_alert_blink.c)\n"
        "target_sources_ifdef(CONFIG_KEYMAP_RGB_REMEMBER app PRIVATE "
        "src/rgb_remember.c)\n"
        "target_sources_ifdef(CONFIG_KEYMAP_CAPTURE app PRIVATE "
        "src/capture_gatt.c)\n"
        "target_sources_ifdef(CONFIG_KEYMAP_TRACKBALL_RUNTIME app PRIVATE "
        "src/trackball_runtime.c)\n"
        "target_sources_ifdef(CONFIG_KEYMAP_UNDERGLOW_WAKE_SYNC app PRIVATE "
        "src/underglow_wake_sync.c)\n"
        "target_sources_ifdef(CONFIG_KEYMAP_NUMLOCK_GUARD app PRIVATE "
        "src/numlock_guard.c)\n"
        "target_sources_ifdef(CONFIG_KEYMAP_BATTCHECK app PRIVATE "
        "src/battcheck.c)\n"
    )

    MODULE_KCONFIG: str = (
        'config KEYMAP_BATTERY_ALERT\n'
        '    bool "Blink the underglow when this half\'s battery is low"\n'
        "    depends on ZMK_RGB_UNDERGLOW\n"
        "    default n\n"
        "\n"
        "if KEYMAP_BATTERY_ALERT\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_THRESHOLD\n"
        '    int "Battery percentage at or below which the alert fires"\n'
        "    range 1 99\n"
        "    default 10\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_BLINK_COUNT\n"
        '    int "Number of blinks per alert"\n'
        "    range 1 20\n"
        "    default 3\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_HUE\n"
        '    int "Alert color hue in degrees"\n'
        "    range 0 360\n"
        "    default 359\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_SAT\n"
        '    int "Alert color saturation percent"\n'
        "    range 0 100\n"
        "    default 90\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_BRT\n"
        '    int "Alert color brightness percent"\n'
        "    range 0 100\n"
        "    default 50\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_INTERVAL_S\n"
        '    int "Minimum seconds between alerts"\n'
        "    range 60 86400\n"
        "    default 120\n"
        "\n"
        "config KEYMAP_BATTERY_ALERT_BLINK_MS\n"
        '    int "Half-period of one blink in milliseconds"\n'
        "    range 50 2000\n"
        "    default 300\n"
        "\n"
        "endif\n"
        "\n"
        "config KEYMAP_RGB_REMEMBER\n"
        '    bool "RGB Save/Restore behavior (&rgb_mem)"\n'
        "    depends on ZMK_RGB_UNDERGLOW\n"
        "    default y\n"
        "\n"
        "config KEYMAP_BATTCHECK\n"
        '    bool "BattCheck behavior (&batt_check): show battery as a color"\n'
        "    depends on ZMK_RGB_UNDERGLOW\n"
        "    default y\n"
        "\n"
        "if KEYMAP_BATTCHECK\n"
        "\n"
        "config KEYMAP_BATTCHECK_MS\n"
        '    int "BattCheck glow duration in milliseconds"\n'
        "    range 250 60000\n"
        "    default 2000\n"
        "\n"
        "endif\n"
        "\n"
        "config KEYMAP_CAPTURE\n"
        '    bool "Expose the last pressed key position over BLE for KeyMapper capture"\n'
        "    depends on ZMK_BLE\n"
        "    default y\n"
        "\n"
        "config KEYMAP_NUMLOCK_GUARD\n"
        '    bool "Num Lock guard behavior for Alt-code sequences (&nl_guard)"\n'
        "    select ZMK_HID_INDICATORS\n"
        "    default y\n"
        "\n"
        "config KEYMAP_UNDERGLOW_WAKE_SYNC\n"
        '    bool "Relight both halves with the last color when waking from idle"\n'
        "    depends on ZMK_RGB_UNDERGLOW\n"
        "    default y\n"
        "\n"
        "config KEYMAP_TRACKBALL_RUNTIME\n"
        '    bool "Runtime-configurable trackball behavior with a BLE config channel"\n'
        "    depends on ZMK_POINTING && ZMK_BLE\n"
        "    select SETTINGS\n"
        "    default y\n"
        "\n"
        "if KEYMAP_TRACKBALL_RUNTIME\n"
        "\n"
        "# Initial values compiled in from the companion app's staged settings;\n"
        "# overridden at runtime by BLE writes (persisted via Zephyr settings).\n"
        "# Modes: 0 cursor, 1 vertical scroll, 2 horizontal scroll, 3 disabled.\n"
        "# Flags: bit 0 = natural scroll direction.\n"
        "config KEYMAP_TB_LEFT_MODE\n"
        '    int "Left trackball initial mode"\n'
        "    default 1\n"
        "\n"
        "config KEYMAP_TB_LEFT_MUL\n"
        '    int "Left trackball speed multiplier (1-16)"\n'
        "    default 1\n"
        "\n"
        "config KEYMAP_TB_LEFT_DIV\n"
        '    int "Left trackball speed divisor (1-16)"\n'
        "    default 3\n"
        "\n"
        "config KEYMAP_TB_LEFT_FLAGS\n"
        '    int "Left trackball flags"\n'
        "    default 1\n"
        "\n"
        "config KEYMAP_TB_RIGHT_MODE\n"
        '    int "Right trackball initial mode"\n'
        "    default 0\n"
        "\n"
        "config KEYMAP_TB_RIGHT_MUL\n"
        '    int "Right trackball speed multiplier (1-16)"\n'
        "    default 1\n"
        "\n"
        "config KEYMAP_TB_RIGHT_DIV\n"
        '    int "Right trackball speed divisor (1-16)"\n'
        "    default 1\n"
        "\n"
        "config KEYMAP_TB_RIGHT_FLAGS\n"
        '    int "Right trackball flags"\n'
        "    default 0\n"
        "\n"
        "endif\n"
    )

    CAPTURE_SOURCE: str = (
        "/*\n"
        " * KeyMapper capture channel.\n"
        " *\n"
        " * Publishes the last pressed key position and a monotonically\n"
        " * increasing press counter as a readable BLE GATT characteristic on\n"
        " * the central half. The companion app polls it while a capture mode\n"
        " * is active, so pressing any key on this keyboard can assign that\n"
        " * key's current binding (sequence, layer switch, anything) to\n"
        " * another key. Read access requires the encrypted host bond, like\n"
        " * the battery service.\n"
        " */\n"
        "\n"
        "#include <zephyr/kernel.h>\n"
        "#include <zephyr/bluetooth/bluetooth.h>\n"
        "#include <zephyr/bluetooth/gatt.h>\n"
        "#include <zephyr/bluetooth/uuid.h>\n"
        "#include <zephyr/sys/byteorder.h>\n"
        "#include <zmk/event_manager.h>\n"
        "#include <zmk/events/position_state_changed.h>\n"
        "#include <zmk/keymap.h>\n"
        "\n"
        "#if !IS_ENABLED(CONFIG_ZMK_SPLIT) || IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)\n"
        "\n"
        "/* The 48-bit block needs ULL: the encode macro shifts it by up to 40. */\n"
        "#define KEYMAP_CAPTURE_SVC_UUID \\\n"
        "    BT_UUID_128_ENCODE(0x6b65796d, 0x0001, 0x4b4d, 0x8000, 0x000000000001ULL)\n"
        "#define KEYMAP_CAPTURE_CHR_UUID \\\n"
        "    BT_UUID_128_ENCODE(0x6b65796d, 0x0002, 0x4b4d, 0x8000, 0x000000000001ULL)\n"
        "\n"
        "#define KEYMAP_CAPTURE_VALUE_LEN 10\n"
        "\n"
        "static uint32_t press_counter;\n"
        "static uint16_t last_position;\n"
        "static uint32_t last_layer_state;\n"
        "\n"
        "static void fill_capture_value(uint8_t value[KEYMAP_CAPTURE_VALUE_LEN]) {\n"
        "    sys_put_le32(press_counter, &value[0]);\n"
        "    sys_put_le16(last_position, &value[4]);\n"
        "    /* Active-layer bitmask (by layer id) at press time, so the host\n"
        "     * can resolve the binding the press actually triggers - the\n"
        "     * keyboard's live layer, not whatever layer the app displays. */\n"
        "    sys_put_le32(last_layer_state, &value[6]);\n"
        "}\n"
        "\n"
        "static ssize_t read_capture(struct bt_conn *conn,\n"
        "                            const struct bt_gatt_attr *attr, void *buf,\n"
        "                            uint16_t len, uint16_t offset) {\n"
        "    uint8_t value[KEYMAP_CAPTURE_VALUE_LEN];\n"
        "    fill_capture_value(value);\n"
        "    return bt_gatt_attr_read(conn, attr, buf, len, offset, value,\n"
        "                             sizeof(value));\n"
        "}\n"
        "\n"
        "static void capture_ccc_changed(const struct bt_gatt_attr *attr,\n"
        "                                uint16_t value) {}\n"
        "\n"
        "BT_GATT_SERVICE_DEFINE(\n"
        "    keymapper_capture_svc,\n"
        "    BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(KEYMAP_CAPTURE_SVC_UUID)),\n"
        "    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(KEYMAP_CAPTURE_CHR_UUID),\n"
        "                           BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,\n"
        "                           BT_GATT_PERM_READ_ENCRYPT,\n"
        "                           read_capture, NULL, NULL),\n"
        "    BT_GATT_CCC(capture_ccc_changed,\n"
        "                BT_GATT_PERM_READ_ENCRYPT | BT_GATT_PERM_WRITE_ENCRYPT));\n"
        "\n"
        "static int capture_listener(const zmk_event_t *eh) {\n"
        "    const struct zmk_position_state_changed *ev =\n"
        "        as_zmk_position_state_changed(eh);\n"
        "    if (ev == NULL || !ev->state) {\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "    last_position = (uint16_t)ev->position;\n"
        "    last_layer_state = zmk_keymap_layer_state();\n"
        "    press_counter++;\n"
        "    /* Push every press to a subscribed host: polling cannot tell\n"
        "     * WHEN a key was pressed, and the companion app needs press\n"
        "     * timing to pair a press with the right on-screen target. */\n"
        "    uint8_t value[KEYMAP_CAPTURE_VALUE_LEN];\n"
        "    fill_capture_value(value);\n"
        "    bt_gatt_notify(NULL, &keymapper_capture_svc.attrs[1], value,\n"
        "                   sizeof(value));\n"
        "    return ZMK_EV_EVENT_BUBBLE;\n"
        "}\n"
        "\n"
        "ZMK_LISTENER(keymapper_capture, capture_listener);\n"
        "ZMK_SUBSCRIPTION(keymapper_capture, zmk_position_state_changed);\n"
        "\n"
        "#endif /* central-only */\n"
    )

    NUMLOCK_GUARD_SOURCE: str = (
        "/*\n"
        " * Num Lock guard behavior (&nl_guard).\n"
        " *\n"
        " * Windows Alt-codes need Num Lock ON: with it off the keypad digits\n"
        " * are navigation keys, so accent sequences typed nothing (or moved\n"
        " * lines around - Alt+Up is a shortcut in many editors). The host\n"
        " * reports its lock-key LEDs to the keyboard, so this behavior can\n"
        " * check the real Num Lock state: press = switch Num Lock on when it\n"
        " * is off (with a short settle so the host applies it before the\n"
        " * digits arrive); release = restore the original state. Wrapped\n"
        " * around every Alt-code sequence, they work regardless of the\n"
        " * host's Num Lock.\n"
        " */\n"
        "\n"
        "#define DT_DRV_COMPAT zmk_behavior_numlock_guard\n"
        "\n"
        "#include <zephyr/device.h>\n"
        "#include <zephyr/kernel.h>\n"
        "#include <drivers/behavior.h>\n"
        "#include <zmk/behavior.h>\n"
        "#include <zmk/hid_indicators.h>\n"
        "#include <zmk/events/keycode_state_changed.h>\n"
        "#include <dt-bindings/zmk/keys.h>\n"
        "\n"
        "/* Keycode events and HID indicators exist on the central half only\n"
        " * (behaviors run there; the peripheral just streams key positions),\n"
        " * so the peripheral build must not reference their symbols. */\n"
        "#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT) &&                        \\\n"
        "    (!IS_ENABLED(CONFIG_ZMK_SPLIT) ||                                  \\\n"
        "     IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL))\n"
        "\n"
        "/* HID LED report bit 0 is Num Lock. */\n"
        "#define NUMLOCK_LED_BIT BIT(0)\n"
        "\n"
        "static bool guard_toggled;\n"
        "\n"
        "static void tap_numlock(int64_t timestamp) {\n"
        "    raise_zmk_keycode_state_changed_from_encoded(KP_NUMLOCK, true,\n"
        "                                                 timestamp);\n"
        "    raise_zmk_keycode_state_changed_from_encoded(KP_NUMLOCK, false,\n"
        "                                                 timestamp);\n"
        "}\n"
        "\n"
        "static int on_pressed(struct zmk_behavior_binding *binding,\n"
        "                      struct zmk_behavior_binding_event event) {\n"
        "    guard_toggled = false;\n"
        "    if (!(zmk_hid_indicators_get_current_profile() & NUMLOCK_LED_BIT)) {\n"
        "        tap_numlock(event.timestamp);\n"
        "        guard_toggled = true;\n"
        "        /* Give the host time to process the toggle before the\n"
        "         * digits arrive (blocks the behavior queue, like a macro\n"
        "         * wait would). */\n"
        "        k_msleep(50);\n"
        "    }\n"
        "    return ZMK_BEHAVIOR_OPAQUE;\n"
        "}\n"
        "\n"
        "static int on_released(struct zmk_behavior_binding *binding,\n"
        "                       struct zmk_behavior_binding_event event) {\n"
        "    if (guard_toggled) {\n"
        "        guard_toggled = false;\n"
        "        k_msleep(20);\n"
        "        tap_numlock(event.timestamp);\n"
        "    }\n"
        "    return ZMK_BEHAVIOR_OPAQUE;\n"
        "}\n"
        "\n"
        "static const struct behavior_driver_api numlock_guard_driver_api = {\n"
        "    .binding_pressed = on_pressed,\n"
        "    .binding_released = on_released,\n"
        "/* Required or Studio's set-binding RPC rejects direct assignment\n"
        " * with INVALID_PARAMETERS (metadata lookup errors out). */\n"
        "#if IS_ENABLED(CONFIG_ZMK_BEHAVIOR_METADATA)\n"
        "    .get_parameter_metadata = zmk_behavior_get_empty_param_metadata,\n"
        "#endif\n"
        "};\n"
        "\n"
        "BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,\n"
        "                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,\n"
        "                        &numlock_guard_driver_api);\n"
        "\n"
        "#endif /* compat okay, central only */\n"
    )

    NUMLOCK_GUARD_BINDING: str = (
        "description: |\n"
        "  Ensures Num Lock is ON while held (press switches it on when the\n"
        "  host reports it off; release restores the original state). Wrap\n"
        "  Alt-code sequences in it so keypad digits are digits, not\n"
        "  navigation keys.\n"
        "\n"
        'compatible: "zmk,behavior-numlock-guard"\n'
        "\n"
        "properties:\n"
        '  "#binding-cells":\n'
        "    type: int\n"
        "    required: true\n"
        "    const: 0\n"
        "  display-name:\n"
        "    type: string\n"
    )

    NUMLOCK_GUARD_NODE: str = (
        "        nl_guard: nl_guard {\n"
        '            compatible = "zmk,behavior-numlock-guard";\n'
        "            #binding-cells = <0>;\n"
        '            display-name = "NumLock Guard";\n'
        "        };\n"
    )

    RGB_REMEMBER_BINDING: str = (
        "description: |\n"
        "  Saves the current underglow color and on/off state when pressed and\n"
        "  restores both when released. Wrap a momentary-layer color sequence in\n"
        "  it to return to whatever color was active before the hold.\n"
        "\n"
        'compatible: "zmk,behavior-rgb-remember"\n'
        "\n"
        "properties:\n"
        '  "#binding-cells":\n'
        "    type: int\n"
        "    required: true\n"
        "    const: 0\n"
        "  display-name:\n"
        "    type: string\n"
    )

    RGB_REMEMBER_SOURCE: str = (
        "/*\n"
        " * RGB Save/Restore behavior (&rgb_mem).\n"
        " *\n"
        " * Press: memorize the current underglow color and on/off state.\n"
        " * Release: restore both. Global locality, so each half memorizes and\n"
        " * restores its own strip. One memory slot: nested holds overwrite the\n"
        " * saved value and restore the innermost snapshot.\n"
        " */\n"
        "\n"
        "#define DT_DRV_COMPAT zmk_behavior_rgb_remember\n"
        "\n"
        "#include <zephyr/device.h>\n"
        "#include <drivers/behavior.h>\n"
        "#include <zmk/behavior.h>\n"
        "#include <zmk/rgb_underglow.h>\n"
        "\n"
        "#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)\n"
        "\n"
        "static struct zmk_led_hsb saved_color;\n"
        "static bool saved_on;\n"
        "static bool have_saved;\n"
        "\n"
        "static int on_pressed(struct zmk_behavior_binding *binding,\n"
        "                      struct zmk_behavior_binding_event event) {\n"
        "    zmk_rgb_underglow_get_state(&saved_on);\n"
        "    /* calc_hue(0) returns the current color unchanged. */\n"
        "    saved_color = zmk_rgb_underglow_calc_hue(0);\n"
        "    have_saved = true;\n"
        "    return ZMK_BEHAVIOR_OPAQUE;\n"
        "}\n"
        "\n"
        "static int on_released(struct zmk_behavior_binding *binding,\n"
        "                       struct zmk_behavior_binding_event event) {\n"
        "    if (have_saved) {\n"
        "        zmk_rgb_underglow_set_hsb(saved_color);\n"
        "        if (saved_on) {\n"
        "            zmk_rgb_underglow_on();\n"
        "        } else {\n"
        "            zmk_rgb_underglow_off();\n"
        "        }\n"
        "    }\n"
        "    return ZMK_BEHAVIOR_OPAQUE;\n"
        "}\n"
        "\n"
        "static const struct behavior_driver_api rgb_remember_driver_api = {\n"
        "    .binding_pressed = on_pressed,\n"
        "    .binding_released = on_released,\n"
        "    .locality = BEHAVIOR_LOCALITY_GLOBAL,\n"
        "/* Required or Studio's set-binding RPC rejects direct assignment\n"
        " * with INVALID_PARAMETERS (metadata lookup errors out). */\n"
        "#if IS_ENABLED(CONFIG_ZMK_BEHAVIOR_METADATA)\n"
        "    .get_parameter_metadata = zmk_behavior_get_empty_param_metadata,\n"
        "#endif\n"
        "};\n"
        "\n"
        "BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,\n"
        "                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,\n"
        "                        &rgb_remember_driver_api);\n"
        "\n"
        "#endif /* DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT) */\n"
    )

    RGB_REMEMBER_NODE: str = (
        "    behaviors {\n"
        "        rgb_mem: rgb_mem {\n"
        '            compatible = "zmk,behavior-rgb-remember";\n'
        "            #binding-cells = <0>;\n"
        '            display-name = "RGB Save/Restore";\n'
        "        };\n"
        "        nl_guard: nl_guard {\n"
        '            compatible = "zmk,behavior-numlock-guard";\n'
        "            #binding-cells = <0>;\n"
        '            display-name = "NumLock Guard";\n'
        "        };\n"
        "        /* Node name <= 8 chars: the BLE split relay truncates\n"
        "         * longer behavior names and the right half never runs it. */\n"
        "        batt_chk: batt_chk {\n"
        '            compatible = "zmk,behavior-battcheck";\n'
        "            #binding-cells = <0>;\n"
        '            display-name = "BattCheck";\n'
        "        };\n"
        "    };\n\n"
    )

    BATTCHECK_BINDING: str = (
        "description: |\n"
        "  Shows each half's own battery charge as an underglow color for a\n"
        "  configured time (0% = red, through orange and yellow, 100% =\n"
        "  green), then restores the previous color, effect, and on/off\n"
        "  state. Global: one press runs on both halves, each reading its\n"
        "  own battery.\n"
        "\n"
        'compatible: "zmk,behavior-battcheck"\n'
        "\n"
        "properties:\n"
        '  "#binding-cells":\n'
        "    type: int\n"
        "    required: true\n"
        "    const: 0\n"
        "  display-name:\n"
        "    type: string\n"
    )

    BATTCHECK_SOURCE: str = (
        "/*\n"
        " * BattCheck behavior (&batt_chk).\n"
        " *\n"
        " * Press: each half shows its own battery charge as an underglow\n"
        " * color for CONFIG_KEYMAP_BATTCHECK_MS - 0% = red (hue 0) through\n"
        " * orange and yellow to 100% = green (hue 120) - then the previous\n"
        " * color, effect, and on/off state are restored. Global locality:\n"
        " * one key press runs on both halves, each reading its own battery.\n"
        " * A re-press during the glow refreshes the color and restarts the\n"
        " * timer without overwriting the original snapshot. The solid\n"
        " * effect is forced during the glow so a moving effect cannot\n"
        " * repaint over the battery color; direct set_hsb calls are not\n"
        " * persisted by ZMK, so the temporary color can never become the\n"
        " * remembered one.\n"
        " *\n"
        " * Threading: on the peripheral the press arrives on the BT RX\n"
        " * thread, which the system workqueue can preempt, so the press\n"
        " * handler only submits work - BOTH the show and the restore run\n"
        " * on the system workqueue, serializing all state. The node name\n"
        " * must stay within 8 characters (the BLE split relay truncates\n"
        " * longer behavior names; the peripheral then never finds the\n"
        " * behavior). The duration is clamped below the idle timeout at\n"
        " * generation time so a glow cannot span an idle transition.\n"
        " */\n"
        "\n"
        "#define DT_DRV_COMPAT zmk_behavior_battcheck\n"
        "\n"
        "#include <zephyr/device.h>\n"
        "#include <zephyr/kernel.h>\n"
        "#include <drivers/behavior.h>\n"
        "#include <zmk/behavior.h>\n"
        "#include <zmk/battery.h>\n"
        "#include <zmk/rgb_underglow.h>\n"
        "\n"
        "#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)\n"
        "\n"
        "/* Effect 0 is solid in ZMK's effect table. */\n"
        "#define BATTCHECK_EFFECT_SOLID 0\n"
        "\n"
        "static struct zmk_led_hsb saved_color;\n"
        "static int saved_effect;\n"
        "static bool saved_on;\n"
        "static bool glowing;\n"
        "\n"
        "static void restore_glow(struct k_work *work) {\n"
        "    if (!glowing) {\n"
        "        return;\n"
        "    }\n"
        "    glowing = false;\n"
        "    zmk_rgb_underglow_select_effect(saved_effect);\n"
        "    zmk_rgb_underglow_set_hsb(saved_color);\n"
        "    if (saved_on) {\n"
        "        zmk_rgb_underglow_on();\n"
        "    } else {\n"
        "        zmk_rgb_underglow_off();\n"
        "    }\n"
        "}\n"
        "\n"
        "static K_WORK_DELAYABLE_DEFINE(battcheck_restore_work, restore_glow);\n"
        "\n"
        "static void show_glow(struct k_work *work) {\n"
        "    if (!glowing) {\n"
        "        zmk_rgb_underglow_get_state(&saved_on);\n"
        "        /* calc_hue(0)/calc_effect(0) return the current values\n"
        "         * unchanged. */\n"
        "        saved_color = zmk_rgb_underglow_calc_hue(0);\n"
        "        saved_effect = zmk_rgb_underglow_calc_effect(0);\n"
        "        glowing = true;\n"
        "    }\n"
        "#if IS_ENABLED(CONFIG_ZMK_BATTERY_REPORTING)\n"
        "    uint8_t soc = zmk_battery_state_of_charge();\n"
        "#else\n"
        "    uint8_t soc = 0;\n"
        "#endif\n"
        "    if (soc > 100) {\n"
        "        soc = 100;\n"
        "    }\n"
        "    struct zmk_led_hsb glow = {\n"
        "        .h = (uint16_t)(((uint32_t)soc * 120) / 100),\n"
        "        .s = 100,\n"
        "        /* At least the stock start brightness, so the gauge is\n"
        "         * visible even when the user's glow is dim or off. The\n"
        "         * 0-100 stored scale renders remapped onto the compiled\n"
        "         * BRT_MIN..BRT_MAX window. */\n"
        "        .b = MAX(saved_color.b, CONFIG_ZMK_RGB_UNDERGLOW_BRT_START),\n"
        "    };\n"
        "    zmk_rgb_underglow_select_effect(BATTCHECK_EFFECT_SOLID);\n"
        "    zmk_rgb_underglow_set_hsb(glow);\n"
        "    zmk_rgb_underglow_on();\n"
        "    k_work_reschedule(&battcheck_restore_work,\n"
        "                      K_MSEC(CONFIG_KEYMAP_BATTCHECK_MS));\n"
        "}\n"
        "\n"
        "static K_WORK_DEFINE(battcheck_show_work, show_glow);\n"
        "\n"
        "static int on_pressed(struct zmk_behavior_binding *binding,\n"
        "                      struct zmk_behavior_binding_event event) {\n"
        "    k_work_submit(&battcheck_show_work);\n"
        "    return ZMK_BEHAVIOR_OPAQUE;\n"
        "}\n"
        "\n"
        "static int on_released(struct zmk_behavior_binding *binding,\n"
        "                       struct zmk_behavior_binding_event event) {\n"
        "    return ZMK_BEHAVIOR_OPAQUE;\n"
        "}\n"
        "\n"
        "static const struct behavior_driver_api battcheck_driver_api = {\n"
        "    .binding_pressed = on_pressed,\n"
        "    .binding_released = on_released,\n"
        "    .locality = BEHAVIOR_LOCALITY_GLOBAL,\n"
        "/* Studio's set-binding RPC validates against parameter metadata and\n"
        " * REJECTS behaviors that provide none (INVALID_PARAMETERS), so\n"
        " * even 0-param behaviors must declare the empty metadata. */\n"
        "#if IS_ENABLED(CONFIG_ZMK_BEHAVIOR_METADATA)\n"
        "    .get_parameter_metadata = zmk_behavior_get_empty_param_metadata,\n"
        "#endif\n"
        "};\n"
        "\n"
        "BEHAVIOR_DT_INST_DEFINE(0, NULL, NULL, NULL, NULL, POST_KERNEL,\n"
        "                        CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,\n"
        "                        &battcheck_driver_api);\n"
        "\n"
        "#endif /* DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT) */\n"
    )

    TRACKBALL_RUNTIME_SOURCE: str = (
        "/*\n"
        " * KeyMapper runtime trackball configuration.\n"
        " *\n"
        " * One input processor handles both trackballs (param1 selects the\n"
        " * ball: 0 = left/central sensor, 1 = right/peripheral sensor, whose\n"
        " * events reach the central over the split link). Mode, speed, and\n"
        " * scroll direction live in a runtime table, editable over an\n"
        " * encrypted BLE GATT characteristic and persisted via Zephyr\n"
        " * settings - so the companion app changes trackball behavior\n"
        " * instantly, no rebuild or reflash. Initial values come from the\n"
        " * CONFIG_KEYMAP_TB_* build options until the first BLE write.\n"
        " */\n"
        "\n"
        "#define DT_DRV_COMPAT keymapper_input_processor_trackball\n"
        "\n"
        "#include <zephyr/kernel.h>\n"
        "#include <zephyr/device.h>\n"
        "#include <zephyr/input/input.h>\n"
        "#include <zephyr/settings/settings.h>\n"
        "#include <drivers/input_processor.h>\n"
        "\n"
        "#if DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT)\n"
        "\n"
        "#define KEYMAP_TB_MODE_MOUSE 0\n"
        "#define KEYMAP_TB_MODE_VSCROLL 1\n"
        "#define KEYMAP_TB_MODE_HSCROLL 2\n"
        "#define KEYMAP_TB_MODE_DISABLED 3\n"
        "#define KEYMAP_TB_FLAG_NATURAL 0x01\n"
        "#define KEYMAP_TB_BALL_COUNT 2\n"
        "#define KEYMAP_TB_PAYLOAD_VERSION 1\n"
        "#define KEYMAP_TB_PAYLOAD_LEN (1 + KEYMAP_TB_BALL_COUNT * 4)\n"
        "\n"
        "struct keymap_tb_ball_config {\n"
        "    uint8_t mode;\n"
        "    uint8_t multiplier;\n"
        "    uint8_t divisor;\n"
        "    uint8_t flags;\n"
        "};\n"
        "\n"
        "/* Values compiled in from the companion app's staged settings. */\n"
        "static const struct keymap_tb_ball_config\n"
        "    kconfig_defaults[KEYMAP_TB_BALL_COUNT] = {\n"
        "    {CONFIG_KEYMAP_TB_LEFT_MODE, CONFIG_KEYMAP_TB_LEFT_MUL,\n"
        "     CONFIG_KEYMAP_TB_LEFT_DIV, CONFIG_KEYMAP_TB_LEFT_FLAGS},\n"
        "    {CONFIG_KEYMAP_TB_RIGHT_MODE, CONFIG_KEYMAP_TB_RIGHT_MUL,\n"
        "     CONFIG_KEYMAP_TB_RIGHT_DIV, CONFIG_KEYMAP_TB_RIGHT_FLAGS},\n"
        "};\n"
        "\n"
        "static struct keymap_tb_ball_config ball_configs[KEYMAP_TB_BALL_COUNT] = {\n"
        "    {CONFIG_KEYMAP_TB_LEFT_MODE, CONFIG_KEYMAP_TB_LEFT_MUL,\n"
        "     CONFIG_KEYMAP_TB_LEFT_DIV, CONFIG_KEYMAP_TB_LEFT_FLAGS},\n"
        "    {CONFIG_KEYMAP_TB_RIGHT_MODE, CONFIG_KEYMAP_TB_RIGHT_MUL,\n"
        "     CONFIG_KEYMAP_TB_RIGHT_DIV, CONFIG_KEYMAP_TB_RIGHT_FLAGS},\n"
        "};\n"
        "\n"
        "/* Last-known-good copy, updated only by validated GATT writes and\n"
        " * validated settings loads. Wake and reconnect re-assert the active\n"
        " * table from it, so whatever transiently disturbs the active table\n"
        " * around sleep/wake cannot outlive the next wake. */\n"
        "static struct keymap_tb_ball_config golden_configs[KEYMAP_TB_BALL_COUNT] = {\n"
        "    {CONFIG_KEYMAP_TB_LEFT_MODE, CONFIG_KEYMAP_TB_LEFT_MUL,\n"
        "     CONFIG_KEYMAP_TB_LEFT_DIV, CONFIG_KEYMAP_TB_LEFT_FLAGS},\n"
        "    {CONFIG_KEYMAP_TB_RIGHT_MODE, CONFIG_KEYMAP_TB_RIGHT_MUL,\n"
        "     CONFIG_KEYMAP_TB_RIGHT_DIV, CONFIG_KEYMAP_TB_RIGHT_FLAGS},\n"
        "};\n"
        "\n"
        "/* Persisted record: the active config plus the compiled-in baseline\n"
        " * it was saved against. A new firmware build with different staged\n"
        " * settings changes the baseline, which invalidates the stored record\n"
        " * - so a rebuild + flash always applies the app's settings even when\n"
        " * an older live write is still in the settings flash. */\n"
        "struct keymap_tb_persist {\n"
        "    struct keymap_tb_ball_config active[KEYMAP_TB_BALL_COUNT];\n"
        "    struct keymap_tb_ball_config baseline[KEYMAP_TB_BALL_COUNT];\n"
        "};\n"
        "\n"
        "/* Scaling remainders per ball and axis, so slow motion is not lost\n"
        " * to integer division (same technique as ZMK's own scaler). */\n"
        "static int16_t remainders[KEYMAP_TB_BALL_COUNT][2];\n"
        "\n"
        "static int tb_handle_event(const struct device *dev,\n"
        "                           struct input_event *event, uint32_t param1,\n"
        "                           uint32_t param2,\n"
        "                           struct zmk_input_processor_state *state) {\n"
        "    if (param1 >= KEYMAP_TB_BALL_COUNT || event->type != INPUT_EV_REL) {\n"
        "        return ZMK_INPUT_PROC_CONTINUE;\n"
        "    }\n"
        "    if (event->code != INPUT_REL_X && event->code != INPUT_REL_Y) {\n"
        "        return ZMK_INPUT_PROC_CONTINUE;\n"
        "    }\n"
        "    const struct keymap_tb_ball_config *cfg = &ball_configs[param1];\n"
        "    if (cfg->mode == KEYMAP_TB_MODE_DISABLED) {\n"
        "        return ZMK_INPUT_PROC_STOP;\n"
        "    }\n"
        "    int axis = (event->code == INPUT_REL_X) ? 0 : 1;\n"
        "    int16_t divisor = MAX(1, (int16_t)cfg->divisor);\n"
        "    int16_t value_mul =\n"
        "        event->value * (int16_t)cfg->multiplier + remainders[param1][axis];\n"
        "    int16_t scaled = value_mul / divisor;\n"
        "    remainders[param1][axis] = value_mul - scaled * divisor;\n"
        "    event->value = scaled;\n"
        "    if (cfg->mode == KEYMAP_TB_MODE_MOUSE) {\n"
        "        return ZMK_INPUT_PROC_CONTINUE;\n"
        "    }\n"
        "    /* Scroll modes: the ball's Y axis is the primary motion. Vertical\n"
        "     * maps it onto the wheel, horizontal onto the horizontal wheel;\n"
        "     * natural direction inverts that primary axis only. */\n"
        "    bool from_y = (event->code == INPUT_REL_Y);\n"
        "    if (cfg->mode == KEYMAP_TB_MODE_VSCROLL) {\n"
        "        event->code = from_y ? INPUT_REL_WHEEL : INPUT_REL_HWHEEL;\n"
        "    } else {\n"
        "        event->code = from_y ? INPUT_REL_HWHEEL : INPUT_REL_WHEEL;\n"
        "    }\n"
        "    if ((cfg->flags & KEYMAP_TB_FLAG_NATURAL) && from_y) {\n"
        "        event->value = -event->value;\n"
        "    }\n"
        "    return ZMK_INPUT_PROC_CONTINUE;\n"
        "}\n"
        "\n"
        "static struct zmk_input_processor_driver_api tb_driver_api = {\n"
        "    .handle_event = tb_handle_event,\n"
        "};\n"
        "\n"
        "#define KEYMAP_TB_INST(n)                                              \\\n"
        "    DEVICE_DT_INST_DEFINE(n, NULL, NULL, NULL, NULL, POST_KERNEL,     \\\n"
        "                          CONFIG_KERNEL_INIT_PRIORITY_DEFAULT,        \\\n"
        "                          &tb_driver_api);\n"
        "\n"
        "DT_INST_FOREACH_STATUS_OKAY(KEYMAP_TB_INST)\n"
        "\n"
        "static int tb_settings_set(const char *name, size_t len,\n"
        "                           settings_read_cb read_cb, void *cb_arg) {\n"
        "    const char *next;\n"
        "    if (settings_name_steq(name, \"cfg\", &next) && !next) {\n"
        "        struct keymap_tb_persist record;\n"
        "        if (len != sizeof(record)) {\n"
        "            return -EINVAL;\n"
        "        }\n"
        "        ssize_t rc = read_cb(cb_arg, &record, sizeof(record));\n"
        "        if (rc < 0) {\n"
        "            return (int)rc;\n"
        "        }\n"
        "        if (memcmp(record.baseline, kconfig_defaults,\n"
        "                   sizeof(kconfig_defaults)) == 0) {\n"
        "            memcpy(ball_configs, record.active, sizeof(ball_configs));\n"
        "            memcpy(golden_configs, record.active, sizeof(golden_configs));\n"
        "        }\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
        "\n"
        "SETTINGS_STATIC_HANDLER_DEFINE(keymapper_tb, \"keymapper_tb\", NULL,\n"
        "                               tb_settings_set, NULL, NULL);\n"
        "\n"
        "/* The BLE config channel lives on the central half only: both\n"
        " * listeners run there, and only the central holds the host bond. */\n"
        "#if !IS_ENABLED(CONFIG_ZMK_SPLIT) || IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)\n"
        "\n"
        "#include <zephyr/bluetooth/bluetooth.h>\n"
        "#include <zephyr/bluetooth/gatt.h>\n"
        "#include <zephyr/bluetooth/uuid.h>\n"
        "#include <zmk/event_manager.h>\n"
        "#include <zmk/events/activity_state_changed.h>\n"
        "\n"
        "#define KEYMAP_TB_SVC_UUID \\\n"
        "    BT_UUID_128_ENCODE(0x6b65796d, 0x0003, 0x4b4d, 0x8000, 0x000000000001ULL)\n"
        "#define KEYMAP_TB_CHR_UUID \\\n"
        "    BT_UUID_128_ENCODE(0x6b65796d, 0x0004, 0x4b4d, 0x8000, 0x000000000001ULL)\n"
        "\n"
        "/* Persist off the BT RX thread: an inline flash write would stall\n"
        " * HCI processing, so saves are debounced onto the system workqueue\n"
        " * (the same pattern ZMK uses for its own settings). */\n"
        "static void tb_save_work_handler(struct k_work *work) {\n"
        "    struct keymap_tb_persist record;\n"
        "    memcpy(record.active, ball_configs, sizeof(record.active));\n"
        "    memcpy(record.baseline, kconfig_defaults, sizeof(record.baseline));\n"
        "    settings_save_one(\"keymapper_tb/cfg\", &record, sizeof(record));\n"
        "}\n"
        "\n"
        "static K_WORK_DELAYABLE_DEFINE(tb_save_work, tb_save_work_handler);\n"
        "\n"
        "/* Wake-time self-healing: re-assert the active table from the\n"
        " * last-known-good copy and clear the scaling remainders. Runs off\n"
        " * the listener thread. */\n"
        "static void tb_reassert_work_handler(struct k_work *work) {\n"
        "    memcpy(ball_configs, golden_configs, sizeof(ball_configs));\n"
        "    memset(remainders, 0, sizeof(remainders));\n"
        "}\n"
        "\n"
        "static K_WORK_DELAYABLE_DEFINE(tb_reassert_work, tb_reassert_work_handler);\n"
        "\n"
        "/* Sleep: flush the config to flash (deep sleep is a power-off; the\n"
        " * reboot must find the latest values). Wake: re-assert the config\n"
        " * so the trackballs always return exactly as programmed. */\n"
        "static int tb_activity_listener(const zmk_event_t *eh) {\n"
        "    const struct zmk_activity_state_changed *ev =\n"
        "        as_zmk_activity_state_changed(eh);\n"
        "    if (ev == NULL) {\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "    if (ev->state == ZMK_ACTIVITY_SLEEP) {\n"
        "        k_work_cancel_delayable(&tb_save_work);\n"
        "        tb_save_work_handler(NULL);\n"
        "    } else if (ev->state == ZMK_ACTIVITY_ACTIVE) {\n"
        "        k_work_reschedule(&tb_reassert_work, K_MSEC(200));\n"
        "    }\n"
        "    return ZMK_EV_EVENT_BUBBLE;\n"
        "}\n"
        "\n"
        "ZMK_LISTENER(keymapper_tb_activity, tb_activity_listener);\n"
        "ZMK_SUBSCRIPTION(keymapper_tb_activity, zmk_activity_state_changed);\n"
        "\n"
        "#if IS_ENABLED(CONFIG_ZMK_SPLIT)\n"
        "#include <zmk/events/split_peripheral_status_changed.h>\n"
        "\n"
        "/* A reconnecting peripheral just rebooted (deep sleep, battery):\n"
        " * re-assert shortly after it settles. */\n"
        "static int tb_peripheral_listener(const zmk_event_t *eh) {\n"
        "    const struct zmk_split_peripheral_status_changed *ev =\n"
        "        as_zmk_split_peripheral_status_changed(eh);\n"
        "    if (ev != NULL && ev->connected) {\n"
        "        k_work_reschedule(&tb_reassert_work, K_MSEC(1000));\n"
        "    }\n"
        "    return ZMK_EV_EVENT_BUBBLE;\n"
        "}\n"
        "\n"
        "ZMK_LISTENER(keymapper_tb_peripheral, tb_peripheral_listener);\n"
        "ZMK_SUBSCRIPTION(keymapper_tb_peripheral,\n"
        "                 zmk_split_peripheral_status_changed);\n"
        "#endif /* CONFIG_ZMK_SPLIT */\n"
        "\n"
        "static ssize_t tb_config_read(struct bt_conn *conn,\n"
        "                              const struct bt_gatt_attr *attr, void *buf,\n"
        "                              uint16_t len, uint16_t offset) {\n"
        "    uint8_t value[KEYMAP_TB_PAYLOAD_LEN];\n"
        "    value[0] = KEYMAP_TB_PAYLOAD_VERSION;\n"
        "    memcpy(&value[1], ball_configs, sizeof(ball_configs));\n"
        "    return bt_gatt_attr_read(conn, attr, buf, len, offset, value,\n"
        "                             sizeof(value));\n"
        "}\n"
        "\n"
        "static ssize_t tb_config_write(struct bt_conn *conn,\n"
        "                               const struct bt_gatt_attr *attr,\n"
        "                               const void *buf, uint16_t len,\n"
        "                               uint16_t offset, uint8_t flags) {\n"
        "    if (offset != 0) {\n"
        "        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);\n"
        "    }\n"
        "    if (len != KEYMAP_TB_PAYLOAD_LEN) {\n"
        "        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);\n"
        "    }\n"
        "    const uint8_t *bytes = buf;\n"
        "    if (bytes[0] != KEYMAP_TB_PAYLOAD_VERSION) {\n"
        "        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);\n"
        "    }\n"
        "    for (int ball = 0; ball < KEYMAP_TB_BALL_COUNT; ball++) {\n"
        "        const uint8_t *b = &bytes[1 + ball * 4];\n"
        "        if (b[0] > KEYMAP_TB_MODE_DISABLED || b[1] < 1 || b[1] > 16 ||\n"
        "            b[2] < 1 || b[2] > 16) {\n"
        "            return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);\n"
        "        }\n"
        "    }\n"
        "    memcpy(ball_configs, &bytes[1], sizeof(ball_configs));\n"
        "    memcpy(golden_configs, &bytes[1], sizeof(golden_configs));\n"
        "    memset(remainders, 0, sizeof(remainders));\n"
        "    /* Persist promptly - a long debounce lost writes to naps. */\n"
        "    k_work_reschedule(&tb_save_work, K_SECONDS(5));\n"
        "    return len;\n"
        "}\n"
        "\n"
        "BT_GATT_SERVICE_DEFINE(\n"
        "    keymapper_tb_svc,\n"
        "    BT_GATT_PRIMARY_SERVICE(BT_UUID_DECLARE_128(KEYMAP_TB_SVC_UUID)),\n"
        "    BT_GATT_CHARACTERISTIC(BT_UUID_DECLARE_128(KEYMAP_TB_CHR_UUID),\n"
        "                           BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE,\n"
        "                           BT_GATT_PERM_READ_ENCRYPT |\n"
        "                               BT_GATT_PERM_WRITE_ENCRYPT,\n"
        "                           tb_config_read, tb_config_write, NULL));\n"
        "\n"
        "#endif /* central-only */\n"
        "\n"
        "#endif /* DT_HAS_COMPAT_STATUS_OKAY(DT_DRV_COMPAT) */\n"
    )

    TRACKBALL_RUNTIME_BINDING: str = (
        "description: KeyMapper runtime-configurable trackball input processor\n"
        "\n"
        'compatible: "keymapper,input-processor-trackball"\n'
        "\n"
        "# The cell MUST be named param1: ZMK's input-listener entry macro\n"
        "# looks specifier cells up by that exact name (ip_one_param.yaml),\n"
        "# and track-remainders must exist for the same macro to compile.\n"
        "properties:\n"
        '  "#input-processor-cells":\n'
        "    type: int\n"
        "    required: true\n"
        "    const: 1\n"
        "  track-remainders:\n"
        "    type: boolean\n"
        "\n"
        "input-processor-cells:\n"
        "  - param1\n"
    )

    VENDOR_PREFIXES: str = "keymapper\tKeyMapper\n"

    UNDERGLOW_WAKE_SYNC_SOURCE: str = (
        "/*\n"
        " * Underglow wake sync.\n"
        " *\n"
        " * Stock ZMK wakes each half's LEDs only on that half's OWN key\n"
        " * activity (the split link carries no activity relay), so after an\n"
        " * idle LED-off one half stays dark. This central-side listener\n"
        " * re-issues a GLOBAL underglow ON whenever the keyboard returns to\n"
        " * active (or a peripheral reconnects, e.g. after deep-sleep reboot)\n"
        " * while the underglow is supposed to be on - both halves relight\n"
        " * with the last color. When the user keeps LEDs off, state is off\n"
        " * and nothing fires.\n"
        " */\n"
        "\n"
        "#include <zephyr/kernel.h>\n"
        "#include <zmk/event_manager.h>\n"
        "#include <zmk/events/activity_state_changed.h>\n"
        "#include <zmk/rgb_underglow.h>\n"
        "#include <zmk/behavior.h>\n"
        "#include <dt-bindings/zmk/rgb.h>\n"
        "\n"
        "#if !IS_ENABLED(CONFIG_ZMK_SPLIT) || IS_ENABLED(CONFIG_ZMK_SPLIT_ROLE_CENTRAL)\n"
        "\n"
        "#if IS_ENABLED(CONFIG_ZMK_SPLIT)\n"
        "#include <zmk/events/split_peripheral_status_changed.h>\n"
        "#endif\n"
        "\n"
        "static void wake_sync_work_handler(struct k_work *work) {\n"
        "    bool on;\n"
        "    if (zmk_rgb_underglow_get_state(&on) != 0 || !on) {\n"
        "        return;\n"
        "    }\n"
        "    /* The rgb_ug behavior has global locality: invoking it on the\n"
        "     * central also relays it to the peripheral over the split link,\n"
        "     * and ON restores the last color without changing it. */\n"
        "    struct zmk_behavior_binding binding = {\n"
        "        .behavior_dev = \"rgb_ug\",\n"
        "        .param1 = RGB_ON_CMD,\n"
        "        .param2 = 0,\n"
        "    };\n"
        "    struct zmk_behavior_binding_event event = {\n"
        "        .layer = 0,\n"
        "        .position = 0,\n"
        "        .timestamp = k_uptime_get(),\n"
        "    };\n"
        "    zmk_behavior_invoke_binding(&binding, event, true);\n"
        "    zmk_behavior_invoke_binding(&binding, event, false);\n"
        "}\n"
        "\n"
        "static K_WORK_DELAYABLE_DEFINE(wake_sync_work, wake_sync_work_handler);\n"
        "\n"
        "static int wake_sync_listener(const zmk_event_t *eh) {\n"
        "    const struct zmk_activity_state_changed *activity =\n"
        "        as_zmk_activity_state_changed(eh);\n"
        "    if (activity != NULL) {\n"
        "        if (activity->state == ZMK_ACTIVITY_ACTIVE) {\n"
        "            /* Deferred so ZMK's own auto-off restore runs first and\n"
        "             * off the listener thread (behavior invoke can block). */\n"
        "            k_work_reschedule(&wake_sync_work, K_MSEC(100));\n"
        "        }\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "#if IS_ENABLED(CONFIG_ZMK_SPLIT)\n"
        "    const struct zmk_split_peripheral_status_changed *peripheral =\n"
        "        as_zmk_split_peripheral_status_changed(eh);\n"
        "    if (peripheral != NULL && peripheral->connected) {\n"
        "        /* Give the reconnected half a moment to settle first. */\n"
        "        k_work_reschedule(&wake_sync_work, K_MSEC(1500));\n"
        "    }\n"
        "#endif\n"
        "    return ZMK_EV_EVENT_BUBBLE;\n"
        "}\n"
        "\n"
        "ZMK_LISTENER(underglow_wake_sync, wake_sync_listener);\n"
        "ZMK_SUBSCRIPTION(underglow_wake_sync, zmk_activity_state_changed);\n"
        "#if IS_ENABLED(CONFIG_ZMK_SPLIT)\n"
        "ZMK_SUBSCRIPTION(underglow_wake_sync, zmk_split_peripheral_status_changed);\n"
        "#endif\n"
        "\n"
        "#endif /* central-only */\n"
    )

    TRACKBALL_RUNTIME_NODE: str = (
        "// Both trackballs run through KeyMapper's runtime processor: mode,\n"
        "// speed, and direction are stored on the keyboard and editable live\n"
        "// over Bluetooth - no rebuild needed to change them.\n"
        "    keymapper_tb: keymapper_tb {\n"
        '        compatible = "keymapper,input-processor-trackball";\n'
        "        #input-processor-cells = <1>;\n"
        "    };\n"
    )

    MODULE_SOURCE: str = (
        "/*\n"
        " * Low-battery underglow alert.\n"
        " *\n"
        " * Each half monitors its own battery reports; when the state of charge\n"
        " * drops to the configured threshold, the underglow blinks the alert\n"
        " * color a configured number of times, then the previous color and\n"
        " * on/off state are restored. Alerts repeat at most once per configured\n"
        " * interval. Battery reports arrive roughly once per minute, so the\n"
        " * effective interval resolution is one minute.\n"
        " */\n"
        "\n"
        "#include <zephyr/kernel.h>\n"
        "#include <zmk/event_manager.h>\n"
        "#include <zmk/events/battery_state_changed.h>\n"
        "#include <zmk/rgb_underglow.h>\n"
        "\n"
        "static struct zmk_led_hsb saved_color;\n"
        "static bool saved_on;\n"
        "static bool blinking;\n"
        "static uint8_t phase;\n"
        "static int64_t last_alert_ms = -((int64_t)CONFIG_KEYMAP_BATTERY_ALERT_INTERVAL_S * 1000);\n"
        "\n"
        "static void blink_step(struct k_work *work);\n"
        "static K_WORK_DELAYABLE_DEFINE(blink_work, blink_step);\n"
        "\n"
        "static void blink_step(struct k_work *work) {\n"
        "    if (phase >= CONFIG_KEYMAP_BATTERY_ALERT_BLINK_COUNT * 2) {\n"
        "        zmk_rgb_underglow_set_hsb(saved_color);\n"
        "        if (saved_on) {\n"
        "            zmk_rgb_underglow_on();\n"
        "        } else {\n"
        "            zmk_rgb_underglow_off();\n"
        "        }\n"
        "        blinking = false;\n"
        "        return;\n"
        "    }\n"
        "    if ((phase % 2) == 0) {\n"
        "        struct zmk_led_hsb alert = {\n"
        "            .h = CONFIG_KEYMAP_BATTERY_ALERT_HUE,\n"
        "            .s = CONFIG_KEYMAP_BATTERY_ALERT_SAT,\n"
        "            .b = CONFIG_KEYMAP_BATTERY_ALERT_BRT,\n"
        "        };\n"
        "        zmk_rgb_underglow_set_hsb(alert);\n"
        "        zmk_rgb_underglow_on();\n"
        "    } else {\n"
        "        zmk_rgb_underglow_off();\n"
        "    }\n"
        "    phase++;\n"
        "    k_work_reschedule(&blink_work, K_MSEC(CONFIG_KEYMAP_BATTERY_ALERT_BLINK_MS));\n"
        "}\n"
        "\n"
        "static int battery_alert_listener(const zmk_event_t *eh) {\n"
        "    const struct zmk_battery_state_changed *ev = as_zmk_battery_state_changed(eh);\n"
        "    if (ev == NULL) {\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "    /* A reading of 0 usually means the gauge has no data yet. */\n"
        "    if (ev->state_of_charge == 0 ||\n"
        "        ev->state_of_charge > CONFIG_KEYMAP_BATTERY_ALERT_THRESHOLD) {\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "    if (blinking) {\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "    int64_t now = k_uptime_get();\n"
        "    if (now - last_alert_ms < (int64_t)CONFIG_KEYMAP_BATTERY_ALERT_INTERVAL_S * 1000) {\n"
        "        return ZMK_EV_EVENT_BUBBLE;\n"
        "    }\n"
        "    last_alert_ms = now;\n"
        "    zmk_rgb_underglow_get_state(&saved_on);\n"
        "    /* calc_hue(0) returns the current color unchanged. */\n"
        "    saved_color = zmk_rgb_underglow_calc_hue(0);\n"
        "    blinking = true;\n"
        "    phase = 0;\n"
        "    k_work_reschedule(&blink_work, K_NO_WAIT);\n"
        "    return ZMK_EV_EVENT_BUBBLE;\n"
        "}\n"
        "\n"
        "ZMK_LISTENER(battery_alert_blink, battery_alert_listener);\n"
        "ZMK_SUBSCRIPTION(battery_alert_blink, zmk_battery_state_changed);\n"
    )


# --------------------------------------------------------------------------- #
# Transports                                                                  #
# --------------------------------------------------------------------------- #
class StudioTransport(Protocol):
    """Byte transport contract used by :class:`StudioClient` and :class:`FakeImprint`."""

    def read(self, timeout_s: float) -> bytes:
        """Return available bytes, or ``b""`` after ``timeout_s`` with no data."""
        ...

    def write(self, data: bytes) -> None:
        """Write ``data`` fully to the peer."""
        ...

    def close(self) -> None:
        """Release the underlying resource; subsequent reads return ``b""``."""
        ...

    def alive(self) -> bool:
        """Whether the transport can still exchange bytes (``False`` once dead)."""
        ...


class SerialTransport:
    """USB CDC-ACM transport over a Windows COM port.

    The Studio link ignores baud settings (CDC-ACM); a short read timeout keeps the
    reader thread responsive to shutdown without busy-waiting.
    """

    def __init__(self, port: str) -> None:
        """Open the serial port.

        Args:
            port: COM port device name, e.g. ``COM5``.
        """
        self._serial = serial.Serial(port=port, timeout=0.05, write_timeout=2.0)
        self._dead = False

    def read(self, timeout_s: float) -> bytes:
        """Read whatever is available within roughly ``timeout_s`` seconds.

        Args:
            timeout_s: Upper bound on blocking time for this call.

        Returns:
            All bytes waiting at the port, or ``b""`` when nothing arrived in
            time or the port has died (unplugged/closed); the latter also marks
            the transport not :meth:`alive`.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                waiting = self._serial.in_waiting
                data = self._serial.read(max(1, waiting))
            except (serial.SerialException, OSError):
                self._dead = True
                return b""
            if data:
                return data
            if time.monotonic() >= deadline:
                return b""

    def write(self, data: bytes) -> None:
        """Write ``data`` to the port.

        Args:
            data: Framed bytes to transmit.
        """
        try:
            self._serial.write(data)
        except (serial.SerialException, OSError):
            self._dead = True
            raise

    def close(self) -> None:
        """Close the serial port; in-flight reads return ``b""`` afterwards."""
        self._dead = True
        try:
            self._serial.close()
        except (serial.SerialException, OSError):
            pass

    def alive(self) -> bool:
        """Whether the port is still open and has not faulted."""
        return not self._dead and bool(self._serial.is_open)


class LoopbackTransport:
    """In-memory transport pair connecting a client to a :class:`FakeImprint`.

    Construct once, then hand :attr:`client_end` to a :class:`StudioClient` and
    :attr:`device_end` to a :class:`FakeImprint`. Each end satisfies
    :class:`StudioTransport`.
    """

    class _End:
        """One direction-aware endpoint of the loopback pair."""

        def __init__(self, rx: "Queue[bytes]", tx: "Queue[bytes]") -> None:
            self._rx = rx
            self._tx = tx
            self._closed = threading.Event()

        def read(self, timeout_s: float) -> bytes:
            """Return the next queued chunk or ``b""`` on timeout/closure."""
            if self._closed.is_set():
                return b""
            try:
                return self._rx.get(timeout=timeout_s)
            except Empty:
                return b""

        def write(self, data: bytes) -> None:
            """Queue ``data`` for the peer endpoint."""
            if not self._closed.is_set():
                self._tx.put(data)

        def close(self) -> None:
            """Mark this endpoint closed; reads return ``b""`` immediately."""
            self._closed.set()

        def alive(self) -> bool:
            """Whether this endpoint is still open."""
            return not self._closed.is_set()

    def __init__(self) -> None:
        a_to_b: "Queue[bytes]" = Queue()
        b_to_a: "Queue[bytes]" = Queue()
        self.client_end: LoopbackTransport._End = LoopbackTransport._End(b_to_a, a_to_b)
        self.device_end: LoopbackTransport._End = LoopbackTransport._End(a_to_b, b_to_a)


# --------------------------------------------------------------------------- #
# Studio RPC client                                                           #
# --------------------------------------------------------------------------- #
class StudioClient:
    """Client for the ZMK Studio RPC protocol over any :class:`StudioTransport`.

    One request is outstanding at a time (a lock serialises callers, matching the
    reference implementation). A daemon reader thread decodes frames and routes
    request responses to the waiting caller and notifications to subscribers.

    Every RPC method may raise :class:`StudioTimeoutError` (no response within
    the configured timeout), :class:`StudioLockedError` (secured call while the
    keyboard is locked), or :class:`StudioRpcError` (protocol-level error);
    per-method ``Raises`` sections list only conditions specific to that method.

    Usage::

        client = StudioClient(SerialTransport("COM5"), rpc_timeout_s=5.0)
        client.subscribe(lambda ev: print(ev))
        info = client.get_device_info()
        ...
        client.close()
    """

    def __init__(self, transport: StudioTransport, rpc_timeout_s: float) -> None:
        """Start the client over an already-open transport.

        Args:
            transport: Open byte transport to the keyboard.
            rpc_timeout_s: Seconds to wait for each response before raising
                :class:`StudioTimeoutError`.
        """
        self._transport = transport
        self._timeout_s = float(rpc_timeout_s)
        self._decoder = Framing.Decoder()
        self._request_lock = threading.Lock()
        self._next_request_id = 1
        self._responses: "Queue[Any]" = Queue()
        self._subscribers: List[Callable[[ConnectionEvent], None]] = []
        self._alive = threading.Event()
        self._alive.set()
        self._reader = threading.Thread(target=self._reader_loop, name="StudioReader", daemon=True)
        self._reader.start()

    # -- lifecycle ---------------------------------------------------------- #
    def close(self) -> None:
        """Stop the reader thread and close the transport."""
        self._alive.clear()
        self._transport.close()
        self._reader.join(timeout=2.0)

    @property
    def connected(self) -> bool:
        """Whether the reader thread still considers the transport usable."""
        return self._alive.is_set()

    def subscribe(self, callback: Callable[[ConnectionEvent], None]) -> None:
        """Register ``callback`` for unsolicited events.

        Args:
            callback: Called from the reader thread with a :class:`ConnectionEvent`
                for lock changes, unsaved-changes changes, and connection loss.
                Must not block.
        """
        self._subscribers.append(callback)

    # -- reader ------------------------------------------------------------- #
    def _reader_loop(self) -> None:
        """Pump transport bytes into the decoder and dispatch decoded messages.

        Runs on the daemon reader thread until :meth:`close` is called or the
        transport dies (USB unplug, port fault). Death is detected via the
        transport's ``alive()`` flag; subscribers then receive a single
        ``CONNECTION_LOST`` event and the client reports ``connected == False``.
        """
        while self._alive.is_set():
            chunk = self._transport.read(timeout_s=0.2)
            if not chunk:
                if self._alive.is_set() and not self._transport.alive():
                    self._alive.clear()
                    event = ConnectionEvent(
                        kind=NotificationKind.CONNECTION_LOST, value="transport closed"
                    )
                    for callback in list(self._subscribers):
                        callback(event)
                    return
                continue
            for payload in self._decoder.feed(chunk):
                try:
                    response = studio_pb2.Response.FromString(payload)
                except Exception:
                    continue
                which = response.WhichOneof("type")
                if which == "request_response":
                    self._responses.put(response.request_response)
                elif which == "notification":
                    self._dispatch_notification(response.notification)

    def _dispatch_notification(self, notification: Any) -> None:
        """Convert a wire notification into a :class:`ConnectionEvent` and fan out.

        Args:
            notification: Decoded ``zmk.studio.Notification`` message.
        """
        event: Optional[ConnectionEvent] = None
        sub = notification.WhichOneof("subsystem")
        if sub == "core":
            if notification.core.WhichOneof("notification_type") == "lock_state_changed":
                event = ConnectionEvent(
                    kind=NotificationKind.LOCK_STATE_CHANGED,
                    value=LockState(int(notification.core.lock_state_changed)),
                )
        elif sub == "keymap":
            if notification.keymap.WhichOneof("notification_type") == "unsaved_changes_status_changed":
                event = ConnectionEvent(
                    kind=NotificationKind.UNSAVED_CHANGES_CHANGED,
                    value=bool(notification.keymap.unsaved_changes_status_changed),
                )
        if event is not None:
            for callback in list(self._subscribers):
                callback(event)

    # -- request plumbing ---------------------------------------------------- #
    def _call(self, request: Any, timeout_s: Optional[float] = None) -> Any:
        """Send ``request`` and return the matching subsystem response message.

        Args:
            request: A ``zmk.studio.Request`` with the subsystem oneof set and
                ``request_id`` left at 0 (assigned here).
            timeout_s: Per-call timeout override; ``None`` uses the client's
                configured timeout. Flash-heavy RPCs pass a larger value.

        Returns:
            The subsystem response message (``core``/``behaviors``/``keymap``).

        Raises:
            StudioTimeoutError: No response within the effective timeout.
            StudioLockedError: The keyboard reported UNLOCK_REQUIRED.
            StudioRpcError: Any other meta error condition.
        """
        effective_timeout = self._timeout_s if timeout_s is None else timeout_s
        with self._request_lock:
            request.request_id = self._next_request_id
            self._next_request_id += 1
            # Drain stale responses from a previously timed-out call so the
            # request/response correlation cannot skew permanently.
            while True:
                try:
                    self._responses.get_nowait()
                except Empty:
                    break
            self._transport.write(Framing.encode(request.SerializeToString()))
            deadline = time.monotonic() + effective_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StudioTimeoutError(
                        f"no response to request {request.request_id} "
                        f"within {effective_timeout}s"
                    )
                try:
                    rr = self._responses.get(timeout=remaining)
                except Empty:
                    continue
                if rr.request_id != request.request_id:
                    continue
                if rr.WhichOneof("subsystem") == "meta":
                    meta = rr.meta
                    if meta.WhichOneof("response_type") == "simple_error":
                        if meta.simple_error == meta_pb2.ErrorConditions.UNLOCK_REQUIRED:
                            raise StudioLockedError("keyboard is locked; unlock is physical")
                        raise StudioRpcError(
                            meta_pb2.ErrorConditions.Name(meta.simple_error)
                        )
                    return None
                return getattr(rr, rr.WhichOneof("subsystem"))

    # -- core --------------------------------------------------------------- #
    def get_device_info(self) -> DeviceInfo:
        """Read the keyboard's identity (works while locked).

        Returns:
            The keyboard's :class:`DeviceInfo`.
        """
        req = studio_pb2.Request()
        req.core.get_device_info = True
        resp = self._call(req)
        return DeviceInfo(
            name=resp.get_device_info.name,
            serial_number=resp.get_device_info.serial_number.hex(),
        )

    def get_lock_state(self) -> LockState:
        """Read the current Studio lock state (works while locked).

        Returns:
            Current :class:`LockState`.
        """
        req = studio_pb2.Request()
        req.core.get_lock_state = True
        resp = self._call(req)
        return LockState(int(resp.get_lock_state))

    def reset_settings(self) -> bool:
        """Erase all Studio-saved settings ("Restore Stock Settings").

        Deletes the keymap/layer-order/layer-name settings keys so the keymap
        compiled into the firmware becomes authoritative again. Destructive:
        callers must ensure a backup exists first. Deleting a full stored keymap
        is thousands of flash operations, so this call waits with the slow-RPC
        timeout.

        Returns:
            ``True`` when the keyboard confirmed the reset.
        """
        req = studio_pb2.Request()
        req.core.reset_settings = True
        resp = self._call(req, timeout_s=max(self._timeout_s, SLOW_RPC_TIMEOUT_S))
        return bool(resp.reset_settings)

    # -- behaviors ----------------------------------------------------------- #
    def list_behavior_ids(self) -> List[int]:
        """List the wire IDs of every behavior compiled into the firmware.

        Returns:
            Behavior IDs usable with :meth:`get_behavior_details` and in bindings.
        """
        req = studio_pb2.Request()
        req.behaviors.list_all_behaviors = True
        resp = self._call(req)
        return list(resp.list_all_behaviors.behaviors)

    def get_behavior_details(self, behavior_id: int) -> BehaviorDetails:
        """Fetch name and parameter metadata for one behavior.

        Args:
            behavior_id: Wire ID from :meth:`list_behavior_ids`.

        Returns:
            The behavior's :class:`BehaviorDetails`.
        """
        req = studio_pb2.Request()
        req.behaviors.get_behavior_details.behavior_id = behavior_id
        resp = self._call(req)
        details = resp.get_behavior_details

        def _domain(desc: Any) -> ParamDomain:
            kind = desc.WhichOneof("value_type") or "nil"
            domain = ParamDomain(name=desc.name, kind=kind)
            if kind == "constant":
                domain.constant = int(desc.constant)
            elif kind == "range":
                domain.range_min = int(desc.range.min)
                domain.range_max = int(desc.range.max)
            elif kind == "hid_usage":
                domain.keyboard_max = int(desc.hid_usage.keyboard_max)
                domain.consumer_max = int(desc.hid_usage.consumer_max)
            return domain

        param_sets: List[Tuple[List[ParamDomain], List[ParamDomain]]] = [
            ([_domain(d) for d in ps.param1], [_domain(d) for d in ps.param2])
            for ps in details.metadata
        ]
        return BehaviorDetails(
            behavior_id=int(details.id),
            display_name=details.display_name,
            param_sets=param_sets,
        )

    # -- keymap --------------------------------------------------------------- #
    def get_keymap(self) -> Keymap:
        """Read the full keymap (requires unlocked keyboard).

        Returns:
            The current :class:`Keymap` including any unsaved edits.
        """
        req = studio_pb2.Request()
        req.keymap.get_keymap = True
        resp = self._call(req)
        km = resp.get_keymap
        return Keymap(
            layers=[
                Layer(
                    layer_id=int(l.id),
                    name=l.name,
                    bindings=[
                        Binding(int(b.behavior_id), int(b.param1), int(b.param2))
                        for b in l.bindings
                    ],
                )
                for l in km.layers
            ],
            available_layers=int(km.available_layers),
            max_layer_name_length=int(km.max_layer_name_length),
        )

    def get_physical_layouts(self) -> PhysicalLayouts:
        """Read every physical layout and which one is active (requires unlock).

        Returns:
            The keyboard's :class:`PhysicalLayouts`.
        """
        req = studio_pb2.Request()
        req.keymap.get_physical_layouts = True
        resp = self._call(req)
        pl = resp.get_physical_layouts
        return PhysicalLayouts(
            active_index=int(pl.active_layout_index),
            layouts=[
                PhysicalLayout(
                    name=layout.name,
                    keys=[
                        PhysicalKey(
                            width=int(k.width),
                            height=int(k.height),
                            x=int(k.x),
                            y=int(k.y),
                            r=int(k.r),
                            rx=int(k.rx),
                            ry=int(k.ry),
                        )
                        for k in layout.keys
                    ],
                )
                for layout in pl.layouts
            ],
        )

    def set_layer_binding(self, layer_id: int, key_position: int, binding: Binding) -> None:
        """Bind a behavior to one key of one layer (pending until saved).

        Args:
            layer_id: Target layer's stable ID.
            key_position: Key index within the active physical layout.
            binding: Behavior reference and parameters to assign.

        Raises:
            StudioRpcError: With the firmware's error-code name when the layer,
                position, behavior, or parameters were rejected.
        """
        req = studio_pb2.Request()
        req.keymap.set_layer_binding.layer_id = layer_id
        req.keymap.set_layer_binding.key_position = key_position
        req.keymap.set_layer_binding.binding.behavior_id = binding.behavior_id
        req.keymap.set_layer_binding.binding.param1 = binding.param1
        req.keymap.set_layer_binding.binding.param2 = binding.param2
        resp = self._call(req)
        code = int(resp.set_layer_binding)
        if code != keymap_pb2.SetLayerBindingResponse.SET_LAYER_BINDING_RESP_OK:
            raise StudioRpcError(keymap_pb2.SetLayerBindingResponse.Name(code))

    def check_unsaved_changes(self) -> bool:
        """Return whether edits are pending that have not been saved to flash."""
        req = studio_pb2.Request()
        req.keymap.check_unsaved_changes = True
        resp = self._call(req)
        return bool(resp.check_unsaved_changes)

    def save_changes(self) -> None:
        """Persist pending edits to the keyboard's settings flash.

        Persisting many changed bindings is flash-heavy, so this call waits
        with the slow-RPC timeout.

        Raises:
            StudioRpcError: With the firmware's save error-code name on failure.
        """
        req = studio_pb2.Request()
        req.keymap.save_changes = True
        resp = self._call(req, timeout_s=max(self._timeout_s, SLOW_RPC_TIMEOUT_S))
        if resp.save_changes.WhichOneof("result") == "err":
            raise StudioRpcError(keymap_pb2.SaveChangesErrorCode.Name(resp.save_changes.err))

    def discard_changes(self) -> None:
        """Revert all pending edits back to the last saved state."""
        req = studio_pb2.Request()
        req.keymap.discard_changes = True
        self._call(req)

    def set_layer_props(self, layer_id: int, name: str) -> None:
        """Rename a layer (pending until saved).

        Args:
            layer_id: Target layer's stable ID.
            name: New display name (within the firmware's length limit).

        Raises:
            StudioRpcError: With the firmware's error-code name on failure.
        """
        req = studio_pb2.Request()
        req.keymap.set_layer_props.layer_id = layer_id
        req.keymap.set_layer_props.name = name
        resp = self._call(req)
        code = int(resp.set_layer_props)
        if code != keymap_pb2.SetLayerPropsResponse.SET_LAYER_PROPS_RESP_OK:
            raise StudioRpcError(keymap_pb2.SetLayerPropsResponse.Name(code))

    def add_layer(self) -> Tuple[int, Layer]:
        """Enable one additional (reserved) layer.

        Returns:
            Tuple of (index in the layer order, the new :class:`Layer`).

        Raises:
            StudioRpcError: With the firmware's error-code name (e.g. NO_SPACE).
        """
        req = studio_pb2.Request()
        req.keymap.add_layer.SetInParent()
        resp = self._call(req)
        if resp.add_layer.WhichOneof("result") == "err":
            raise StudioRpcError(keymap_pb2.AddLayerErrorCode.Name(resp.add_layer.err))
        ok = resp.add_layer.ok
        return int(ok.index), Layer(
            layer_id=int(ok.layer.id),
            name=ok.layer.name,
            bindings=[
                Binding(int(b.behavior_id), int(b.param1), int(b.param2))
                for b in ok.layer.bindings
            ],
        )

    def remove_layer(self, layer_index: int) -> None:
        """Disable the layer at ``layer_index`` (restorable until settings reset).

        Args:
            layer_index: Position of the layer in the current order.

        Raises:
            StudioRpcError: With the firmware's error-code name on failure.
        """
        req = studio_pb2.Request()
        req.keymap.remove_layer.layer_index = layer_index
        resp = self._call(req)
        if resp.remove_layer.WhichOneof("result") == "err":
            raise StudioRpcError(keymap_pb2.RemoveLayerErrorCode.Name(resp.remove_layer.err))

    def restore_layer(self, layer_id: int, at_index: int) -> Layer:
        """Re-enable a previously removed layer.

        Args:
            layer_id: Stable ID of the removed layer.
            at_index: Position to insert it at in the layer order.

        Returns:
            The restored :class:`Layer`.

        Raises:
            StudioRpcError: With the firmware's error-code name on failure.
        """
        req = studio_pb2.Request()
        req.keymap.restore_layer.layer_id = layer_id
        req.keymap.restore_layer.at_index = at_index
        resp = self._call(req)
        if resp.restore_layer.WhichOneof("result") == "err":
            raise StudioRpcError(keymap_pb2.RestoreLayerErrorCode.Name(resp.restore_layer.err))
        ok = resp.restore_layer.ok
        return Layer(
            layer_id=int(ok.id),
            name=ok.name,
            bindings=[
                Binding(int(b.behavior_id), int(b.param1), int(b.param2)) for b in ok.bindings
            ],
        )

    def move_layer(self, start_index: int, dest_index: int) -> Keymap:
        """Reorder layers by moving one to a new position.

        Args:
            start_index: Current position of the layer.
            dest_index: Desired position.

        Returns:
            The full reordered :class:`Keymap` as confirmed by the keyboard.

        Raises:
            StudioRpcError: With the firmware's error-code name on failure.
        """
        req = studio_pb2.Request()
        req.keymap.move_layer.start_index = start_index
        req.keymap.move_layer.dest_index = dest_index
        resp = self._call(req)
        if resp.move_layer.WhichOneof("result") == "err":
            raise StudioRpcError(keymap_pb2.MoveLayerErrorCode.Name(resp.move_layer.err))
        km = resp.move_layer.ok
        return Keymap(
            layers=[
                Layer(
                    layer_id=int(l.id),
                    name=l.name,
                    bindings=[
                        Binding(int(b.behavior_id), int(b.param1), int(b.param2))
                        for b in l.bindings
                    ],
                )
                for l in km.layers
            ],
            available_layers=int(km.available_layers),
            max_layer_name_length=int(km.max_layer_name_length),
        )

    def set_active_physical_layout(self, layout_index: int) -> Keymap:
        """Switch the active physical layout.

        Args:
            layout_index: Index into the layouts list.

        Returns:
            The full :class:`Keymap` re-mapped to the new layout.

        Raises:
            StudioRpcError: With the firmware's error-code name on failure.
        """
        req = studio_pb2.Request()
        req.keymap.set_active_physical_layout = layout_index
        resp = self._call(req)
        if resp.set_active_physical_layout.WhichOneof("result") == "err":
            raise StudioRpcError(
                keymap_pb2.SetActivePhysicalLayoutErrorCode.Name(
                    resp.set_active_physical_layout.err
                )
            )
        km = resp.set_active_physical_layout.ok
        return Keymap(
            layers=[
                Layer(
                    layer_id=int(l.id),
                    name=l.name,
                    bindings=[
                        Binding(int(b.behavior_id), int(b.param1), int(b.param2))
                        for b in l.bindings
                    ],
                )
                for l in km.layers
            ],
            available_layers=int(km.available_layers),
            max_layer_name_length=int(km.max_layer_name_length),
        )


# --------------------------------------------------------------------------- #
# Fake device for offline tests and demo mode                                 #
# --------------------------------------------------------------------------- #
class FakeImprint:
    """In-process keyboard implementing the real Studio wire protocol.

    Runs a daemon thread that decodes framed requests from a transport endpoint,
    mutates an in-memory keymap model, and answers with correctly encoded
    responses — including UNLOCK_REQUIRED gating for secured RPCs and unsolicited
    notifications for lock and unsaved-changes transitions. Used by unit tests and
    by the backend's demo mode (no hardware attached).

    The behavior catalog mirrors the stock Imprint firmware's most relevant
    entries; wire IDs are arbitrary but stable within a process.
    """

    BEHAVIOR_KEY_PRESS: int = 1
    BEHAVIOR_MOMENTARY_LAYER: int = 2
    BEHAVIOR_TO_LAYER: int = 3
    BEHAVIOR_TOGGLE_LAYER: int = 4
    BEHAVIOR_TRANSPARENT: int = 5
    BEHAVIOR_NONE: int = 6
    BEHAVIOR_RGB_UNDERGLOW: int = 7
    BEHAVIOR_STUDIO_UNLOCK: int = 8

    TOTAL_LAYERS: int = 32
    KEY_COUNT: int = 6
    MAX_LAYER_NAME_LENGTH: int = 20

    def __init__(self, transport_end: StudioTransport, locked: bool = True) -> None:
        """Start the fake device.

        Args:
            transport_end: The device side of a :class:`LoopbackTransport`.
            locked: Whether the device boots in the locked state (stock firmware
                behavior). Unlock with :meth:`press_unlock_combo`.
        """
        self._transport = transport_end
        self._locked = bool(locked)
        self._decoder = Framing.Decoder()
        self._alive = threading.Event()
        self._alive.set()

        self.behaviors: Dict[int, Dict[str, Any]] = {
            FakeImprint.BEHAVIOR_KEY_PRESS: {
                "name": "Key Press",
                "metadata": [("hid_usage", "nil")],
            },
            FakeImprint.BEHAVIOR_MOMENTARY_LAYER: {
                "name": "Momentary Layer",
                "metadata": [("layer_id", "nil")],
            },
            FakeImprint.BEHAVIOR_TO_LAYER: {
                "name": "To Layer",
                "metadata": [("layer_id", "nil")],
            },
            FakeImprint.BEHAVIOR_TOGGLE_LAYER: {
                "name": "Toggle Layer",
                "metadata": [("layer_id", "nil")],
            },
            FakeImprint.BEHAVIOR_TRANSPARENT: {
                "name": "Transparent",
                "metadata": [("nil", "nil")],
            },
            FakeImprint.BEHAVIOR_NONE: {
                "name": "None",
                "metadata": [("nil", "nil")],
            },
            FakeImprint.BEHAVIOR_RGB_UNDERGLOW: {
                "name": "Underglow",
                "metadata": [("range", "range")],
            },
            FakeImprint.BEHAVIOR_STUDIO_UNLOCK: {
                "name": "Studio Unlock",
                "metadata": [("nil", "nil")],
            },
        }

        default_names = ["Base", "Numpad_Nav", "Keyboard_Control", "Auto_Mouse", "factory_test"]
        # Key-press parameters use the real wire encoding: HID keyboard page
        # (0x07) in bits 16-23, usage ID in the low bits (0x04 = A, 0x05 = B, …).
        self._saved_layers: List[Layer] = [
            Layer(
                layer_id=i,
                name=name,
                bindings=[
                    Binding(
                        FakeImprint.BEHAVIOR_KEY_PRESS,
                        (DtsMaps.HID_PAGE_KEYBOARD << 16) | (0x04 + k),
                        0,
                    )
                    for k in range(FakeImprint.KEY_COUNT)
                ],
            )
            for i, name in enumerate(default_names)
        ]
        self._layers: List[Layer] = self._clone_layers(self._saved_layers)
        self._removed: Dict[int, Layer] = {}
        self._next_layer_id: int = len(default_names)
        self._unsaved: bool = False

        self.layouts = PhysicalLayouts(
            active_index=0,
            layouts=[
                PhysicalLayout(
                    name="Fake Full",
                    keys=[
                        PhysicalKey(width=100, height=100, x=100 * k, y=0)
                        for k in range(FakeImprint.KEY_COUNT)
                    ],
                ),
                PhysicalLayout(
                    name="Fake Compact",
                    keys=[
                        PhysicalKey(width=100, height=100, x=100 * k, y=100)
                        for k in range(FakeImprint.KEY_COUNT)
                    ],
                ),
            ],
        )

        self._thread = threading.Thread(target=self._serve_loop, name="FakeImprint", daemon=True)
        self._thread.start()

    # -- public controls ------------------------------------------------------ #
    def press_unlock_combo(self) -> None:
        """Simulate holding the physical unlock combo: unlock and notify."""
        self._locked = False
        self._notify_lock_state()

    def lock(self) -> None:
        """Simulate the idle/disconnect relock: lock and notify."""
        self._locked = True
        self._notify_lock_state()

    def stop(self) -> None:
        """Stop the serve thread and close the device transport end."""
        self._alive.clear()
        self._transport.close()
        self._thread.join(timeout=2.0)

    @property
    def saved_layers(self) -> List[Layer]:
        """The layers last persisted with ``save_changes`` (deep snapshot)."""
        return self._clone_layers(self._saved_layers)

    # -- internals ------------------------------------------------------------ #
    @staticmethod
    def _clone_layers(layers: List[Layer]) -> List[Layer]:
        """Deep-copy a layer list so saved and pending states stay independent.

        Args:
            layers: Source layers.

        Returns:
            Independent copy with fresh :class:`Binding` instances.
        """
        return [
            Layer(
                layer_id=l.layer_id,
                name=l.name,
                bindings=[Binding(b.behavior_id, b.param1, b.param2) for b in l.bindings],
            )
            for l in layers
        ]

    def _send(self, response: Any) -> None:
        """Frame and write one ``zmk.studio.Response`` to the transport."""
        self._transport.write(Framing.encode(response.SerializeToString()))

    def _notify_lock_state(self) -> None:
        """Emit the ``lock_state_changed`` notification."""
        resp = studio_pb2.Response()
        resp.notification.core.lock_state_changed = (
            core_pb2.LockState.ZMK_STUDIO_CORE_LOCK_STATE_LOCKED
            if self._locked
            else core_pb2.LockState.ZMK_STUDIO_CORE_LOCK_STATE_UNLOCKED
        )
        self._send(resp)

    def _set_unsaved(self, unsaved: bool) -> None:
        """Track the pending-changes flag; notify on transitions."""
        if unsaved != self._unsaved:
            self._unsaved = unsaved
            resp = studio_pb2.Response()
            resp.notification.keymap.unsaved_changes_status_changed = unsaved
            self._send(resp)

    def _serve_loop(self) -> None:
        """Decode framed requests and dispatch them until stopped."""
        while self._alive.is_set():
            chunk = self._transport.read(timeout_s=0.2)
            if not chunk:
                continue
            for payload in self._decoder.feed(chunk):
                try:
                    request = studio_pb2.Request.FromString(payload)
                except Exception:
                    continue
                self._handle(request)

    def _meta_error(self, request_id: int, condition: int) -> None:
        """Answer ``request_id`` with a meta ``simple_error``."""
        resp = studio_pb2.Response()
        resp.request_response.request_id = request_id
        resp.request_response.meta.simple_error = condition
        self._send(resp)

    def _handle(self, request: Any) -> None:
        """Route one request to its subsystem handler, enforcing the lock."""
        rid = int(request.request_id)
        sub = request.WhichOneof("subsystem")
        if sub == "core":
            self._handle_core(rid, request.core)
        elif sub == "behaviors":
            if self._locked:
                self._meta_error(rid, meta_pb2.ErrorConditions.UNLOCK_REQUIRED)
                return
            self._handle_behaviors(rid, request.behaviors)
        elif sub == "keymap":
            if self._locked:
                self._meta_error(rid, meta_pb2.ErrorConditions.UNLOCK_REQUIRED)
                return
            self._handle_keymap(rid, request.keymap)
        else:
            self._meta_error(rid, meta_pb2.ErrorConditions.RPC_NOT_FOUND)

    def _handle_core(self, rid: int, req: Any) -> None:
        """Serve the ``core`` subsystem (device info and lock state are unsecured)."""
        which = req.WhichOneof("request_type")
        resp = studio_pb2.Response()
        resp.request_response.request_id = rid
        if which == "get_device_info":
            resp.request_response.core.get_device_info.name = "Imprint"
            resp.request_response.core.get_device_info.serial_number = b"\xfa\x4e\x00\x01"
        elif which == "get_lock_state":
            resp.request_response.core.get_lock_state = (
                core_pb2.LockState.ZMK_STUDIO_CORE_LOCK_STATE_LOCKED
                if self._locked
                else core_pb2.LockState.ZMK_STUDIO_CORE_LOCK_STATE_UNLOCKED
            )
        elif which == "reset_settings":
            if self._locked:
                self._meta_error(rid, meta_pb2.ErrorConditions.UNLOCK_REQUIRED)
                return
            self._layers = self._clone_layers(self._saved_layers[:5])
            self._removed.clear()
            self._set_unsaved(False)
            resp.request_response.core.reset_settings = True
        else:
            self._meta_error(rid, meta_pb2.ErrorConditions.RPC_NOT_FOUND)
            return
        self._send(resp)

    def _handle_behaviors(self, rid: int, req: Any) -> None:
        """Serve the ``behaviors`` subsystem from the static catalog."""
        which = req.WhichOneof("request_type")
        resp = studio_pb2.Response()
        resp.request_response.request_id = rid
        if which == "list_all_behaviors":
            resp.request_response.behaviors.list_all_behaviors.behaviors.extend(
                sorted(self.behaviors.keys())
            )
        elif which == "get_behavior_details":
            behavior_id = int(req.get_behavior_details.behavior_id)
            entry = self.behaviors.get(behavior_id)
            if entry is None:
                self._meta_error(rid, meta_pb2.ErrorConditions.GENERIC)
                return
            details = resp.request_response.behaviors.get_behavior_details
            details.id = behavior_id
            details.display_name = entry["name"]
            for p1_kind, p2_kind in entry["metadata"]:
                ps = details.metadata.add()
                for kind, target in ((p1_kind, ps.param1), (p2_kind, ps.param2)):
                    if kind == "nil":
                        continue
                    desc = target.add()
                    desc.name = kind
                    if kind == "hid_usage":
                        desc.hid_usage.keyboard_max = 0xFF
                        desc.hid_usage.consumer_max = 0xFFF
                    elif kind == "layer_id":
                        desc.layer_id.SetInParent()
                    elif kind == "range":
                        desc.range.min = 0
                        desc.range.max = 0xFFFF
        else:
            self._meta_error(rid, meta_pb2.ErrorConditions.RPC_NOT_FOUND)
            return
        self._send(resp)

    def _fill_keymap(self, target: Any) -> None:
        """Copy the pending keymap model into a wire ``Keymap`` message."""
        for layer in self._layers:
            wire_layer = target.layers.add()
            wire_layer.id = layer.layer_id
            wire_layer.name = layer.name
            for b in layer.bindings:
                wb = wire_layer.bindings.add()
                wb.behavior_id = b.behavior_id
                wb.param1 = b.param1
                wb.param2 = b.param2
        target.available_layers = FakeImprint.TOTAL_LAYERS - len(self._layers)
        target.max_layer_name_length = FakeImprint.MAX_LAYER_NAME_LENGTH

    def _handle_keymap(self, rid: int, req: Any) -> None:
        """Serve the ``keymap`` subsystem against the in-memory model."""
        which = req.WhichOneof("request_type")
        resp = studio_pb2.Response()
        resp.request_response.request_id = rid
        if which == "get_keymap":
            self._fill_keymap(resp.request_response.keymap.get_keymap)
        elif which == "get_physical_layouts":
            out = resp.request_response.keymap.get_physical_layouts
            out.active_layout_index = self.layouts.active_index
            for layout in self.layouts.layouts:
                wire_layout = out.layouts.add()
                wire_layout.name = layout.name
                for key in layout.keys:
                    wk = wire_layout.keys.add()
                    wk.width = key.width
                    wk.height = key.height
                    wk.x = key.x
                    wk.y = key.y
                    wk.r = key.r
                    wk.rx = key.rx
                    wk.ry = key.ry
        elif which == "set_layer_binding":
            slb = req.set_layer_binding
            layer = next((l for l in self._layers if l.layer_id == int(slb.layer_id)), None)
            if layer is None:
                resp.request_response.keymap.set_layer_binding = (
                    keymap_pb2.SetLayerBindingResponse.SET_LAYER_BINDING_RESP_INVALID_LOCATION
                )
            elif not (0 <= int(slb.key_position) < len(layer.bindings)):
                resp.request_response.keymap.set_layer_binding = (
                    keymap_pb2.SetLayerBindingResponse.SET_LAYER_BINDING_RESP_INVALID_LOCATION
                )
            elif int(slb.binding.behavior_id) not in self.behaviors:
                resp.request_response.keymap.set_layer_binding = (
                    keymap_pb2.SetLayerBindingResponse.SET_LAYER_BINDING_RESP_INVALID_BEHAVIOR
                )
            else:
                layer.bindings[int(slb.key_position)] = Binding(
                    int(slb.binding.behavior_id),
                    int(slb.binding.param1),
                    int(slb.binding.param2),
                )
                resp.request_response.keymap.set_layer_binding = (
                    keymap_pb2.SetLayerBindingResponse.SET_LAYER_BINDING_RESP_OK
                )
                self._set_unsaved(True)
        elif which == "check_unsaved_changes":
            resp.request_response.keymap.check_unsaved_changes = self._unsaved
        elif which == "save_changes":
            self._saved_layers = self._clone_layers(self._layers)
            resp.request_response.keymap.save_changes.ok = True
            self._set_unsaved(False)
        elif which == "discard_changes":
            self._layers = self._clone_layers(self._saved_layers)
            resp.request_response.keymap.discard_changes = True
            self._set_unsaved(False)
        elif which == "set_layer_props":
            slp = req.set_layer_props
            layer = next((l for l in self._layers if l.layer_id == int(slp.layer_id)), None)
            if layer is None:
                resp.request_response.keymap.set_layer_props = (
                    keymap_pb2.SetLayerPropsResponse.SET_LAYER_PROPS_RESP_ERR_INVALID_ID
                )
            else:
                layer.name = slp.name
                resp.request_response.keymap.set_layer_props = (
                    keymap_pb2.SetLayerPropsResponse.SET_LAYER_PROPS_RESP_OK
                )
                self._set_unsaved(True)
        elif which == "add_layer":
            if len(self._layers) >= FakeImprint.TOTAL_LAYERS:
                resp.request_response.keymap.add_layer.err = (
                    keymap_pb2.AddLayerErrorCode.ADD_LAYER_ERR_NO_SPACE
                )
            else:
                new_layer = Layer(
                    layer_id=self._next_layer_id,
                    name=f"extra{self._next_layer_id}",
                    bindings=[
                        Binding(FakeImprint.BEHAVIOR_TRANSPARENT, 0, 0)
                        for _ in range(FakeImprint.KEY_COUNT)
                    ],
                )
                self._next_layer_id += 1
                self._layers.append(new_layer)
                ok = resp.request_response.keymap.add_layer.ok
                ok.index = len(self._layers) - 1
                ok.layer.id = new_layer.layer_id
                ok.layer.name = new_layer.name
                for b in new_layer.bindings:
                    wb = ok.layer.bindings.add()
                    wb.behavior_id = b.behavior_id
                    wb.param1 = b.param1
                    wb.param2 = b.param2
                self._set_unsaved(True)
        elif which == "remove_layer":
            idx = int(req.remove_layer.layer_index)
            if not (0 <= idx < len(self._layers)):
                resp.request_response.keymap.remove_layer.err = (
                    keymap_pb2.RemoveLayerErrorCode.REMOVE_LAYER_ERR_INVALID_INDEX
                )
            else:
                removed = self._layers.pop(idx)
                self._removed[removed.layer_id] = removed
                resp.request_response.keymap.remove_layer.ok.SetInParent()
                self._set_unsaved(True)
        elif which == "restore_layer":
            layer_id = int(req.restore_layer.layer_id)
            at_index = int(req.restore_layer.at_index)
            stored = self._removed.pop(layer_id, None)
            if stored is None:
                resp.request_response.keymap.restore_layer.err = (
                    keymap_pb2.RestoreLayerErrorCode.RESTORE_LAYER_ERR_INVALID_ID
                )
            elif not (0 <= at_index <= len(self._layers)):
                resp.request_response.keymap.restore_layer.err = (
                    keymap_pb2.RestoreLayerErrorCode.RESTORE_LAYER_ERR_INVALID_INDEX
                )
            else:
                self._layers.insert(at_index, stored)
                ok = resp.request_response.keymap.restore_layer.ok
                ok.id = stored.layer_id
                ok.name = stored.name
                for b in stored.bindings:
                    wb = ok.bindings.add()
                    wb.behavior_id = b.behavior_id
                    wb.param1 = b.param1
                    wb.param2 = b.param2
                self._set_unsaved(True)
        elif which == "move_layer":
            start = int(req.move_layer.start_index)
            dest = int(req.move_layer.dest_index)
            if not (0 <= start < len(self._layers)) or not (0 <= dest < len(self._layers)):
                resp.request_response.keymap.move_layer.err = (
                    keymap_pb2.MoveLayerErrorCode.MOVE_LAYER_ERR_INVALID_LAYER
                )
            else:
                layer = self._layers.pop(start)
                self._layers.insert(dest, layer)
                self._fill_keymap(resp.request_response.keymap.move_layer.ok)
                self._set_unsaved(True)
        elif which == "set_active_physical_layout":
            idx = int(req.set_active_physical_layout)
            if not (0 <= idx < len(self.layouts.layouts)):
                resp.request_response.keymap.set_active_physical_layout.err = (
                    keymap_pb2.SetActivePhysicalLayoutErrorCode.SET_ACTIVE_PHYSICAL_LAYOUT_ERR_INVALID_LAYOUT_INDEX
                )
            else:
                self.layouts.active_index = idx
                self._fill_keymap(resp.request_response.keymap.set_active_physical_layout.ok)
        else:
            self._meta_error(rid, meta_pb2.ErrorConditions.RPC_NOT_FOUND)
            return
        self._send(resp)


# --------------------------------------------------------------------------- #
# API request bodies                                                          #
# --------------------------------------------------------------------------- #
class ApiBindingUpdate(BaseModel):
    """Body of ``PUT /api/binding``.

    Attributes:
        layer_id: Stable ID of the layer to edit.
        key_position: Key index within the active physical layout (>= 0).
        behavior_id: Wire ID of the behavior to bind.
        param1: First behavior parameter.
        param2: Second behavior parameter.
    """

    layer_id: int = Field(ge=0)
    key_position: int = Field(ge=0)
    behavior_id: int
    param1: int = 0
    param2: int = 0


class ApiBulkSet(BaseModel):
    """Body of ``POST /api/bulk_set`` — assign one binding to every key of a layer.

    Attributes:
        layer_id: Stable ID of the layer to fill.
        behavior_id: Wire ID of the behavior to bind everywhere.
        param1: First behavior parameter applied to every key.
        param2: Second behavior parameter applied to every key.
    """

    layer_id: int = Field(ge=0)
    behavior_id: int
    param1: int = 0
    param2: int = 0


class ApiLayerName(BaseModel):
    """Body of ``PUT /api/layer/name``.

    Attributes:
        layer_id: Stable ID of the layer to rename.
        name: New display name (1..40 characters; the firmware may cap shorter).
    """

    layer_id: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=40)


class ApiLayerMove(BaseModel):
    """Body of ``POST /api/layer/move``.

    Attributes:
        start_index: Current position of the layer in the order.
        dest_index: Desired position.
    """

    start_index: int = Field(ge=0)
    dest_index: int = Field(ge=0)


class ApiLayerRemove(BaseModel):
    """Body of ``POST /api/layer/remove``.

    Attributes:
        layer_index: Position of the layer to disable.
    """

    layer_index: int = Field(ge=0)


class ApiLayerRestore(BaseModel):
    """Body of ``POST /api/layer/restore``.

    Attributes:
        layer_id: Stable ID of the previously removed layer.
        at_index: Position to insert it at.
    """

    layer_id: int = Field(ge=0)
    at_index: int = Field(ge=0)


class ApiConfirm(BaseModel):
    """Body of destructive endpoints requiring explicit confirmation.

    Attributes:
        confirm: Must be ``True``; the UI sets it only after the user confirmed
            the consequence in a dialog.
    """

    confirm: bool


class ApiMacros(BaseModel):
    """Body of ``POST /api/firmware/macros`` — stage the macro set to compile.

    Attributes:
        macros: Macro definitions in :meth:`MacroDefinition.to_dict` shape.
    """

    macros: List[Dict[str, Any]]


class ApiGenerate(BaseModel):
    """Body of ``POST /api/firmware/generate``.

    Attributes:
        confirm: Must be ``True`` (the wizard shows what will be generated first).
        backup_name: Backup file to bake in; ``None`` takes a fresh backup from
            the connected keyboard, falling back to the newest existing file.
    """

    confirm: bool
    backup_name: Optional[str] = None


class ApiLayerColor(BaseModel):
    """Body of ``POST /api/firmware/layer_color``.

    Attributes:
        layer: Layer position whose color sequences to recolor (0–31).
        hue: New hue in degrees (0–360).
        saturation: New saturation percent (0–100).
        brightness: New brightness percent (0–100).
    """

    layer: int = Field(ge=0, le=31)
    hue: int = Field(ge=0, le=360)
    saturation: int = Field(ge=0, le=100)
    brightness: int = Field(ge=0, le=100)


class ApiBackupNames(BaseModel):
    """Body of ``POST /api/backups/delete``.

    Attributes:
        names: Backup file names to delete.
        confirm: Must be ``True``; deletion is irreversible.
    """

    names: List[str]
    confirm: bool


class ApiBackupRestore(BaseModel):
    """Body of ``POST /api/backups/restore``.

    Attributes:
        name: Backup file to re-apply to the keyboard.
        confirm: Must be ``True``; existing pending edits are overwritten.
    """

    name: str
    confirm: bool


class ApiBrightness(BaseModel):
    """Body of ``POST /api/firmware/sequence_brightness``.

    Attributes:
        brightness: New brightness (0–100) written into the brightness
            component of every ``RGB_COLOR_HSB(h,s,b)`` in the staged
            sequences. The stored 0–100 scale is remapped by the firmware
            onto its compiled 5–50% output window, so 0 is the darkest
            still-lit setting (the same one the physical brightness-down
            key bottoms out at), not "off".
    """

    brightness: int = Field(ge=0, le=100)


class ApiFlashFile(BaseModel):
    """Body of ``POST /api/firmware/flash``.

    Attributes:
        file: Bare file name of a downloaded ``.uf2`` to flash.
        confirm: Must be ``True``; flashing replaces the firmware on the half
            currently in bootloader mode.
    """

    file: str = Field(min_length=1)
    confirm: bool


# --------------------------------------------------------------------------- #
# Shared application state                                                    #
# --------------------------------------------------------------------------- #
@dataclass
class AppState:
    """Mutable state shared between the API layer and the device manager.

    Attributes:
        params: Validated configuration.
        paths: Resolved project paths from ``.env`` (attributes: DATA_DIR,
            LOGS_DIR, CONFIG_FILE, BACKUPS_DIR, FIRMWARE_WORKSPACE_DIR, WEB_DIR,
            PROTO_DIR).
        logger: Project logger (duck-typed: ``info``/``warning``/``error``).
        client: Live Studio client, or ``None`` while disconnected.
        fake_device: The demo-mode device when running without hardware.
        device_info: Identity of the connected keyboard, when known.
        lock_state: Last observed lock state, when known.
        unsaved_changes: Last observed pending-changes flag.
        port_name: COM port currently in use, when connected.
        ui_clients: Number of WebSocket UI clients currently attached.
        ever_had_ui_client: Whether any UI client has connected since startup;
            arms the short idle-shutdown threshold.
        last_ui_disconnect_monotonic: ``time.monotonic()`` of the moment the last
            UI client detached; drives idle self-shutdown.
        last_backup_path: Newest backup written this session, or ``None``; gates
            destructive operations.
        request_shutdown: Callable installed by the server runner; invoking it
            makes the HTTP server exit gracefully. ``None`` under tests.
        events: Queue of :class:`ConnectionEvent` bridged from the client's reader
            thread into the async API layer.
        process_runner: Callable ``(args, cwd, timeout_s) -> (code, out, err)``
            used for external commands (git/gh); tests inject a fake. ``None``
            selects the real subprocess runner.
        drive_probe: Callable returning candidate bootloader drive roots; tests
            inject temp dirs. ``None`` selects the real drive-letter scan.
        battery_reader: Callable returning raw battery percentages (one per BAS
            instance, central first); tests inject a fake. ``None`` selects the
            real BLE GATT read.
        capture_reader: Callable returning the keyboard's last key press as
            ``(press_counter, position, active_layer_mask)`` (the mask may be
            ``None`` on older firmware; two-element tuples are tolerated) or
            ``None`` when the firmware lacks the capture service; tests
            inject a fake. ``None`` selects the real BLE GATT read.
        trackball_writer: Callable taking the trackball config payload and
            returning True when the keyboard applied it live, False when the
            firmware lacks the config service; tests inject a fake. ``None``
            selects the real BLE GATT write.
        capture_watch: Live BLE subscription handles for the capture stream
            (device, characteristic, event token); ``None`` while no watch is
            active.
        capture_watch_starter: Callable returning whether a capture watch
            could be started (pushing presses into ``events``); tests inject
            a fake. ``None`` selects the real BLE GATT subscription.
        ble_address: Cached Bluetooth MAC of the paired keyboard, resolved from
            the Windows registry on first battery read.
        firmware_job: Status of the single in-flight firmware build/flash job
            (``phase``, ``detail``, ``error``, ``uf2_files``); empty when idle.
        pending_macros: Macros staged in the sequence composer, compiled into the
            next generated firmware; persisted to the macros file across restarts.
        battery_alert: Low-battery blink settings compiled into the next
            generated firmware; persisted to the battery-alert file.
        power: Idle / deep-sleep settings compiled into the next generated
            firmware; persisted to the power settings file.
        locking: Studio-locking setting compiled into the next generated
            firmware; persisted to the locking settings file.
        trackballs: Trackball behavior settings compiled into the next
            generated firmware; persisted to the trackballs settings file.
    """

    params: Parameters
    paths: SimpleNamespace
    logger: Any
    client: Optional[StudioClient] = None
    fake_device: Optional[FakeImprint] = None
    device_info: Optional[DeviceInfo] = None
    lock_state: Optional[LockState] = None
    unsaved_changes: bool = False
    port_name: Optional[str] = None
    ui_clients: int = 0
    ever_had_ui_client: bool = False
    last_ui_disconnect_monotonic: float = field(default_factory=time.monotonic)
    last_backup_path: Optional[Path] = None
    request_shutdown: Optional[Callable[[], None]] = None
    events: "Queue[ConnectionEvent]" = field(default_factory=Queue)
    process_runner: Optional[Callable[..., Tuple[int, str, str]]] = None
    drive_probe: Optional[Callable[[], List[Path]]] = None
    battery_reader: Optional[Callable[[], List[int]]] = None
    capture_reader: Optional[Callable[[], Optional[Tuple[Any, ...]]]] = None
    trackball_writer: Optional[Callable[[bytes], bool]] = None
    capture_watch: Optional[Any] = None
    capture_watch_starter: Optional[Callable[[], bool]] = None
    ble_address: Optional[str] = None
    firmware_job: Dict[str, Any] = field(default_factory=dict)
    pending_macros: List[MacroDefinition] = field(default_factory=list)
    battery_alert: BatteryAlertConfig = field(default_factory=BatteryAlertConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
    locking: LockingConfig = field(default_factory=LockingConfig)
    trackballs: TrackballConfig = field(default_factory=TrackballConfig)
