/// Application-wide state: connection snapshot, keymap, catalogs, selection.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'keycodes.dart';
import 'models.dart';

/// One recorded binding change (element of an undo/redo group).
class BindingEdit {
  const BindingEdit({
    required this.layerId,
    required this.position,
    required this.before,
    required this.after,
  });

  final int layerId;
  final int position;
  final BindingModel before;
  final BindingModel after;
}

/// Single source of truth for the UI; refreshed by WebSocket pushes and API calls.
class AppModel extends ChangeNotifier {
  AppModel(this.api);

  final ApiClient api;

  StateSnapshot snapshot = StateSnapshot.disconnected;
  KeymapModel? keymap;
  PhysicalLayoutsModel? layouts;
  List<BehaviorModel> behaviors = const [];
  int selectedLayerIndex = 0;
  int? selectedKeyPosition;
  String? lastError;
  bool loadingKeymap = false;

  StreamSubscription<Map<String, dynamic>>? _wsSubscription;
  Timer? _wsRetry;
  bool _wasLocked = true;

  /// Behavior lookup by wire ID.
  BehaviorModel? behaviorById(int id) {
    for (final b in behaviors) {
      if (b.behaviorId == id) return b;
    }
    return null;
  }

  /// Coarse category of a binding, used to color key-cap labels.
  ///
  /// Returns one of: ``letter``, ``number``, ``fkey``, ``keypad``, ``key``,
  /// ``mouse``, ``layer``, ``sequence``, ``blank``, ``other``.
  String labelKindFor(BindingModel binding) {
    final behavior = behaviorById(binding.behaviorId);
    if (behavior == null) return 'other';
    final name = behavior.displayName;
    if (name == 'Transparent' || name == 'None') return 'blank';
    if (name.startsWith('Mouse')) return 'mouse';
    final domains = behavior.param1Domains;
    if (domains.isNotEmpty && domains.first.kind == 'layer_id') return 'layer';
    if (domains.isNotEmpty && domains.first.kind == 'hid_usage') {
      final page = (binding.param1 >> 16) & 0xFF;
      final usage = binding.param1 & 0xFFFF;
      if (page != 0x07) return 'key';
      if (usage >= 0x04 && usage <= 0x1D) return 'letter';
      if (usage >= 0x1E && usage <= 0x27) return 'number';
      if ((usage >= 0x3A && usage <= 0x45) || (usage >= 0x68 && usage <= 0x73)) {
        return 'fkey';
      }
      if (usage >= 0x53 && usage <= 0x63) return 'keypad';
      return 'key';
    }
    if (behavior.paramSets.isEmpty ||
        (behavior.param1Domains.isEmpty && behavior.param2Domains.isEmpty)) {
      // Parameterless non-stock names are typically compiled-in sequences —
      // except the stock set and KeyMapper's own firmware behaviors.
      const stockParamless = {
        'Caps Word', 'Key Repeat', 'Reset', 'Bootloader', 'Studio Unlock',
        'Soft Off', 'Grave Escape',
        'RGB Save/Restore', 'NumLock Guard', 'BattCheck',
      };
      return stockParamless.contains(name) ? 'other' : 'sequence';
    }
    return 'other';
  }

  /// Short human-readable label for a binding (used on key caps and lists).
  String labelFor(BindingModel binding) {
    final behavior = behaviorById(binding.behaviorId);
    if (behavior == null) return '#${binding.behaviorId}';
    final name = behavior.displayName;
    switch (name) {
      case 'Transparent':
        return '▽';
      case 'None':
        return '∅';
      case 'Key Press':
        return describeKeycodeParam(binding.param1);
    }
    final domains = behavior.param1Domains;
    if (domains.isEmpty) return name;
    if (domains.first.kind == 'layer_id') {
      final shortName = name
          .replaceAll('Momentary Layer', 'MO')
          .replaceAll('To Layer', 'TO')
          .replaceAll('Toggle Layer', 'TOG')
          .replaceAll('Sticky Layer', 'SL');
      // The wire value is the stable layer ID; show the layer's POSITION so
      // key caps agree with the layer rail and the editor dropdowns.
      final position =
          keymap?.layers.indexWhere((l) => l.layerId == binding.param1) ?? -1;
      return '$shortName ${position >= 0 ? position : binding.param1}';
    }
    if (domains.first.kind == 'hid_usage') {
      return '$name ${describeKeycodeParam(binding.param1)}';
    }
    final constant =
        BehaviorModel.constantName(behavior.allParam1Domains, binding.param1);
    if (constant != null) {
      final shortBehavior = name
          .replaceAll('Underglow', 'UG')
          .replaceAll('RGB UG', 'UG')
          .replaceAll('Bluetooth', 'BT')
          .replaceAll('Output Selection', 'OUT')
          .replaceAll('Outputs', 'OUT')
          .replaceAll('Backlight', 'BL');
      final shortCommand = constant
          .replaceAll('Brightness', 'Brt')
          .replaceAll('Saturation', 'Sat')
          .replaceAll('Toggle On/Off', 'Toggle')
          .replaceAll(' Up', ' +')
          .replaceAll(' Down', ' −');
      return '$shortBehavior $shortCommand';
    }
    return '$name ${binding.param1}';
  }

  /// Starts the WebSocket subscription (auto-reconnects while the page lives).
  void start() {
    _subscribe();
    refreshState();
  }

  /// Called with the key position and the active-layer bitmask (null on
  /// older firmware) for every press streamed live from the keyboard while
  /// a capture watch is on (see [ApiClient.captureWatch]).
  void Function(int position, int? layersMask)? onCapturePress;

  /// Firmware-wizard progress for this app session. Lives on the model so a
  /// tab switch (which disposes the wizard screen) cannot lose it, and so an
  /// in-flight automatic finish keeps running and reporting when the user
  /// returns to the tab.
  bool wizardRightFlashed = false;
  bool wizardLeftFlashed = false;
  bool wizardWaitingReconnect = false;
  bool wizardFinalizing = false;
  bool wizardFinished = false;
  String? wizardFinishError;

  /// Applies [change] to the wizard fields and notifies listeners, so every
  /// mounted screen re-renders regardless of which one drove the change.
  void updateWizard(void Function() change) {
    change();
    notifyListeners();
  }

  void _subscribe() {
    _wsSubscription?.cancel();
    try {
      _wsSubscription = api.stateStream().listen(
        (message) {
          if (message['event'] == 'capture_press') {
            final position = message['position'];
            final layers = message['layers'];
            if (position is int) {
              onCapturePress?.call(position, layers is int ? layers : null);
            }
            return;
          }
          _onSnapshot(StateSnapshot.fromJson(message));
        },
        onError: (_) => _scheduleRetry(),
        onDone: _scheduleRetry,
      );
    } catch (_) {
      _scheduleRetry();
    }
  }

  void _scheduleRetry() {
    _wsRetry?.cancel();
    _wsRetry = Timer(const Duration(seconds: 3), _subscribe);
  }

  void _onSnapshot(StateSnapshot next) {
    final unlockedNow = _wasLocked && !next.locked && next.connected;
    snapshot = next;
    _wasLocked = next.locked;
    notifyListeners();
    if (unlockedNow) {
      // The keyboard just got unlocked: everything becomes readable.
      loadEverything();
    }
  }

  /// Fetches the current state snapshot once (start-up and manual refresh).
  Future<void> refreshState() async {
    try {
      snapshot = await api.getState();
      _wasLocked = snapshot.locked;
      notifyListeners();
      if (snapshot.connected && !snapshot.locked) {
        await loadEverything();
      }
    } on Object catch (e) {
      lastError = e.toString();
      notifyListeners();
    }
  }

  /// Loads keymap, layouts, and behavior catalog (requires unlocked keyboard).
  Future<void> loadEverything() async {
    loadingKeymap = true;
    notifyListeners();
    try {
      keymap = await api.getKeymap();
      layouts = await api.getLayouts();
      behaviors = await api.getBehaviors();
      // Behavior pickers list entries alphabetically, whatever the firmware's
      // internal order — newly compiled sequences slot in alphabetically too.
      behaviors.sort((a, b) =>
          a.displayName.toLowerCase().compareTo(b.displayName.toLowerCase()));
      if (selectedLayerIndex >= (keymap?.layers.length ?? 0)) {
        selectedLayerIndex = 0;
      }
      lastError = null;
    } on ApiException catch (e) {
      lastError = e.isLocked ? null : e.detail;
    } on Object catch (e) {
      lastError = e.toString();
    } finally {
      loadingKeymap = false;
      notifyListeners();
    }
  }

  /// Reloads only the keymap (after an edit).
  Future<void> reloadKeymap() async {
    try {
      keymap = await api.getKeymap();
      lastError = null;
    } on Object catch (e) {
      lastError = e.toString();
    }
    notifyListeners();
  }

  void selectLayer(int index) {
    selectedLayerIndex = index;
    selectedKeyPosition = null;
    notifyListeners();
  }

  void selectKey(int? position) {
    selectedKeyPosition = position;
    notifyListeners();
  }

  final List<List<BindingEdit>> _undoStack = [];
  final List<List<BindingEdit>> _redoStack = [];

  bool get canUndo => _undoStack.isNotEmpty;
  bool get canRedo => _redoStack.isNotEmpty;

  /// Forgets session edit history (called when the server-side state jumped:
  /// discard, layout switch, reconnect).
  void clearEditHistory() {
    _undoStack.clear();
    _redoStack.clear();
    notifyListeners();
  }

  BindingModel? _currentBinding(int layerId, int position) {
    final layer = keymap?.layers.where((l) => l.layerId == layerId).firstOrNull;
    if (layer == null || position >= layer.bindings.length) return null;
    return layer.bindings[position];
  }

  int? _captureCounter;
  bool _captureSupported = false;

  /// Whether the flashed firmware exposes the capture channel (last checked
  /// by [rebaselineCapture]).
  bool get captureSupported => _captureSupported;

  /// Reads the keyboard's press counter once so [pollKeyboardPress] only
  /// reports presses made AFTER a capture mode was armed, and refreshes
  /// [captureSupported]. Safe to call while disconnected.
  Future<bool> rebaselineCapture() async {
    try {
      final reading = await api.getCapturePress();
      _captureSupported = reading != null;
      if (reading != null) _captureCounter = reading.$1;
    } on Object {
      _captureSupported = false;
    }
    return _captureSupported;
  }

  /// Polls the keyboard's capture channel once; returns the position of the
  /// key most recently pressed ON THE KEYBOARD plus the active-layer bitmask
  /// at press time (null on older firmware) when a new press happened since
  /// the previous poll, and null otherwise (no press, unsupported firmware,
  /// or Bluetooth hiccup).
  Future<(int, int?)?> pollKeyboardPress() async {
    try {
      final reading = await api.getCapturePress();
      if (reading == null) return null;
      final (counter, position, layersMask) = reading;
      final previous = _captureCounter;
      _captureCounter = counter;
      if (previous == null || counter == previous) return null;
      return (position, layersMask);
    } on Object {
      return null;
    }
  }

  /// Binding at a key position of the layer at [layerIndex] (rail order),
  /// or null when out of range.
  BindingModel? bindingAtPosition(int layerIndex, int position) {
    final layers = keymap?.layers;
    if (layers == null || layerIndex < 0 || layerIndex >= layers.length) {
      return null;
    }
    final bindings = layers[layerIndex].bindings;
    if (position < 0 || position >= bindings.length) return null;
    return bindings[position];
  }

  /// Resolves the binding a key press at [position] triggers given the
  /// keyboard's active-layer bitmask (bits by layer id), replicating ZMK's
  /// rule: highest active layer wins, Transparent falls through, and the
  /// base layer (index 0) is always active. This makes capture assign
  /// exactly what the key DOES at press time — e.g. after a sequence
  /// switched the keyboard to layer 1, the same physical key captures its
  /// layer-1 binding, not the layer shown in the editor.
  BindingModel? resolveActiveBinding(int position, int layersMask) {
    final layers = keymap?.layers;
    if (layers == null || layers.isEmpty) return null;
    for (var i = layers.length - 1; i >= 0; i--) {
      final active = i == 0 || (layersMask & (1 << layers[i].layerId)) != 0;
      if (!active) continue;
      final bindings = layers[i].bindings;
      if (position < 0 || position >= bindings.length) continue;
      final binding = bindings[position];
      final name = behaviorById(binding.behaviorId)?.displayName ?? '';
      if (name.toLowerCase() == 'transparent') continue;
      return binding;
    }
    return null;
  }

  /// Applies one binding, recording it for undo, and refreshes the keymap.
  Future<void> setBinding(
      int layerId, int position, int behaviorId, int param1, int param2) async {
    final before = _currentBinding(layerId, position);
    await api.setBinding(layerId, position, behaviorId, param1, param2);
    if (before != null) {
      _undoStack.add([
        BindingEdit(
            layerId: layerId,
            position: position,
            before: before,
            after: BindingModel(
                behaviorId: behaviorId, param1: param1, param2: param2)),
      ]);
      _redoStack.clear();
    }
    await reloadKeymap();
  }

  /// Applies one binding to all keys of a layer (one undo group) and refreshes.
  Future<int> bulkSet(int layerId, int behaviorId, int param1, int param2) async {
    final layer = keymap?.layers.where((l) => l.layerId == layerId).firstOrNull;
    final after = BindingModel(behaviorId: behaviorId, param1: param1, param2: param2);
    final group = <BindingEdit>[
      if (layer != null)
        for (int i = 0; i < layer.bindings.length; i++)
          BindingEdit(
              layerId: layerId,
              position: i,
              before: layer.bindings[i],
              after: after),
    ];
    final count = await api.bulkSet(layerId, behaviorId, param1, param2);
    if (group.isNotEmpty) {
      _undoStack.add(group);
      _redoStack.clear();
    }
    await reloadKeymap();
    return count;
  }

  // ---- Multi-selection, clipboard, and cluster paste ---------------------- //

  bool multiSelect = false;
  final Set<int> multiSelected = {};
  bool pasteArmed = false;
  List<(double, double, BindingModel)>? _clipboard;

  int get clipboardSize => _clipboard?.length ?? 0;

  /// Turns multi-selection mode on/off (clearing selection and paste mode).
  void setMultiSelect(bool on) {
    multiSelect = on;
    multiSelected.clear();
    pasteArmed = false;
    notifyListeners();
  }

  /// Toggles one key's membership in the multi-selection.
  void toggleSelected(int position) {
    if (!multiSelected.remove(position)) multiSelected.add(position);
    notifyListeners();
  }

  /// Arms/disarms paste mode (next key tap becomes the paste anchor).
  void armPaste(bool on) {
    pasteArmed = on && clipboardSize > 0;
    notifyListeners();
  }

  static (double, double) _keyCenter(PhysicalKeyModel key) =>
      (key.x + key.width / 2.0, key.y + key.height / 2.0);

  /// Copies the selected keys' bindings with their geometry, anchored at the
  /// top-left-most selected key. Returns how many keys were copied.
  int copySelection(PhysicalLayoutModel layout, LayerModel layer) {
    if (multiSelected.isEmpty) return 0;
    final positions = multiSelected
        .where((p) => p < layout.keys.length && p < layer.bindings.length)
        .toList();
    if (positions.isEmpty) return 0;
    positions.sort((a, b) {
      final ca = _keyCenter(layout.keys[a]);
      final cb = _keyCenter(layout.keys[b]);
      final byY = ca.$2.compareTo(cb.$2);
      return byY != 0 ? byY : ca.$1.compareTo(cb.$1);
    });
    final anchor = _keyCenter(layout.keys[positions.first]);
    _clipboard = [
      for (final p in positions)
        (
          _keyCenter(layout.keys[p]).$1 - anchor.$1,
          _keyCenter(layout.keys[p]).$2 - anchor.$2,
          layer.bindings[p],
        ),
    ];
    pasteArmed = false;
    notifyListeners();
    return _clipboard!.length;
  }

  /// Applies several bindings on one layer as a single undoable group.
  Future<int> applyEdits(int layerId, Map<int, BindingModel> targets) async {
    final layer = keymap?.layers.where((l) => l.layerId == layerId).firstOrNull;
    if (layer == null || targets.isEmpty) return 0;
    final group = <BindingEdit>[
      for (final entry in targets.entries)
        if (entry.key < layer.bindings.length)
          BindingEdit(
              layerId: layerId,
              position: entry.key,
              before: layer.bindings[entry.key],
              after: entry.value),
    ];
    for (final edit in group) {
      await api.setBinding(edit.layerId, edit.position, edit.after.behaviorId,
          edit.after.param1, edit.after.param2);
    }
    if (group.isNotEmpty) {
      _undoStack.add(group);
      _redoStack.clear();
    }
    await reloadKeymap();
    return group.length;
  }

  /// Pastes the clipboard onto a geometrically identical cluster anchored at
  /// the tapped key. Every copied key must land on a real key (35 centi-unit
  /// tolerance) or nothing is applied.
  Future<int> pasteAt(
      int anchorPosition, PhysicalLayoutModel layout, int layerId) async {
    final clipboard = _clipboard;
    if (clipboard == null || anchorPosition >= layout.keys.length) return 0;
    final anchor = _keyCenter(layout.keys[anchorPosition]);
    const tolerance = 35.0;
    final targets = <int, BindingModel>{};
    for (final (dx, dy, binding) in clipboard) {
      final tx = anchor.$1 + dx;
      final ty = anchor.$2 + dy;
      int? best;
      double bestDist = tolerance;
      for (int i = 0; i < layout.keys.length; i++) {
        final c = _keyCenter(layout.keys[i]);
        final dist = (c.$1 - tx).abs() + (c.$2 - ty).abs();
        if (dist < bestDist) {
          bestDist = dist;
          best = i;
        }
      }
      if (best == null) {
        throw StateError(
            'The copied cluster does not fit here: no key at offset '
            '(${dx.toStringAsFixed(0)}, ${dy.toStringAsFixed(0)}) from the '
            'anchor. Pick an anchor whose surrounding keys match the copied '
            'shape (anchor = top-left key of the copied cluster).');
      }
      targets[best] = binding;
    }
    pasteArmed = false;
    return applyEdits(layerId, targets);
  }

  /// Behavior wire ID by display name, or null when absent from the catalog.
  int? behaviorIdByName(String name) {
    for (final b in behaviors) {
      if (b.displayName == name) return b.behaviorId;
    }
    return null;
  }

  /// Reverts the most recent edit group (stays pending until saved).
  Future<void> undo() async {
    if (_undoStack.isEmpty) return;
    final group = _undoStack.removeLast();
    for (final edit in group.reversed) {
      await api.setBinding(edit.layerId, edit.position, edit.before.behaviorId,
          edit.before.param1, edit.before.param2);
    }
    _redoStack.add(group);
    await reloadKeymap();
  }

  /// Re-applies the most recently undone edit group.
  Future<void> redo() async {
    if (_redoStack.isEmpty) return;
    final group = _redoStack.removeLast();
    for (final edit in group) {
      await api.setBinding(edit.layerId, edit.position, edit.after.behaviorId,
          edit.after.param1, edit.after.param2);
    }
    _undoStack.add(group);
    await reloadKeymap();
  }

  @override
  void dispose() {
    _wsSubscription?.cancel();
    _wsRetry?.cancel();
    super.dispose();
  }
}
