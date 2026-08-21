/// Metadata-driven binding editor: behavior picker plus parameter editors.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show KeyDownEvent;

import '../app_state.dart';
import '../keycodes.dart';
import '../models.dart';

/// The outcome of an edit session.
class BindingChoice {
  const BindingChoice(this.behaviorId, this.param1, this.param2);

  final int behaviorId;
  final int param1;
  final int param2;
}

/// Opens the binding editor dialog; resolves to the chosen binding or null.
Future<BindingChoice?> showBindingEditor(
  BuildContext context,
  AppModel model, {
  BindingModel? current,
  String title = 'Edit binding',
}) {
  return showDialog<BindingChoice>(
    context: context,
    builder: (context) => Dialog(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480, maxHeight: 640),
        child: _BindingEditor(model: model, current: current, title: title),
      ),
    ),
  );
}

class _BindingEditor extends StatefulWidget {
  const _BindingEditor({required this.model, this.current, required this.title});

  final AppModel model;
  final BindingModel? current;
  final String title;

  @override
  State<_BindingEditor> createState() => _BindingEditorState();
}

class _BindingEditorState extends State<_BindingEditor> {
  late int _behaviorId;
  int _param1 = 0;
  int _param2 = 0;
  final Set<int> _mods = {};
  String _keycodeFilter = '';
  bool _capturing = false;
  String _keycodeCategory = 'All';
  final FocusNode _captureFocus = FocusNode();
  Timer? _cyboardPoll;
  Timer? _pendingBrowserKey;
  bool _pollBusy = false;

  @override
  void dispose() {
    _cyboardPoll?.cancel();
    _pendingBrowserKey?.cancel();
    _captureFocus.dispose();
    super.dispose();
  }

  /// Arms or disarms key capture. While armed, the dialog listens to this
  /// computer's keyboard AND to the keyboard itself (capture channel over
  /// Bluetooth): pressing one of ITS keys adopts that key's full binding —
  /// sequence, layer switch, underglow, anything — not just a keycode.
  void _setCapturing(bool on) {
    setState(() => _capturing = on);
    _cyboardPoll?.cancel();
    _cyboardPoll = null;
    _pendingBrowserKey?.cancel();
    _pendingBrowserKey = null;
    if (!on) return;
    _captureFocus.requestFocus();
    widget.model.rebaselineCapture().then((supported) {
      if (!mounted || !_capturing || !supported) return;
      _cyboardPoll?.cancel();
      _cyboardPoll = Timer.periodic(
        const Duration(milliseconds: 350),
        (_) => _pollCyboard(),
      );
    });
  }

  /// One capture poll tick: when a key was pressed on the keyboard itself,
  /// adopt its complete binding from the layer selected in the editor.
  Future<void> _pollCyboard() async {
    if (_pollBusy) return;
    _pollBusy = true;
    try {
      final press = await widget.model.pollKeyboardPress();
      if (!mounted || !_capturing || press == null) return;
      final (position, layersMask) = press;
      final layers = widget.model.keymap?.layers ?? const <LayerModel>[];
      if (layers.isEmpty) return;
      final layerIndex =
          widget.model.selectedLayerIndex.clamp(0, layers.length - 1);
      final source = layersMask != null
          ? widget.model.resolveActiveBinding(position, layersMask)
          : widget.model.bindingAtPosition(layerIndex, position);
      if (source == null) return;
      _pendingBrowserKey?.cancel();
      _pendingBrowserKey = null;
      setState(() {
        _capturing = false;
        _behaviorId = source.behaviorId;
        _param1 = source.param1;
        _param2 = source.param2;
        _mods.clear();
        for (final entry in modifierWrappers.entries) {
          if (((_param1 >> 24) & entry.key) != 0) _mods.add(entry.key);
        }
      });
      _cyboardPoll?.cancel();
      _cyboardPoll = null;
    } finally {
      _pollBusy = false;
    }
  }

  @override
  void initState() {
    super.initState();
    final current = widget.current;
    _behaviorId = current?.behaviorId ??
        (widget.model.behaviors.isEmpty
            ? 0
            : widget.model.behaviors.first.behaviorId);
    _param1 = current?.param1 ?? 0;
    _param2 = current?.param2 ?? 0;
    for (final entry in modifierWrappers.entries) {
      if (((_param1 >> 24) & entry.key) != 0) _mods.add(entry.key);
    }
  }

  BehaviorModel? get _behavior => widget.model.behaviorById(_behaviorId);

  int _composeKeycodeParam(int base) {
    int mods = 0;
    for (final bit in _mods) {
      mods |= bit;
    }
    return (mods << 24) | (base & 0xFFFFFF);
  }

  /// Editor for one parameter slot given ALL its domains (merged across the
  /// behavior's parameter sets). Behaviors like Underglow or Bluetooth expose
  /// one named constant per command — those render as a command dropdown.
  Widget _paramSlot(List<ParamDomainModel> domains, int value,
      ValueChanged<int> onChanged, String slotLabel) {
    final constants =
        domains.where((d) => d.kind == 'constant' && d.constant != null).toList();
    if (constants.isNotEmpty) {
      final values = {for (final c in constants) c.constant!: c.name};
      if (!values.containsKey(value)) {
        // Commit the displayed default: without this, picking a behavior and
        // pressing Apply sent the stale 0 the dropdown never wrote back, and
        // the keyboard rejected it (INVALID_PARAMETERS).
        WidgetsBinding.instance.addPostFrameCallback(
            (_) => onChanged(constants.first.constant!));
      }
      return DropdownButtonFormField<int>(
        initialValue: values.containsKey(value) ? value : constants.first.constant,
        decoration: InputDecoration(labelText: '$slotLabel: command'),
        items: [
          for (final entry in values.entries)
            DropdownMenuItem(value: entry.key, child: Text(entry.value)),
        ],
        onChanged: (v) => onChanged(v ?? 0),
      );
    }
    if (domains.isEmpty) return const SizedBox.shrink();
    return _paramEditor(domains.first, value, onChanged, slotLabel);
  }

  Widget _paramEditor(ParamDomainModel domain, int value,
      ValueChanged<int> onChanged, String slotLabel) {
    switch (domain.kind) {
      case 'layer_id':
        // The dropdown shows layers by their POSITION in the current order
        // (matching the layer rail); the wire value stays the stable layer ID,
        // which is what the keyboard expects and what survives reordering.
        final layers = widget.model.keymap?.layers ?? const <LayerModel>[];
        return DropdownButtonFormField<int>(
          initialValue: layers.any((l) => l.layerId == value)
              ? value
              : (layers.isEmpty ? null : layers.first.layerId),
          decoration: InputDecoration(labelText: '$slotLabel: layer'),
          items: [
            for (int i = 0; i < layers.length; i++)
              DropdownMenuItem(
                  value: layers[i].layerId,
                  child: Text('$i: ${layers[i].name}')),
          ],
          onChanged: (v) => onChanged(v ?? 0),
        );
      case 'hid_usage':
        final filtered = keycodeCatalog
            .where((k) =>
                (_keycodeCategory == 'All' || k.category == _keycodeCategory) &&
                (_keycodeFilter.isEmpty ||
                    k.name.toLowerCase().contains(_keycodeFilter) ||
                    k.label.toLowerCase().contains(_keycodeFilter)))
            .toList();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('$slotLabel: key — current ${describeKeycodeParam(value)}'),
            const SizedBox(height: 4),
            if (!_capturing)
              OutlinedButton.icon(
                onPressed: () => _setCapturing(true),
                icon: const Icon(Icons.keyboard_alt_outlined),
                label: const Text('Press a key…'),
              )
            else
              KeyboardListener(
                focusNode: _captureFocus,
                autofocus: true,
                onKeyEvent: (event) {
                  if (event is! KeyDownEvent) return;
                  final usage = event.physicalKey.usbHidUsage;
                  // Only ordinary keyboard-page keys can be mirrored from the
                  // computer's keyboard; anything else stays picker-only.
                  if ((usage >> 16) != 0x07) return;
                  if (_cyboardPoll == null) {
                    setState(() {
                      _capturing = false;
                      onChanged(_composeKeycodeParam(usage & 0xFFFFFF));
                    });
                    return;
                  }
                  // The keyboard's capture channel is live: this browser event
                  // may be the typed OUTPUT of a key pressed on the keyboard
                  // itself, so wait one beat — if a position press arrives,
                  // the full binding wins over the raw keycode.
                  void resolvePending() {
                    if (_pollBusy) {
                      // A capture read is still in flight; it wins if it
                      // reports a position, so wait for it to finish.
                      _pendingBrowserKey = Timer(
                          const Duration(milliseconds: 300), resolvePending);
                      return;
                    }
                    _pendingBrowserKey = null;
                    if (!mounted || !_capturing) return;
                    _setCapturing(false);
                    setState(
                        () => onChanged(_composeKeycodeParam(usage & 0xFFFFFF)));
                  }
                  _pendingBrowserKey?.cancel();
                  _pendingBrowserKey = Timer(
                      const Duration(milliseconds: 500), resolvePending);
                },
                child: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.tertiaryContainer,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Row(children: [
                    const Icon(Icons.hearing),
                    const SizedBox(width: 8),
                    const Expanded(
                        child: Text('Listening — press the key you want on '
                            'this computer\'s keyboard, or on the keyboard '
                            'itself to copy that key\'s full binding')),
                    TextButton(
                      onPressed: () => _setCapturing(false),
                      child: const Text('Cancel'),
                    ),
                  ]),
                ),
              ),
            Wrap(
              spacing: 4,
              children: [
                for (final entry in modifierWrappers.entries)
                  FilterChip(
                    label: Text(entry.value),
                    selected: _mods.contains(entry.key),
                    onSelected: (on) => setState(() {
                      on ? _mods.add(entry.key) : _mods.remove(entry.key);
                      onChanged(_composeKeycodeParam(value));
                    }),
                  ),
              ],
            ),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: [
                for (final cat in keycodeCategories)
                  Padding(
                    padding: const EdgeInsets.only(right: 4),
                    child: ChoiceChip(
                      label: Text(cat),
                      selected: _keycodeCategory == cat,
                      onSelected: (_) =>
                          setState(() => _keycodeCategory = cat),
                      visualDensity: VisualDensity.compact,
                    ),
                  ),
              ]),
            ),
            TextField(
              decoration: const InputDecoration(
                  labelText: 'Search keys', prefixIcon: Icon(Icons.search)),
              onChanged: (v) =>
                  setState(() => _keycodeFilter = v.trim().toLowerCase()),
            ),
            SizedBox(
              height: 180,
              child: ListView.builder(
                itemCount: filtered.length,
                itemBuilder: (context, i) {
                  final code = filtered[i];
                  final selected = (value & 0xFFFFFF) == code.param;
                  return ListTile(
                    dense: true,
                    selected: selected,
                    title: Text(code.name),
                    subtitle: Text(code.label),
                    onTap: () => onChanged(_composeKeycodeParam(code.param)),
                  );
                },
              ),
            ),
          ],
        );
      case 'range':
        return TextFormField(
          initialValue: '$value',
          decoration: InputDecoration(
            labelText:
                '$slotLabel: ${domain.name} (${domain.rangeMin}–${domain.rangeMax})',
          ),
          keyboardType: TextInputType.number,
          onChanged: (v) => onChanged(int.tryParse(v) ?? value),
        );
      case 'constant':
        return ListTile(
            dense: true,
            title: Text('$slotLabel: ${domain.name}'),
            trailing: Text('${domain.constant}'));
      default:
        return const SizedBox.shrink();
    }
  }

  @override
  Widget build(BuildContext context) {
    final behavior = _behavior;
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(widget.title, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          DropdownButtonFormField<int>(
            initialValue: _behaviorId,
            decoration: const InputDecoration(labelText: 'Behavior'),
            items: [
              // Hide the internal halves of Shift pairs: users assign the
              // pair itself. A key already bound to a half stays selectable.
              for (final b in widget.model.behaviors)
                if (b.behaviorId == _behaviorId ||
                    (!b.displayName.endsWith(' (plain)') &&
                        !b.displayName.endsWith(' (shift)')))
                  DropdownMenuItem(
                      value: b.behaviorId, child: Text(b.displayName)),
            ],
            onChanged: (v) => setState(() {
              _behaviorId = v ?? _behaviorId;
              _param1 = 0;
              _param2 = 0;
              _mods.clear();
            }),
          ),
          const SizedBox(height: 8),
          if (behavior != null)
            Flexible(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _paramSlot(behavior.allParam1Domains, _param1,
                        (v) => setState(() => _param1 = v), 'Parameter 1'),
                    const SizedBox(height: 8),
                    _paramSlot(behavior.allParam2Domains, _param2,
                        (v) => setState(() => _param2 = v), 'Parameter 2'),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Cancel')),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: () => Navigator.pop(
                    context, BindingChoice(_behaviorId, _param1, _param2)),
                child: const Text('Apply'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
