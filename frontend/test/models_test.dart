import 'package:flutter_test/flutter_test.dart';
import 'package:keymapper/keycodes.dart';
import 'package:keymapper/models.dart';

void main() {
  test('StateSnapshot parses the backend shape', () {
    final snap = StateSnapshot.fromJson({
      'type': 'state',
      'connected': true,
      'fake': true,
      'port': 'FAKE',
      'device': {'name': 'Imprint', 'serial_number': 'aa'},
      'lock_state': 'LOCKED',
      'unsaved_changes': false,
      'last_backup': null,
    });
    expect(snap.connected, isTrue);
    expect(snap.locked, isTrue);
    expect(snap.deviceName, 'Imprint');
  });

  test('KeymapModel parses layers and bindings', () {
    final keymap = KeymapModel.fromJson({
      'layers': [
        {
          'layer_id': 0,
          'name': 'Base',
          'bindings': [
            {'behavior_id': 1, 'param1': 0x070004, 'param2': 0},
          ],
        },
      ],
      'available_layers': 27,
      'max_layer_name_length': 20,
    });
    expect(keymap.layers.single.name, 'Base');
    expect(keymap.layers.single.bindings.single.param1, 0x070004);
  });

  test('keycode catalog contains the essentials with correct usages', () {
    final a = keycodeCatalog.firstWhere((k) => k.name == 'A');
    expect(a.param, 0x070004);
    final kp0 = keycodeCatalog.firstWhere((k) => k.name == 'KP_N0');
    expect(kp0.param, 0x070062);
    final lalt = keycodeCatalog.firstWhere((k) => k.name == 'LALT');
    expect(lalt.param, 0x0700E2);
  });

  test('describeKeycodeParam decodes names and modifier wrappers', () {
    expect(describeKeycodeParam(0x070004), 'A');
    expect(describeKeycodeParam(0x02070004), 'LS(A)');
    expect(describeKeycodeParam(0x0C00E9), 'C_VOL_UP');
    expect(describeKeycodeParam(0x099999), '0x99999');
  });

  test('MacroModel round-trips through JSON', () {
    final macro = MacroModel(
      nodeName: 'e_grave',
      displayName: 'è (Alt+0232)',
      steps: [
        MacroStepModel(kind: 'press', binding: '&kp LALT'),
        MacroStepModel(kind: 'tap', binding: '&kp KP_N0'),
        MacroStepModel(kind: 'release', binding: '&kp LALT'),
      ],
    );
    final restored = MacroModel.fromJson(macro.toJson());
    expect(restored.nodeName, 'e_grave');
    expect(restored.steps.length, 3);
    expect(restored.steps[1].binding, '&kp KP_N0');
    expect(restored.waitMs, 30);
  });

  test('FirmwareStatus tolerates empty job', () {
    final status = FirmwareStatus.fromJson({
      'job': {},
      'github': {'available': true, 'authenticated': false, 'login': null},
      'bootloader_drive': null,
      'staged_macros': 2,
    });
    expect(status.jobPhase, isNull);
    expect(status.githubAvailable, isTrue);
    expect(status.stagedMacros, 2);
  });
}
