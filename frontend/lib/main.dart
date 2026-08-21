/// KeyMapper — configuration app for the Cyboard Imprint (ZMK Studio protocol).
library;

import 'package:flutter/material.dart';

import 'api_client.dart';
import 'app_state.dart';
import 'screens/advanced_screen.dart';
import 'screens/backups_screen.dart';
import 'screens/editor_screen.dart';
import 'screens/firmware_screen.dart';
import 'screens/help_screen.dart';
import 'screens/home_screen.dart';
import 'screens/sequences_screen.dart';

void main() {
  final model = AppModel(ApiClient());
  model.start();
  runApp(KeyMapperApp(model: model));
}

/// Root widget: navigation rail plus the seven feature screens.
class KeyMapperApp extends StatefulWidget {
  const KeyMapperApp({super.key, required this.model});

  final AppModel model;

  @override
  State<KeyMapperApp> createState() => _KeyMapperAppState();
}

class _KeyMapperAppState extends State<KeyMapperApp> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'KeyMapper',
      debugShowCheckedModeBanner: false,
      // Every piece of text in the app is mouse-selectable and copiable.
      builder: (context, child) =>
          child == null ? const SizedBox.shrink() : SelectionArea(child: child),
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF3355AA)),
        useMaterial3: true,
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF3355AA), brightness: Brightness.dark),
        useMaterial3: true,
      ),
      home: ListenableBuilder(
        listenable: widget.model,
        builder: (context, _) {
          final snap = widget.model.snapshot;
          final screens = [
            HomeScreen(model: widget.model),
            EditorScreen(model: widget.model),
            SequencesScreen(model: widget.model),
            AdvancedScreen(model: widget.model),
            FirmwareScreen(model: widget.model),
            BackupsScreen(model: widget.model),
            const HelpScreen(),
          ];
          return Scaffold(
            appBar: AppBar(
              title: const Text('KeyMapper — Cyboard Imprint'),
              actions: [
                if (snap.unsavedChanges)
                  const Padding(
                    padding: EdgeInsets.only(right: 8),
                    child: Chip(
                      avatar:
                          Icon(Icons.circle, color: Colors.orange, size: 12),
                      label: Text('unsaved'),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.only(right: 16),
                  child: Icon(
                    snap.connected
                        ? (snap.locked ? Icons.lock : Icons.lock_open)
                        : Icons.usb_off,
                    color: snap.connected
                        ? (snap.locked ? Colors.orange : Colors.green)
                        : Colors.red,
                  ),
                ),
              ],
            ),
            body: Row(
              children: [
                NavigationRail(
                  selectedIndex: _tab,
                  onDestinationSelected: (i) => setState(() => _tab = i),
                  labelType: NavigationRailLabelType.all,
                  destinations: const [
                    NavigationRailDestination(
                        icon: Icon(Icons.home_outlined), label: Text('Home')),
                    NavigationRailDestination(
                        icon: Icon(Icons.keyboard_alt_outlined),
                        label: Text('Editor')),
                    NavigationRailDestination(
                        icon: Icon(Icons.playlist_add),
                        label: Text('Sequences')),
                    NavigationRailDestination(
                        icon: Icon(Icons.tune), label: Text('Advanced')),
                    NavigationRailDestination(
                        icon: Icon(Icons.memory), label: Text('Firmware')),
                    NavigationRailDestination(
                        icon: Icon(Icons.shield_outlined),
                        label: Text('Backups')),
                    NavigationRailDestination(
                        icon: Icon(Icons.help_outline), label: Text('Help')),
                  ],
                ),
                const VerticalDivider(width: 1),
                Expanded(child: screens[_tab]),
              ],
            ),
          );
        },
      ),
    );
  }
}
