/// Data models mirroring the KeyMapper backend API JSON.
library;

/// Connection/lock/unsaved snapshot pushed over the WebSocket and `/api/state`.
class StateSnapshot {
  const StateSnapshot({
    required this.connected,
    required this.fake,
    required this.port,
    required this.deviceName,
    required this.lockState,
    required this.unsavedChanges,
    required this.lastBackup,
  });

  final bool connected;
  final bool fake;
  final String? port;
  final String? deviceName;
  final String? lockState;
  final bool unsavedChanges;
  final String? lastBackup;

  bool get locked => lockState == 'LOCKED';

  factory StateSnapshot.fromJson(Map<String, dynamic> json) => StateSnapshot(
        connected: json['connected'] == true,
        fake: json['fake'] == true,
        port: json['port'] as String?,
        deviceName:
            (json['device'] as Map<String, dynamic>?)?['name'] as String?,
        lockState: json['lock_state'] as String?,
        unsavedChanges: json['unsaved_changes'] == true,
        lastBackup: json['last_backup'] as String?,
      );

  static const StateSnapshot disconnected = StateSnapshot(
    connected: false,
    fake: false,
    port: null,
    deviceName: null,
    lockState: null,
    unsavedChanges: false,
    lastBackup: null,
  );
}

/// One key binding: behavior reference plus two parameters.
class BindingModel {
  const BindingModel(
      {required this.behaviorId, required this.param1, required this.param2});

  final int behaviorId;
  final int param1;
  final int param2;

  factory BindingModel.fromJson(Map<String, dynamic> json) => BindingModel(
        behaviorId: json['behavior_id'] as int,
        param1: (json['param1'] ?? 0) as int,
        param2: (json['param2'] ?? 0) as int,
      );
}

/// One keymap layer.
class LayerModel {
  const LayerModel(
      {required this.layerId, required this.name, required this.bindings});

  final int layerId;
  final String name;
  final List<BindingModel> bindings;

  factory LayerModel.fromJson(Map<String, dynamic> json) => LayerModel(
        layerId: json['layer_id'] as int,
        name: json['name'] as String,
        bindings: (json['bindings'] as List<dynamic>)
            .map((b) => BindingModel.fromJson(b as Map<String, dynamic>))
            .toList(),
      );
}

/// The full keymap.
class KeymapModel {
  const KeymapModel({
    required this.layers,
    required this.availableLayers,
    required this.maxLayerNameLength,
  });

  final List<LayerModel> layers;
  final int availableLayers;
  final int maxLayerNameLength;

  factory KeymapModel.fromJson(Map<String, dynamic> json) => KeymapModel(
        layers: (json['layers'] as List<dynamic>)
            .map((l) => LayerModel.fromJson(l as Map<String, dynamic>))
            .toList(),
        availableLayers: json['available_layers'] as int,
        maxLayerNameLength: json['max_layer_name_length'] as int,
      );
}

/// Geometry of one physical key in centi-keyunits (100 = one key width).
class PhysicalKeyModel {
  const PhysicalKeyModel({
    required this.width,
    required this.height,
    required this.x,
    required this.y,
    required this.r,
    required this.rx,
    required this.ry,
  });

  final int width;
  final int height;
  final int x;
  final int y;
  final int r;
  final int rx;
  final int ry;

  factory PhysicalKeyModel.fromJson(Map<String, dynamic> json) =>
      PhysicalKeyModel(
        width: json['width'] as int,
        height: json['height'] as int,
        x: json['x'] as int,
        y: json['y'] as int,
        r: (json['r'] ?? 0) as int,
        rx: (json['rx'] ?? 0) as int,
        ry: (json['ry'] ?? 0) as int,
      );
}

/// One selectable physical layout variant.
class PhysicalLayoutModel {
  const PhysicalLayoutModel({required this.name, required this.keys});

  final String name;
  final List<PhysicalKeyModel> keys;

  factory PhysicalLayoutModel.fromJson(Map<String, dynamic> json) =>
      PhysicalLayoutModel(
        name: json['name'] as String,
        keys: (json['keys'] as List<dynamic>)
            .map((k) => PhysicalKeyModel.fromJson(k as Map<String, dynamic>))
            .toList(),
      );
}

/// All layouts plus the active one.
class PhysicalLayoutsModel {
  const PhysicalLayoutsModel(
      {required this.activeIndex, required this.layouts});

  final int activeIndex;
  final List<PhysicalLayoutModel> layouts;

  PhysicalLayoutModel? get active =>
      activeIndex >= 0 && activeIndex < layouts.length
          ? layouts[activeIndex]
          : null;

  factory PhysicalLayoutsModel.fromJson(Map<String, dynamic> json) =>
      PhysicalLayoutsModel(
        activeIndex: json['active_index'] as int,
        layouts: (json['layouts'] as List<dynamic>)
            .map((l) => PhysicalLayoutModel.fromJson(l as Map<String, dynamic>))
            .toList(),
      );
}

/// Domain of one behavior parameter.
class ParamDomainModel {
  const ParamDomainModel({
    required this.name,
    required this.kind,
    this.constant,
    this.rangeMin,
    this.rangeMax,
    this.keyboardMax,
    this.consumerMax,
  });

  final String name;
  final String kind;
  final int? constant;
  final int? rangeMin;
  final int? rangeMax;
  final int? keyboardMax;
  final int? consumerMax;

  factory ParamDomainModel.fromJson(Map<String, dynamic> json) =>
      ParamDomainModel(
        name: json['name'] as String,
        kind: json['kind'] as String,
        constant: json['constant'] as int?,
        rangeMin: json['range_min'] as int?,
        rangeMax: json['range_max'] as int?,
        keyboardMax: json['keyboard_max'] as int?,
        consumerMax: json['consumer_max'] as int?,
      );
}

/// One behavior with its parameter metadata.
class BehaviorModel {
  const BehaviorModel({
    required this.behaviorId,
    required this.displayName,
    required this.paramSets,
  });

  final int behaviorId;
  final String displayName;

  /// Each entry pairs the domains accepted for param1 and param2.
  final List<(List<ParamDomainModel>, List<ParamDomainModel>)> paramSets;

  /// Domains of the first parameter (first set), empty when parameterless.
  List<ParamDomainModel> get param1Domains =>
      paramSets.isEmpty ? const [] : paramSets.first.$1;

  /// Domains of the second parameter (first set), empty when absent.
  List<ParamDomainModel> get param2Domains =>
      paramSets.isEmpty ? const [] : paramSets.first.$2;

  /// All first-parameter domains merged across every parameter set (a behavior
  /// like Underglow advertises one constant per command across its sets).
  List<ParamDomainModel> get allParam1Domains =>
      [for (final set in paramSets) ...set.$1];

  /// All second-parameter domains merged across every parameter set.
  List<ParamDomainModel> get allParam2Domains =>
      [for (final set in paramSets) ...set.$2];

  /// Name of the constant matching [value] among a parameter's domains, or
  /// null when no constant domain carries that value.
  static String? constantName(List<ParamDomainModel> domains, int value) {
    for (final d in domains) {
      if (d.kind == 'constant' && d.constant == value) return d.name;
    }
    return null;
  }

  factory BehaviorModel.fromJson(Map<String, dynamic> json) => BehaviorModel(
        behaviorId: json['behavior_id'] as int,
        displayName: json['display_name'] as String,
        paramSets: (json['param_sets'] as List<dynamic>)
            .map((s) => (
                  ((s as Map<String, dynamic>)['param1'] as List<dynamic>)
                      .map((d) =>
                          ParamDomainModel.fromJson(d as Map<String, dynamic>))
                      .toList(),
                  (s['param2'] as List<dynamic>)
                      .map((d) =>
                          ParamDomainModel.fromJson(d as Map<String, dynamic>))
                      .toList(),
                ))
            .toList(),
      );
}

/// One step of a macro sequence.
class MacroStepModel {
  MacroStepModel({required this.kind, this.binding = '', this.value = 0});

  String kind;
  String binding;
  int value;

  factory MacroStepModel.fromJson(Map<String, dynamic> json) => MacroStepModel(
        kind: json['kind'] as String,
        binding: (json['binding'] ?? '') as String,
        value: (json['value'] ?? 0) as int,
      );

  Map<String, dynamic> toJson() =>
      {'kind': kind, 'binding': binding, 'value': value};
}

/// One macro to be compiled into the firmware. A non-empty [shiftedSteps]
/// makes it a Shift pair: [steps] run normally, [shiftedSteps] run with Shift
/// held (the physical Shift is masked while the shifted variant types).
class MacroModel {
  MacroModel({
    required this.nodeName,
    required this.displayName,
    required this.steps,
    this.waitMs = 30,
    this.tapMs = 30,
    List<MacroStepModel>? shiftedSteps,
  }) : shiftedSteps = shiftedSteps ?? [];

  String nodeName;
  String displayName;
  List<MacroStepModel> steps;
  int waitMs;
  int tapMs;
  List<MacroStepModel> shiftedSteps;

  factory MacroModel.fromJson(Map<String, dynamic> json) => MacroModel(
        nodeName: json['node_name'] as String,
        displayName: json['display_name'] as String,
        steps: (json['steps'] as List<dynamic>)
            .map((s) => MacroStepModel.fromJson(s as Map<String, dynamic>))
            .toList(),
        waitMs: (json['wait_ms'] ?? 30) as int,
        tapMs: (json['tap_ms'] ?? 30) as int,
        shiftedSteps: ((json['shifted_steps'] as List<dynamic>?) ?? const [])
            .map((s) => MacroStepModel.fromJson(s as Map<String, dynamic>))
            .toList(),
      );

  Map<String, dynamic> toJson() => {
        'node_name': nodeName,
        'display_name': displayName,
        'steps': steps.map((s) => s.toJson()).toList(),
        'wait_ms': waitMs,
        'tap_ms': tapMs,
        'shifted_steps': shiftedSteps.map((s) => s.toJson()).toList(),
      };
}

/// One entry of the backups list.
class BackupEntry {
  const BackupEntry(
      {required this.name, required this.createdUtc, required this.sizeBytes});

  final String name;
  final String createdUtc;
  final int sizeBytes;

  factory BackupEntry.fromJson(Map<String, dynamic> json) => BackupEntry(
        name: json['name'] as String,
        createdUtc: json['created_utc'] as String,
        sizeBytes: json['size_bytes'] as int,
      );
}

/// Low-battery underglow blink settings (compiled into generated firmware).
class BatteryAlertModel {
  BatteryAlertModel({
    required this.enabled,
    required this.thresholdPercent,
    required this.blinkCount,
    required this.hue,
    required this.saturation,
    required this.brightness,
    required this.intervalMinutes,
    this.battcheckMs = 2000,
  });

  bool enabled;
  int thresholdPercent;
  int blinkCount;
  int hue;
  int saturation;
  int brightness;
  int intervalMinutes;
  int battcheckMs;

  factory BatteryAlertModel.fromJson(Map<String, dynamic> json) =>
      BatteryAlertModel(
        enabled: json['enabled'] == true,
        thresholdPercent: (json['threshold_percent'] ?? 10) as int,
        blinkCount: (json['blink_count'] ?? 3) as int,
        hue: (json['hue'] ?? 359) as int,
        saturation: (json['saturation'] ?? 90) as int,
        brightness: (json['brightness'] ?? 50) as int,
        intervalMinutes: (json['interval_minutes'] ?? 2) as int,
        battcheckMs: (json['battcheck_ms'] ?? 2000) as int,
      );

  Map<String, dynamic> toJson() => {
        'enabled': enabled,
        'threshold_percent': thresholdPercent,
        'blink_count': blinkCount,
        'hue': hue,
        'saturation': saturation,
        'brightness': brightness,
        'interval_minutes': intervalMinutes,
        'battcheck_ms': battcheckMs,
      };
}

/// Idle / deep-sleep power settings compiled into generated firmware.
class PowerModel {
  PowerModel({
    required this.idleSeconds,
    required this.deepSleepEnabled,
    required this.deepSleepMinutes,
    required this.rgbOffWhenIdle,
    required this.rgbOffWhenUnplugged,
  });

  int idleSeconds;
  bool deepSleepEnabled;
  int deepSleepMinutes;
  bool rgbOffWhenIdle;
  bool rgbOffWhenUnplugged;

  factory PowerModel.fromJson(Map<String, dynamic> json) => PowerModel(
        idleSeconds: (json['idle_seconds'] ?? 30) as int,
        deepSleepEnabled: json['deep_sleep_enabled'] != false,
        deepSleepMinutes: (json['deep_sleep_minutes'] ?? 15) as int,
        rgbOffWhenIdle: json['rgb_off_when_idle'] != false,
        rgbOffWhenUnplugged: json['rgb_off_when_unplugged'] == true,
      );

  Map<String, dynamic> toJson() => {
        'idle_seconds': idleSeconds,
        'deep_sleep_enabled': deepSleepEnabled,
        'deep_sleep_minutes': deepSleepMinutes,
        'rgb_off_when_idle': rgbOffWhenIdle,
        'rgb_off_when_unplugged': rgbOffWhenUnplugged,
      };
}

/// One trackball's behavior, compiled into generated firmware.
class TrackballSideModel {
  TrackballSideModel({
    required this.installed,
    required this.mode,
    required this.speedPercent,
    required this.naturalDirection,
  });

  bool installed;

  /// One of: mouse, scroll_vertical, scroll_horizontal, disabled.
  String mode;
  int speedPercent;
  bool naturalDirection;

  factory TrackballSideModel.fromJson(Map<String, dynamic> json) =>
      TrackballSideModel(
        installed: json['installed'] != false,
        mode: (json['mode'] ?? 'mouse') as String,
        speedPercent: (json['speed_percent'] ?? 100) as int,
        naturalDirection: json['natural_direction'] == true,
      );

  Map<String, dynamic> toJson() => {
        'installed': installed,
        'mode': mode,
        'speed_percent': speedPercent,
        'natural_direction': naturalDirection,
      };
}

/// Both trackballs plus the shared sensor responsiveness.
class TrackballModel {
  TrackballModel({
    required this.left,
    required this.right,
    required this.responsivenessMs,
    this.wakeCheckMs,
    this.awakeAfterMotionMs,
    this.forceAwake = false,
  });

  TrackballSideModel left;
  TrackballSideModel right;
  int responsivenessMs;

  /// Motion-check interval while a sensor rests (null = stock tiers).
  int? wakeCheckMs;

  /// Full-speed time after the last motion before resting (null = stock).
  int? awakeAfterMotionMs;

  /// Never rest at all — the "infinite stay awake" (keyboard idle/deep
  /// sleep still applies).
  bool forceAwake;

  /// The side model for 'left' or 'right'.
  TrackballSideModel side(String name) => name == 'left' ? left : right;

  factory TrackballModel.fromJson(Map<String, dynamic> json) => TrackballModel(
        left: TrackballSideModel.fromJson(
            (json['left'] ?? const <String, dynamic>{}) as Map<String, dynamic>),
        right: TrackballSideModel.fromJson(
            (json['right'] ?? const <String, dynamic>{}) as Map<String, dynamic>),
        responsivenessMs: (json['responsiveness_ms'] ?? 8) as int,
        wakeCheckMs: json['wake_check_ms'] as int?,
        awakeAfterMotionMs: json['awake_after_motion_ms'] as int?,
        forceAwake: json['force_awake'] == true,
      );

  Map<String, dynamic> toJson() => {
        'left': left.toJson(),
        'right': right.toJson(),
        'responsiveness_ms': responsivenessMs,
        'wake_check_ms': wakeCheckMs,
        'awake_after_motion_ms': awakeAfterMotionMs,
        'force_awake': forceAwake,
      };
}

/// Studio-locking setting compiled into generated firmware.
class LockingModel {
  LockingModel({required this.studioLockingEnabled});

  bool studioLockingEnabled;

  factory LockingModel.fromJson(Map<String, dynamic> json) =>
      LockingModel(studioLockingEnabled: json['studio_locking_enabled'] == true);

  Map<String, dynamic> toJson() =>
      {'studio_locking_enabled': studioLockingEnabled};
}

/// Firmware wizard status (job, GitHub auth, bootloader drive).
class FirmwareStatus {
  const FirmwareStatus({
    required this.jobPhase,
    required this.jobDetail,
    required this.jobError,
    required this.uf2Files,
    required this.githubAvailable,
    required this.githubAuthenticated,
    required this.githubLogin,
    required this.bootloaderDrive,
    required this.stagedMacros,
  });

  final String? jobPhase;
  final String? jobDetail;
  final String? jobError;
  final List<String> uf2Files;
  final bool githubAvailable;
  final bool githubAuthenticated;
  final String? githubLogin;
  final String? bootloaderDrive;
  final int stagedMacros;

  factory FirmwareStatus.fromJson(Map<String, dynamic> json) {
    final job = Map<String, dynamic>.from((json['job'] as Map?) ?? const {});
    final github =
        Map<String, dynamic>.from((json['github'] as Map?) ?? const {});
    return FirmwareStatus(
      jobPhase: job['phase'] as String?,
      jobDetail: job['detail'] as String?,
      jobError: job['error'] as String?,
      uf2Files: ((job['uf2_files'] as List<dynamic>?) ?? const [])
          .map((e) => e as String)
          .toList(),
      githubAvailable: github['available'] == true,
      githubAuthenticated: github['authenticated'] == true,
      githubLogin: github['login'] as String?,
      bootloaderDrive: json['bootloader_drive'] as String?,
      stagedMacros: (json['staged_macros'] ?? 0) as int,
    );
  }
}
