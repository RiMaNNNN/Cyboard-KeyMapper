/// Backups: create, inspect, compare, restore, download, and delete layout
/// backup bundles.
library;

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../app_state.dart';
import '../models.dart';

/// Backup management screen with multi-selection.
class BackupsScreen extends StatefulWidget {
  const BackupsScreen({super.key, required this.model});

  final AppModel model;

  @override
  State<BackupsScreen> createState() => _BackupsScreenState();
}

class _BackupsScreenState extends State<BackupsScreen> {
  List<BackupEntry> _backups = [];
  final Set<String> _selected = {};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final backups = await widget.model.api.listBackups();
      if (mounted) {
        setState(() {
          _backups = backups;
          _selected.removeWhere((n) => !backups.any((b) => b.name == n));
          _loading = false;
        });
      }
    } on Object catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Could not list backups: $e')));
      }
    }
  }

  Future<void> _compare(String name) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final diff = await widget.model.api.backupDiff(name);
      if (!mounted) return;
      final changed = (diff['changed'] as List<dynamic>);
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text('Backup vs keyboard — $name'),
          content: SizedBox(
            width: 560,
            child: diff['identical'] == true
                ? const Text('Identical: the keyboard currently matches this '
                    'backup exactly.')
                : SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('${changed.length} key(s) differ:'),
                        const SizedBox(height: 8),
                        for (final c in changed.take(80))
                          Text(
                              '• ${(c as Map)["layer"]}, key '
                              '${(c["position"] as int) + 1}:  now '
                              '"${c["current"]}"  →  backup '
                              '"${c["backup"]}"'),
                        if (changed.length > 80)
                          Text('… and ${changed.length - 80} more'),
                        for (final l
                            in (diff['layers_only_in_backup'] as List<dynamic>))
                          Text('• layer "$l" exists only in the backup'),
                        for (final l in (diff['layers_only_on_keyboard']
                            as List<dynamic>))
                          Text('• layer "$l" exists only on the keyboard'),
                      ],
                    ),
                  ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Close')),
            if (diff['identical'] != true)
              FilledButton.icon(
                onPressed: () {
                  Navigator.pop(context);
                  _restore(name);
                },
                icon: const Icon(Icons.settings_backup_restore),
                label: const Text('Restore this backup'),
              ),
          ],
        ),
      );
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Compare failed: $e')));
    }
  }

  Future<void> _restore(String name) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Restore backup?'),
        content: Text(
            'Every key binding and layer name from "$name" is re-applied to '
            'the keyboard as PENDING changes. Review the result in the Editor '
            'and press Save to persist it — or Discard to abort.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Restore')),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final result = await widget.model.api.backupRestore(name);
      await widget.model.loadEverything();
      await widget.model.refreshState();
      final skippedLayers = (result['skipped_layers'] as List<dynamic>);
      messenger.showSnackBar(SnackBar(
          content: Text('${result["applied"]} binding(s) re-applied as '
              'pending changes — review and Save in the Editor.'
              '${skippedLayers.isEmpty ? "" : " Skipped layers missing from "
                  "the keyboard: ${skippedLayers.join(", ")}."}')));
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Restore failed: $e')));
    }
  }

  Future<void> _deleteSelected() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete ${_selected.length} backup(s)?'),
        content: const Text('Deleted backup files cannot be recovered.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Delete')),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      final n = await widget.model.api.backupsDelete(_selected.toList());
      messenger.showSnackBar(SnackBar(content: Text('$n backup(s) deleted')));
      _selected.clear();
      await _load();
    } on Object catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Delete failed: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Material(
          elevation: 2,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 8, 24, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  const Icon(Icons.shield_outlined),
                  const SizedBox(width: 8),
                  Text('Layout backups', style: theme.textTheme.titleMedium),
                  const Spacer(),
                  FilledButton.icon(
                    onPressed: () async {
                      final messenger = ScaffoldMessenger.of(context);
                      try {
                        final name = await widget.model.api.backupNow();
                        messenger.showSnackBar(
                            SnackBar(content: Text('Backup written: $name')));
                        await _load();
                      } on Object catch (e) {
                        messenger
                            .showSnackBar(SnackBar(content: Text('Failed: $e')));
                      }
                    },
                    icon: const Icon(Icons.save_alt),
                    label: const Text('Back up now'),
                  ),
                ]),
                const SizedBox(height: 4),
                Text(
                  'Each backup is a complete snapshot of the keyboard: every '
                  'layer, every key binding, and the behavior catalog. '
                  'Backups are written automatically before every save, '
                  'settings reset, and firmware generation. Click a backup to '
                  'COMPARE it with the keyboard\'s current state; RESTORE '
                  're-applies it (as pending changes you then Save); DOWNLOAD '
                  'saves a portable copy; tick several to DELETE them.',
                  style: theme.textTheme.bodySmall,
                ),
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Row(children: [
                    Checkbox(
                      tristate: true,
                      value: _selected.isEmpty
                          ? false
                          : _selected.length == _backups.length
                              ? true
                              : null,
                      onChanged: (_) => setState(() {
                        if (_selected.length == _backups.length) {
                          _selected.clear();
                        } else {
                          _selected
                            ..clear()
                            ..addAll(_backups.map((b) => b.name));
                        }
                      }),
                    ),
                    const Text('Select all'),
                    const SizedBox(width: 12),
                    if (_selected.isNotEmpty) ...[
                      Text('${_selected.length} selected'),
                      const SizedBox(width: 12),
                      OutlinedButton.icon(
                        onPressed: _deleteSelected,
                        icon: const Icon(Icons.delete_outline, size: 16),
                        label: const Text('Delete selected'),
                      ),
                      const SizedBox(width: 8),
                      TextButton(
                        onPressed: () => setState(_selected.clear),
                        child: const Text('Clear selection'),
                      ),
                    ],
                  ]),
                ),
              ],
            ),
          ),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              if (_backups.isEmpty)
                const Card(child: ListTile(title: Text('No backups yet')))
              else
                for (final entry in _backups)
                  Card(
                    child: ListTile(
                      leading: Checkbox(
                        value: _selected.contains(entry.name),
                        onChanged: (v) => setState(() {
                          if (v == true) {
                            _selected.add(entry.name);
                          } else {
                            _selected.remove(entry.name);
                          }
                        }),
                      ),
                      title: Text(entry.name),
                      subtitle: Text(
                          '${entry.createdUtc}  ·  ${(entry.sizeBytes / 1024).toStringAsFixed(1)} KB'),
                      onTap: () => _compare(entry.name),
                      trailing: Wrap(spacing: 4, children: [
                        IconButton(
                          tooltip: 'Compare with the keyboard now',
                          icon: const Icon(Icons.difference_outlined),
                          onPressed: () => _compare(entry.name),
                        ),
                        IconButton(
                          tooltip: 'Restore this backup to the keyboard',
                          icon: const Icon(Icons.settings_backup_restore),
                          onPressed: () => _restore(entry.name),
                        ),
                        IconButton(
                          tooltip: 'Download a copy',
                          icon: const Icon(Icons.download),
                          onPressed: () => launchUrl(
                              widget.model.api.backupDownloadUrl(entry.name),
                              webOnlyWindowName: '_blank'),
                        ),
                      ]),
                    ),
                  ),
            ],
          ),
        ),
      ],
    );
  }
}
