/// Live keymap editor: layer rail, board view, bulk operations, save/discard.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show KeyDownEvent;

import '../app_state.dart';
import '../models.dart';
import '../widgets/binding_editor.dart';
import '../widgets/board_view.dart';
import '../widgets/hsb_picker.dart';
import '../widgets/trackball_settings.dart';

/// Full-featured replacement for the Studio keymap page.
class EditorScreen extends StatefulWidget {
  const EditorScreen({super.key, required this.model});

  final AppModel model;

  @override
  State<EditorScreen> createState() => _EditorScreenState();
}

class _EditorScreenState extends State<EditorScreen> {
  AppModel get model => widget.model;

  bool _superFast = false;
  int? _hovered;
  final FocusNode _fastFocus = FocusNode();
  Timer? _cyboardPoll;
  Timer? _pendingBrowserKey;
  bool _pollBusy = false;
  List<String> _installedTrackballs = const [];

  @override
  void initState() {
    super.initState();
    _loadTrackballs();
  }

  /// Refreshes which trackball ball buttons the board shows (sides marked
  /// installed in the trackball settings).
  Future<void> _loadTrackballs() async {
    try {
      final trackballs = await model.api.getTrackballs();
      if (mounted) {
        setState(() => _installedTrackballs = [
              if (trackballs.left.installed) 'left',
              if (trackballs.right.installed) 'right',
            ]);
      }
    } on Object {
      if (mounted) setState(() => _installedTrackballs = const []);
    }
  }

  @override
  void dispose() {
    _cyboardPoll?.cancel();
    _pendingBrowserKey?.cancel();
    model.onCapturePress = null;
    model.api.captureWatch(false).catchError((Object _) => (false, null));
    _fastFocus.dispose();
    super.dispose();
  }

  /// Turns Super Fast Assign on or off. While on, the app also listens to
  /// the KEYBOARD ITSELF: preferably as a live push stream — every press
  /// arrives within tens of milliseconds, so it pairs with the key hovered
  /// when it was actually pressed — falling back to polling on firmware
  /// without notify support. A press assigns that key's full binding —
  /// sequences, layer switches, underglow, anything — to the hovered key.
  void _setSuperFast(bool on) {
    setState(() {
      _superFast = on;
      if (on) {
        model.setMultiSelect(false);
        _fastFocus.requestFocus();
      }
    });
    _cyboardPoll?.cancel();
    _cyboardPoll = null;
    _pendingBrowserKey?.cancel();
    _pendingBrowserKey = null;
    model.onCapturePress = null;
    if (!on) {
      model.api.captureWatch(false).catchError((Object _) => (false, null));
      return;
    }
    model.api.captureWatch(true).then(((bool, String?) result) {
      if (!mounted || !_superFast) return;
      final (supported, _) = result;
      if (supported) {
        model.onCapturePress = (position, layersMask) {
          if (mounted && _superFast) _assignFromCyboard(position, layersMask);
        };
        return;
      }
      _startCapturePolling();
    }).catchError((Object _) {
      if (mounted && _superFast) _startCapturePolling();
    });
  }

  /// Fallback for firmware without capture notifications: poll the capture
  /// channel. Pairing is looser here — the press matches the key hovered
  /// when the poll lands, so keep hovering until the assign appears.
  void _startCapturePolling() {
    model.rebaselineCapture().then((supported) {
      if (!mounted || !_superFast || !supported) return;
      _cyboardPoll?.cancel();
      _cyboardPoll = Timer.periodic(
        const Duration(milliseconds: 350),
        (_) => _pollCyboard(),
      );
    });
  }

  /// Assigns the binding the pressed key carries ON THE KEYBOARD RIGHT NOW
  /// (resolved via the press's active-layer bitmask, falling back to the
  /// editor-selected layer on older firmware) to the hovered key, overriding
  /// any in-flight plain-keycode assign from the browser (the keyboard knows
  /// the real binding; the browser only sees its typed output).
  Future<void> _assignFromCyboard(int position, int? layersMask) async {
    if (!mounted || !_superFast) return;
    // A dialog on top (clicking a key still opens the full editor) owns
    // key capture; don't assign behind it.
    if (!(ModalRoute.of(context)?.isCurrent ?? true)) return;
    final hovered = _hovered;
    final layers = model.keymap?.layers ?? const <LayerModel>[];
    if (layers.isEmpty) return;
    final layerIndex = model.selectedLayerIndex.clamp(0, layers.length - 1);
    final layer = layers[layerIndex];
    final source = layersMask != null
        ? model.resolveActiveBinding(position, layersMask)
        : model.bindingAtPosition(layerIndex, position);
    if (hovered == null || hovered == position || source == null) {
      return;
    }
    _pendingBrowserKey?.cancel();
    _pendingBrowserKey = null;
    await _guard(context, () async {
      await model.setBinding(
        layer.layerId,
        hovered,
        source.behaviorId,
        source.param1,
        source.param2,
      );
    });
  }

  /// One capture poll tick (fallback path): when a key was pressed on the
  /// keyboard itself, assign its binding to the hovered key.
  Future<void> _pollCyboard() async {
    if (_pollBusy) return;
    _pollBusy = true;
    try {
      final press = await model.pollKeyboardPress();
      if (!mounted || !_superFast || press == null) return;
      final (position, layersMask) = press;
      await _assignFromCyboard(position, layersMask);
    } finally {
      _pollBusy = false;
    }
  }

  /// Assigns a plain key press captured from this computer's keyboard to the
  /// currently hovered key (Super Fast Assign mode). When the Cyboard capture
  /// channel is live, the assign is briefly debounced so a press made on the
  /// keyboard itself (whose typed output the browser also sees) resolves to
  /// its full binding instead of a raw keycode.
  Future<void> _fastAssign(int usbHidUsage) async {
    final hovered = _hovered;
    final layers = model.keymap?.layers ?? const <LayerModel>[];
    if (layers.isEmpty) return;
    final layerIndex = model.selectedLayerIndex.clamp(0, layers.length - 1);
    final layer = layers[layerIndex];
    final kpId = model.behaviorIdByName('Key Press');
    if (hovered == null || kpId == null) return;
    if ((usbHidUsage >> 16) != 0x07) return;
    Future<void> apply() => _guard(context, () async {
          await model.setBinding(
            layer.layerId,
            hovered,
            kpId,
            usbHidUsage & 0xFFFFFF,
            0,
          );
        });
    final cyboardListening =
        _cyboardPoll != null || model.onCapturePress != null;
    if (!cyboardListening) {
      await apply();
      return;
    }
    void resolvePending() {
      // A capture read may still be in flight when the debounce expires; if
      // it reports a position it wins, so wait for it before falling back to
      // the raw keycode.
      if (_pollBusy) {
        _pendingBrowserKey =
            Timer(const Duration(milliseconds: 300), resolvePending);
        return;
      }
      _pendingBrowserKey = null;
      if (mounted && _superFast) apply();
    }
    _pendingBrowserKey?.cancel();
    _pendingBrowserKey =
        Timer(const Duration(milliseconds: 500), resolvePending);
  }

  /// Label color per binding category so key types read at a glance:
  /// letters keep the default ink; numbers blue, F-keys purple, keypad cyan,
  /// layer keys orange, mouse pink, sequences green, disabled keys dimmed.
  static Color? _labelColor(String kind, ThemeData theme) {
    final dark = theme.brightness == Brightness.dark;
    switch (kind) {
      case 'letter':
        return null; // default onSurface — the visual baseline
      case 'number':
        return dark ? Colors.lightBlue.shade300 : Colors.blue.shade800;
      case 'fkey':
        return dark ? Colors.purple.shade200 : Colors.purple.shade700;
      case 'keypad':
        return dark ? Colors.cyan.shade200 : Colors.cyan.shade800;
      case 'layer':
        return dark ? Colors.orange.shade300 : Colors.orange.shade900;
      case 'mouse':
        return dark ? Colors.pink.shade200 : Colors.pink.shade700;
      case 'sequence':
        return dark ? Colors.lightGreen.shade300 : Colors.green.shade800;
      case 'blank':
        return theme.colorScheme.outline;
      default:
        return dark ? Colors.teal.shade200 : Colors.teal.shade800;
    }
  }

  Future<void> _guard(
    BuildContext context,
    Future<void> Function() action,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _bulkSet(BuildContext context, LayerModel layer) async {
    final choice = await showBindingEditor(
      context,
      model,
      title: 'Set ALL keys of "${layer.name}" to…',
    );
    if (choice == null || !context.mounted) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Overwrite every key?'),
        content: Text(
          'Every key of layer "${layer.name}" will get this binding. '
          'This stays pending until you Save.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Overwrite'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    await _guard(context, () async {
      final count = await model.bulkSet(
        layer.layerId,
        choice.behaviorId,
        choice.param1,
        choice.param2,
      );
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$count keys updated')));
      }
    });
  }

  /// Sets every SELECTED key of [layer] to the named parameterless behavior
  /// (one undoable group).
  Future<void> _selectionBulk(
    BuildContext context,
    LayerModel layer,
    String behaviorName,
  ) async {
    final behaviorId = model.behaviorIdByName(behaviorName);
    if (behaviorId == null) return;
    await _guard(context, () async {
      final targets = {
        for (final p in model.multiSelected)
          p: BindingModel(behaviorId: behaviorId, param1: 0, param2: 0),
      };
      final n = await model.applyEdits(layer.layerId, targets);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('$n key(s) set to $behaviorName')),
        );
      }
    });
  }

  Future<void> _quickBulk(
    BuildContext context,
    LayerModel layer,
    String behaviorName,
  ) async {
    final behavior = model.behaviors
        .where((b) => b.displayName == behaviorName)
        .toList();
    if (behavior.isEmpty) return;
    await _guard(
      context,
      () async => model.bulkSet(layer.layerId, behavior.first.behaviorId, 0, 0),
    );
  }

  @override
  Widget build(BuildContext context) {
    final snap = model.snapshot;
    if (!snap.connected) {
      return const Center(child: Text('Connect the keyboard first (Home).'));
    }
    if (snap.locked) {
      return const Center(
        child: Text('Keyboard is locked — see the Advanced tab for the '
            'guided unlock (physical A+F hold).'),
      );
    }
    final keymap = model.keymap;
    final layouts = model.layouts;
    if (keymap == null || layouts == null) {
      if (!model.loadingKeymap) model.loadEverything();
      return const Center(child: CircularProgressIndicator());
    }
    if (keymap.layers.isEmpty) {
      return const Center(child: Text('No layers reported by the keyboard.'));
    }
    final layerIndex = model.selectedLayerIndex.clamp(
      0,
      keymap.layers.length - 1,
    );
    final layer = keymap.layers[layerIndex];
    final layout = layouts.active;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(
          width: 230,
          child: Card(
            margin: const EdgeInsets.all(8),
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        'Layers',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      IconButton(
                        tooltip:
                            'Enable one more layer (${keymap.availableLayers} left)',
                        onPressed: keymap.availableLayers > 0
                            ? () => _guard(context, () async {
                                await model.api.addLayer();
                                await model.reloadKeymap();
                              })
                            : null,
                        icon: const Icon(Icons.add),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: ListView.builder(
                    itemCount: keymap.layers.length,
                    itemBuilder: (context, i) {
                      final l = keymap.layers[i];
                      return ListTile(
                        dense: true,
                        selected: i == layerIndex,
                        selectedTileColor: Theme.of(
                          context,
                        ).colorScheme.primaryContainer,
                        title: Text(
                          '$i: ${l.name.isEmpty ? "(unnamed)" : l.name}',
                          style: i == layerIndex
                              ? const TextStyle(fontWeight: FontWeight.bold)
                              : null,
                        ),
                        onTap: () => model.selectLayer(i),
                        trailing: PopupMenuButton<String>(
                          onSelected: (action) async {
                            switch (action) {
                              case 'rename':
                                final controller = TextEditingController(
                                  text: l.name,
                                );
                                final name = await showDialog<String>(
                                  context: context,
                                  builder: (context) => AlertDialog(
                                    title: const Text('Rename layer'),
                                    content: TextField(
                                      controller: controller,
                                      maxLength: keymap.maxLayerNameLength,
                                    ),
                                    actions: [
                                      TextButton(
                                        onPressed: () => Navigator.pop(context),
                                        child: const Text('Cancel'),
                                      ),
                                      FilledButton(
                                        onPressed: () => Navigator.pop(
                                          context,
                                          controller.text,
                                        ),
                                        child: const Text('Rename'),
                                      ),
                                    ],
                                  ),
                                );
                                if (name != null &&
                                    name.isNotEmpty &&
                                    context.mounted) {
                                  await _guard(context, () async {
                                    await model.api.renameLayer(
                                      l.layerId,
                                      name,
                                    );
                                    await model.reloadKeymap();
                                  });
                                }
                              case 'up' when i > 0:
                                await _guard(context, () async {
                                  await model.api.moveLayer(i, i - 1);
                                  await model.reloadKeymap();
                                });
                              case 'down' when i < keymap.layers.length - 1:
                                await _guard(context, () async {
                                  await model.api.moveLayer(i, i + 1);
                                  await model.reloadKeymap();
                                });
                              case 'remove':
                                await _guard(context, () async {
                                  await model.api.removeLayer(i);
                                  await model.reloadKeymap();
                                });
                              case 'color':
                                final picked = await showHsbPicker(
                                  context,
                                  title:
                                      'Underglow color for layer $i'
                                      '${l.name.isEmpty ? "" : " (${l.name})"}',
                                );
                                if (picked != null && context.mounted) {
                                  await _guard(context, () async {
                                    final n = await model.api.setLayerColor(
                                        i, picked.$1, picked.$2, picked.$3);
                                    if (context.mounted) {
                                      ScaffoldMessenger.of(context)
                                          .showSnackBar(SnackBar(
                                              content: Text(n > 0
                                                  ? '$n sequence color(s) '
                                                      'updated — rebuild + '
                                                      'flash to apply'
                                                  : 'No color sequence targets '
                                                      'this layer yet — add a '
                                                      '"To/Hold layer + color" '
                                                      'preset in Sequences '
                                                      'first')));
                                    }
                                  });
                                }
                            }
                          },
                          itemBuilder: (context) => const [
                            PopupMenuItem(
                              value: 'rename',
                              child: Text('Rename'),
                            ),
                            PopupMenuItem(
                              value: 'color',
                              child: Text('Assign color'),
                            ),
                            PopupMenuItem(value: 'up', child: Text('Move up')),
                            PopupMenuItem(
                              value: 'down',
                              child: Text('Move down'),
                            ),
                            PopupMenuItem(
                              value: 'remove',
                              child: Text('Disable layer'),
                            ),
                          ],
                        ),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
        Expanded(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // Row 1: layer identity + edit history | save controls.
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            'Layer $layerIndex'
                            '${layer.name.isEmpty ? "" : " — ${layer.name}"}',
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                        ),
                        IconButton(
                          tooltip: 'Undo last change',
                          onPressed: model.canUndo
                              ? () => _guard(context, model.undo)
                              : null,
                          icon: const Icon(Icons.undo),
                        ),
                        IconButton(
                          tooltip: 'Redo',
                          onPressed: model.canRedo
                              ? () => _guard(context, model.redo)
                              : null,
                          icon: const Icon(Icons.redo),
                        ),
                        const Spacer(),
                        if (snap.unsavedChanges) ...[
                          const Chip(
                            avatar: Icon(
                              Icons.circle,
                              color: Colors.orange,
                              size: 12,
                            ),
                            label: Text('unsaved changes'),
                          ),
                          const SizedBox(width: 8),
                        ],
                        FilledButton.icon(
                          onPressed: snap.unsavedChanges
                              ? () => _guard(context, () async {
                                  final backup = await model.api.save();
                                  await model.refreshState();
                                  if (context.mounted) {
                                    ScaffoldMessenger.of(context).showSnackBar(
                                      SnackBar(
                                        content: Text(
                                          'Saved to keyboard (backup $backup)',
                                        ),
                                      ),
                                    );
                                  }
                                })
                              : null,
                          icon: const Icon(Icons.save),
                          label: const Text('Save'),
                        ),
                        const SizedBox(width: 8),
                        OutlinedButton.icon(
                          onPressed: snap.unsavedChanges
                              ? () => _guard(context, () async {
                                  await model.api.discard();
                                  model.clearEditHistory();
                                  await model.reloadKeymap();
                                  await model.refreshState();
                                })
                              : null,
                          icon: const Icon(Icons.delete_sweep_outlined),
                          label: const Text('Discard'),
                        ),
                      ],
                    ),
                    const Divider(height: 16),
                    // Row 2: whole-layer operations and mode toggles.
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        OutlinedButton.icon(
                          onPressed: () => _bulkSet(context, layer),
                          icon: const Icon(Icons.select_all),
                          label: const Text('Set all to…'),
                        ),
                        OutlinedButton(
                          onPressed: () =>
                              _quickBulk(context, layer, 'Transparent'),
                          child: const Text('All TRANSPARENT'),
                        ),
                        OutlinedButton(
                          onPressed: () => _quickBulk(context, layer, 'None'),
                          child: const Text('All NONE'),
                        ),
                        InkWell(
                          borderRadius: BorderRadius.circular(8),
                          onTap: () => setState(() {
                            model.setMultiSelect(!model.multiSelect);
                            if (model.multiSelect) _superFast = false;
                          }),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Checkbox(
                                value: model.multiSelect,
                                onChanged: (v) {
                                  setState(() {
                                    model.setMultiSelect(v ?? false);
                                  });
                                  if (model.multiSelect && _superFast) {
                                    _setSuperFast(false);
                                  }
                                },
                              ),
                              const Text('Multiple selection'),
                              const SizedBox(width: 4),
                            ],
                          ),
                        ),
                        InkWell(
                          borderRadius: BorderRadius.circular(8),
                          onTap: () => _setSuperFast(!_superFast),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Checkbox(
                                value: _superFast,
                                onChanged: (v) => _setSuperFast(v ?? false),
                              ),
                              const Text('Super Fast Assign'),
                              const SizedBox(width: 4),
                            ],
                          ),
                        ),
                      ],
                    ),
                    // Row 3: contextual actions for the active mode.
                    if (_superFast)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Align(
                          alignment: Alignment.centerLeft,
                          child: Chip(
                            avatar: const Icon(Icons.bolt, size: 16),
                            label: Text(
                              _hovered == null
                                  ? 'Hover a key, then press a key on this '
                                        'computer\'s keyboard — or on the '
                                        'keyboard itself to copy its full '
                                        'binding (sequences, layers, anything)'
                                  : 'Key ${_hovered! + 1} armed — press a key '
                                        'to assign (click for the full dialog)',
                            ),
                          ),
                        ),
                      ),
                    if (model.multiSelect)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            Text('${model.multiSelected.length} selected'),
                            OutlinedButton.icon(
                              onPressed:
                                  model.multiSelected.isEmpty || layout == null
                                  ? null
                                  : () {
                                      final n = model.copySelection(
                                        layout,
                                        layer,
                                      );
                                      ScaffoldMessenger.of(
                                        context,
                                      ).showSnackBar(
                                        SnackBar(
                                          content: Text('Copied $n key(s)'),
                                        ),
                                      );
                                    },
                              icon: const Icon(Icons.copy, size: 16),
                              label: const Text('Copy'),
                            ),
                            OutlinedButton.icon(
                              onPressed:
                                  model.multiSelected.isEmpty || layout == null
                                  ? null
                                  : () => _guard(context, () async {
                                      model.copySelection(layout, layer);
                                      await _selectionBulk(
                                        context,
                                        layer,
                                        'None',
                                      );
                                    }),
                              icon: const Icon(Icons.cut, size: 16),
                              label: const Text('Cut'),
                            ),
                            FilledButton.tonalIcon(
                              onPressed: model.clipboardSize == 0
                                  ? null
                                  : () => model.armPaste(!model.pasteArmed),
                              icon: const Icon(Icons.paste, size: 16),
                              label: Text(
                                model.pasteArmed
                                    ? 'Click the anchor key…'
                                    : 'Paste (${model.clipboardSize})',
                              ),
                            ),
                            OutlinedButton.icon(
                              onPressed: model.multiSelected.isEmpty
                                  ? null
                                  : () async {
                                      final choice = await showBindingEditor(
                                        context,
                                        model,
                                        title:
                                            'Assign ${model.multiSelected.length} '
                                            'selected key(s) to…',
                                      );
                                      if (choice == null || !context.mounted) {
                                        return;
                                      }
                                      await _guard(context, () async {
                                        final targets = {
                                          for (final p in model.multiSelected)
                                            p: BindingModel(
                                              behaviorId: choice.behaviorId,
                                              param1: choice.param1,
                                              param2: choice.param2,
                                            ),
                                        };
                                        final n = await model.applyEdits(
                                          layer.layerId,
                                          targets,
                                        );
                                        if (context.mounted) {
                                          ScaffoldMessenger.of(
                                            context,
                                          ).showSnackBar(
                                            SnackBar(
                                              content: Text(
                                                '$n key(s) assigned',
                                              ),
                                            ),
                                          );
                                        }
                                      });
                                    },
                              icon: const Icon(Icons.edit_note, size: 16),
                              label: const Text('Assign Selection to…'),
                            ),
                            OutlinedButton(
                              onPressed: model.multiSelected.isEmpty
                                  ? null
                                  : () =>
                                        _selectionBulk(context, layer, 'None'),
                              child: const Text('Assign Selection to NONE'),
                            ),
                            OutlinedButton(
                              onPressed: model.multiSelected.isEmpty
                                  ? null
                                  : () => _selectionBulk(
                                      context,
                                      layer,
                                      'Transparent',
                                    ),
                              child: const Text(
                                'Assign Selection to TRANSPARENT',
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: layout == null
                    ? const Center(child: Text('No active physical layout'))
                    : Padding(
                        padding: const EdgeInsets.all(12),
                        child: KeyboardListener(
                          focusNode: _fastFocus,
                          onKeyEvent: (event) {
                            if (_superFast && event is KeyDownEvent) {
                              _fastAssign(event.physicalKey.usbHidUsage);
                            }
                          },
                          child: Center(
                            child: BoardView(
                              layout: layout,
                              labels: [
                                for (final b in layer.bindings)
                                  model.labelFor(b),
                              ],
                              labelColors: [
                                for (final b in layer.bindings)
                                  _labelColor(
                                    model.labelKindFor(b),
                                    Theme.of(context),
                                  ),
                              ],
                              selectedPosition: model.selectedKeyPosition,
                              highlightPositions: model.multiSelect
                                  ? model.multiSelected
                                  : _superFast && _hovered != null
                                  ? {_hovered!}
                                  : const {},
                              onKeyHover: !_superFast
                                  ? null
                                  : (position) {
                                      setState(() => _hovered = position);
                                      if (position != null &&
                                          !_fastFocus.hasFocus) {
                                        _fastFocus.requestFocus();
                                      }
                                    },
                              trackballs: _installedTrackballs,
                              onTrackballTap: (side) async {
                                await showTrackballDialog(
                                    context, model, side);
                                await _loadTrackballs();
                              },
                              onKeyTap: (position) async {
                                if (model.multiSelect) {
                                  if (model.pasteArmed) {
                                    await _guard(context, () async {
                                      final n = await model.pasteAt(
                                        position,
                                        layout,
                                        layer.layerId,
                                      );
                                      if (context.mounted) {
                                        ScaffoldMessenger.of(
                                          context,
                                        ).showSnackBar(
                                          SnackBar(
                                            content: Text('Pasted $n key(s)'),
                                          ),
                                        );
                                      }
                                    });
                                  } else {
                                    model.toggleSelected(position);
                                  }
                                  return;
                                }
                                model.selectKey(position);
                                final choice = await showBindingEditor(
                                  context,
                                  model,
                                  current: position < layer.bindings.length
                                      ? layer.bindings[position]
                                      : null,
                                  title:
                                      'Key ${position + 1} on "${layer.name}"',
                                );
                                if (choice != null && context.mounted) {
                                  await _guard(context, () async {
                                    await model.setBinding(
                                      layer.layerId,
                                      position,
                                      choice.behaviorId,
                                      choice.param1,
                                      choice.param2,
                                    );
                                  });
                                }
                              },
                            ),
                          ),
                        ),
                      ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
