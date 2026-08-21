/// Curated keycode catalog for building `&kp` parameters and macro bindings.
///
/// A ZMK key-press parameter packs implicit modifiers into bits 24-31, the HID
/// usage page into bits 16-23 and the usage ID into bits 0-15. The catalog
/// mirrors the backend's mapping tables so values round-trip between the wire
/// format and human-readable names.
library;

/// One selectable keycode.
class Keycode {
  const Keycode(this.name, this.page, this.usage, this.label);

  /// Canonical ZMK name (as used in devicetree source).
  final String name;

  /// HID usage page (0x07 keyboard, 0x0C consumer).
  final int page;

  /// HID usage ID within the page.
  final int usage;

  /// Human-readable description shown in pickers.
  final String label;

  /// The 32-bit `&kp` parameter value (no implicit modifiers).
  int get param => (page << 16) | usage;

  /// Studio-style category for filtering in pickers.
  String get category {
    if (page == 0x0C) {
      if (label.startsWith('App Launch:')) return 'App Launch';
      if (label.startsWith('App Control:')) return 'App Control';
      return 'Media';
    }
    if (usage >= 0x04 && usage <= 0x1D) return 'Letters';
    if (usage >= 0x1E && usage <= 0x27) return 'Numbers';
    if ((usage >= 0x3A && usage <= 0x45) || (usage >= 0x68 && usage <= 0x73)) {
      return 'F-keys';
    }
    if ((usage >= 0x53 && usage <= 0x63) || usage == 0x67) return 'Keypad';
    if (usage >= 0xE0 && usage <= 0xE7) return 'Modifiers';
    return 'Nav & symbols';
  }
}

/// Category filter order shown in pickers.
const List<String> keycodeCategories = [
  'All', 'Letters', 'Numbers', 'Nav & symbols', 'F-keys', 'Keypad',
  'Modifiers', 'Media', 'App Launch', 'App Control',
];

/// Builds the full catalog once.
List<Keycode> _buildCatalog() {
  final List<Keycode> list = [];
  for (int i = 0; i < 26; i++) {
    final letter = String.fromCharCode(0x41 + i);
    list.add(Keycode(letter, 0x07, 0x04 + i, 'Letter $letter'));
  }
  for (int i = 1; i <= 9; i++) {
    list.add(Keycode('N$i', 0x07, 0x1E + i - 1, 'Digit $i'));
  }
  list.add(const Keycode('N0', 0x07, 0x27, 'Digit 0'));
  list.addAll(const [
    Keycode('RET', 0x07, 0x28, 'Enter / Return'),
    Keycode('ESC', 0x07, 0x29, 'Escape'),
    Keycode('BSPC', 0x07, 0x2A, 'Backspace'),
    Keycode('TAB', 0x07, 0x2B, 'Tab'),
    Keycode('SPACE', 0x07, 0x2C, 'Space'),
    Keycode('MINUS', 0x07, 0x2D, '- and _'),
    Keycode('EQUAL', 0x07, 0x2E, '= and +'),
    Keycode('LBKT', 0x07, 0x2F, '[ and {'),
    Keycode('RBKT', 0x07, 0x30, '] and }'),
    Keycode('BSLH', 0x07, 0x31, r'\ and |'),
    Keycode('SEMI', 0x07, 0x33, '; and :'),
    Keycode('SQT', 0x07, 0x34, "' and \""),
    Keycode('GRAVE', 0x07, 0x35, '` and ~'),
    Keycode('COMMA', 0x07, 0x36, ', and <'),
    Keycode('DOT', 0x07, 0x37, '. and >'),
    Keycode('FSLH', 0x07, 0x38, '/ and ?'),
    Keycode('CAPS', 0x07, 0x39, 'Caps Lock'),
  ]);
  for (int i = 1; i <= 12; i++) {
    list.add(Keycode('F$i', 0x07, 0x3A + i - 1, 'Function key F$i'));
  }
  list.addAll(const [
    Keycode('PSCRN', 0x07, 0x46, 'Print Screen'),
    Keycode('SLCK', 0x07, 0x47, 'Scroll Lock'),
    Keycode('PAUSE_BREAK', 0x07, 0x48, 'Pause / Break'),
    Keycode('INS', 0x07, 0x49, 'Insert'),
    Keycode('HOME', 0x07, 0x4A, 'Home'),
    Keycode('PG_UP', 0x07, 0x4B, 'Page Up'),
    Keycode('DEL', 0x07, 0x4C, 'Delete'),
    Keycode('END', 0x07, 0x4D, 'End'),
    Keycode('PG_DN', 0x07, 0x4E, 'Page Down'),
    Keycode('RIGHT', 0x07, 0x4F, 'Arrow Right'),
    Keycode('LEFT', 0x07, 0x50, 'Arrow Left'),
    Keycode('DOWN', 0x07, 0x51, 'Arrow Down'),
    Keycode('UP', 0x07, 0x52, 'Arrow Up'),
    Keycode('KP_NUM', 0x07, 0x53, 'Num Lock'),
    Keycode('KP_SLASH', 0x07, 0x54, 'Keypad /'),
    Keycode('KP_MULTIPLY', 0x07, 0x55, 'Keypad *'),
    Keycode('KP_MINUS', 0x07, 0x56, 'Keypad -'),
    Keycode('KP_PLUS', 0x07, 0x57, 'Keypad +'),
    Keycode('KP_ENTER', 0x07, 0x58, 'Keypad Enter'),
  ]);
  for (int i = 1; i <= 9; i++) {
    list.add(Keycode('KP_N$i', 0x07, 0x59 + i - 1, 'Keypad digit $i'));
  }
  list.addAll(const [
    Keycode('KP_N0', 0x07, 0x62, 'Keypad digit 0'),
    Keycode('KP_DOT', 0x07, 0x63, 'Keypad .'),
    Keycode('K_APP', 0x07, 0x65, 'Context menu'),
    Keycode('LCTRL', 0x07, 0xE0, 'Left Control'),
    Keycode('LSHFT', 0x07, 0xE1, 'Left Shift'),
    Keycode('LALT', 0x07, 0xE2, 'Left Alt'),
    Keycode('LGUI', 0x07, 0xE3, 'Left GUI / Win'),
    Keycode('RCTRL', 0x07, 0xE4, 'Right Control'),
    Keycode('RSHFT', 0x07, 0xE5, 'Right Shift'),
    Keycode('RALT', 0x07, 0xE6, 'Right Alt (AltGr)'),
    Keycode('RGUI', 0x07, 0xE7, 'Right GUI / Win'),
    Keycode('C_MUTE', 0x0C, 0xE2, 'Volume Mute'),
    Keycode('C_VOL_UP', 0x0C, 0xE9, 'Volume Up'),
    Keycode('C_VOL_DN', 0x0C, 0xEA, 'Volume Down'),
    Keycode('C_PP', 0x0C, 0xCD, 'Play / Pause'),
    Keycode('C_NEXT', 0x0C, 0xB5, 'Next Track'),
    Keycode('C_PREV', 0x0C, 0xB6, 'Previous Track'),
    Keycode('C_STOP', 0x0C, 0xB7, 'Stop Media'),
    Keycode('C_PLAY', 0x0C, 0xB0, 'Play'),
    Keycode('C_PAUSE', 0x0C, 0xB1, 'Pause'),
    Keycode('C_REC', 0x0C, 0xB2, 'Record'),
    Keycode('C_FF', 0x0C, 0xB3, 'Fast Forward'),
    Keycode('C_RW', 0x0C, 0xB4, 'Rewind'),
    Keycode('C_EJECT', 0x0C, 0xB8, 'Eject'),
    Keycode('C_BRI_INC', 0x0C, 0x6F, 'Brightness Up'),
    Keycode('C_BRI_DEC', 0x0C, 0x70, 'Brightness Down'),
    Keycode('C_BRI_MIN', 0x0C, 0x73, 'Brightness Minimum'),
    Keycode('C_BRI_MAX', 0x0C, 0x74, 'Brightness Maximum'),
    Keycode('C_BRI_AUTO', 0x0C, 0x75, 'Brightness Auto'),
    Keycode('C_PWR', 0x0C, 0x30, 'Consumer Power'),
    Keycode('C_SLEEP', 0x0C, 0x32, 'System Sleep'),
    Keycode('C_MENU', 0x0C, 0x40, 'Media Menu'),
    Keycode('C_CAPTIONS', 0x0C, 0x61, 'Closed Captions'),
    // AL = Application Launch keys (open a program).
    Keycode('C_AL_WORD', 0x0C, 0x184, 'App Launch: Word Processor'),
    Keycode('C_AL_TEXT_EDITOR', 0x0C, 0x185, 'App Launch: Text Editor'),
    Keycode('C_AL_SHEET', 0x0C, 0x186, 'App Launch: Spreadsheet'),
    Keycode('C_AL_GRAPHICS_EDITOR', 0x0C, 0x187, 'App Launch: Graphics Editor'),
    Keycode('C_AL_PRESENTATION', 0x0C, 0x188, 'App Launch: Presentation App'),
    Keycode('C_AL_DB', 0x0C, 0x189, 'App Launch: Database App'),
    Keycode('C_AL_MAIL', 0x0C, 0x18A, 'App Launch: Email Reader'),
    Keycode('C_AL_NEWS', 0x0C, 0x18B, 'App Launch: News Reader'),
    Keycode('C_AL_VOICEMAIL', 0x0C, 0x18C, 'App Launch: Voicemail'),
    Keycode('C_AL_CONTACTS', 0x0C, 0x18D, 'App Launch: Contacts / Address Book'),
    Keycode('C_AL_CAL', 0x0C, 0x18E, 'App Launch: Calendar'),
    Keycode('C_AL_TASK_MANAGER', 0x0C, 0x18F, 'App Launch: Task Manager'),
    Keycode('C_AL_JOURNAL', 0x0C, 0x190, 'App Launch: Journal'),
    Keycode('C_AL_FINANCE', 0x0C, 0x191, 'App Launch: Finance App'),
    Keycode('C_AL_CALC', 0x0C, 0x192, 'App Launch: Calculator'),
    Keycode('C_AL_AV_CAPTURE_PLAYBACK', 0x0C, 0x193, 'App Launch: A/V Capture'),
    Keycode('C_AL_MY_COMPUTER', 0x0C, 0x194, 'App Launch: My Computer / Files'),
    Keycode('C_AL_WWW', 0x0C, 0x196, 'App Launch: Internet Browser'),
    Keycode('C_AL_CHAT', 0x0C, 0x199, 'App Launch: Network Chat'),
    Keycode('C_AL_LOGOFF', 0x0C, 0x19C, 'App Launch: Log Off'),
    Keycode('C_AL_LOCK', 0x0C, 0x19E, 'App Launch: Lock Screen / Screensaver'),
    Keycode('C_AL_CONTROL_PANEL', 0x0C, 0x19F, 'App Launch: Control Panel'),
    Keycode('C_AL_SELECT_TASK', 0x0C, 0x1A2, 'App Launch: Select Task'),
    Keycode('C_AL_NEXT_TASK', 0x0C, 0x1A3, 'App Launch: Next Task'),
    Keycode('C_AL_PREV_TASK', 0x0C, 0x1A4, 'App Launch: Previous Task'),
    Keycode('C_AL_HELP', 0x0C, 0x1A6, 'App Launch: Help'),
    Keycode('C_AL_DOCS', 0x0C, 0x1A7, 'App Launch: Documents'),
    Keycode('C_AL_SPELL', 0x0C, 0x1AB, 'App Launch: Spell Check'),
    Keycode('C_AL_KEYBOARD_LAYOUT', 0x0C, 0x1AE, 'App Launch: On-screen Keyboard'),
    // AC = Application Control keys (act inside the current program).
    Keycode('C_AC_NEW', 0x0C, 0x201, 'App Control: New'),
    Keycode('C_AC_OPEN', 0x0C, 0x202, 'App Control: Open'),
    Keycode('C_AC_CLOSE', 0x0C, 0x203, 'App Control: Close'),
    Keycode('C_AC_EXIT', 0x0C, 0x204, 'App Control: Exit'),
    Keycode('C_AC_SAVE', 0x0C, 0x207, 'App Control: Save'),
    Keycode('C_AC_PRINT', 0x0C, 0x208, 'App Control: Print'),
    Keycode('C_AC_PROPS', 0x0C, 0x209, 'App Control: Properties'),
    Keycode('C_AC_UNDO', 0x0C, 0x21A, 'App Control: Undo'),
    Keycode('C_AC_COPY', 0x0C, 0x21B, 'App Control: Copy'),
    Keycode('C_AC_CUT', 0x0C, 0x21C, 'App Control: Cut'),
    Keycode('C_AC_PASTE', 0x0C, 0x21D, 'App Control: Paste'),
    Keycode('C_AC_FIND', 0x0C, 0x21F, 'App Control: Find'),
    Keycode('C_AC_SEARCH', 0x0C, 0x221, 'App Control: Search'),
    Keycode('C_AC_GOTO', 0x0C, 0x222, 'App Control: Go To'),
    Keycode('C_AC_HOME', 0x0C, 0x223, 'App Control: Browser Home'),
    Keycode('C_AC_BACK', 0x0C, 0x224, 'App Control: Browser Back'),
    Keycode('C_AC_FORWARD', 0x0C, 0x225, 'App Control: Browser Forward'),
    Keycode('C_AC_STOP', 0x0C, 0x226, 'App Control: Browser Stop'),
    Keycode('C_AC_REFRESH', 0x0C, 0x227, 'App Control: Refresh'),
    Keycode('C_AC_BOOKMARKS', 0x0C, 0x22A, 'App Control: Bookmarks'),
    Keycode('C_AC_ZOOM_IN', 0x0C, 0x22D, 'App Control: Zoom In'),
    Keycode('C_AC_ZOOM_OUT', 0x0C, 0x22E, 'App Control: Zoom Out'),
    Keycode('C_AC_SCROLL_UP', 0x0C, 0x233, 'App Control: Scroll Up'),
    Keycode('C_AC_SCROLL_DOWN', 0x0C, 0x234, 'App Control: Scroll Down'),
    Keycode('C_AC_CANCEL', 0x0C, 0x25F, 'App Control: Cancel'),
  ]);
  return List.unmodifiable(list);
}

/// The complete keycode catalog (stable order: letters, digits, symbols, F-keys,
/// navigation, keypad, modifiers, consumer keys).
final List<Keycode> keycodeCatalog = _buildCatalog();

/// Implicit-modifier wrapper bits (bit value -> ZMK wrapper name).
const Map<int, String> modifierWrappers = {
  0x01: 'LC',
  0x02: 'LS',
  0x04: 'LA',
  0x08: 'LG',
  0x10: 'RC',
  0x20: 'RS',
  0x40: 'RA',
  0x80: 'RG',
};

/// US-QWERTY character map for typing text through key taps.
///
/// Maps each typable character to its devicetree binding. Letters use the
/// plain keycode (uppercase adds LS); symbols follow the US layout, which is
/// what Windows Alt-code users typically run. Unmappable characters return
/// null from [bindingsForText].
const Map<String, String> _usCharBindings = {
  ' ': '&kp SPACE', '.': '&kp DOT', ',': '&kp COMMA', ';': '&kp SEMI',
  "'": '&kp SQT', '`': '&kp GRAVE', '-': '&kp MINUS', '=': '&kp EQUAL',
  '[': '&kp LBKT', ']': '&kp RBKT', '\\': '&kp BSLH', '/': '&kp FSLH',
  ':': '&kp LS(SEMI)', '"': '&kp LS(SQT)', '~': '&kp LS(GRAVE)',
  '_': '&kp LS(MINUS)', '+': '&kp LS(EQUAL)', '{': '&kp LS(LBKT)',
  '}': '&kp LS(RBKT)', '|': '&kp LS(BSLH)', '?': '&kp LS(FSLH)',
  '<': '&kp LS(COMMA)', '>': '&kp LS(DOT)', '!': '&kp LS(N1)',
  '@': '&kp LS(N2)', '#': '&kp LS(N3)', r'$': '&kp LS(N4)',
  '%': '&kp LS(N5)', '^': '&kp LS(N6)', '&': '&kp LS(N7)',
  '*': '&kp LS(N8)', '(': '&kp LS(N9)', ')': '&kp LS(N0)',
};

/// Converts [text] into one `&kp` binding per character (US-QWERTY host
/// layout), or null when a character cannot be typed with plain key taps.
List<String>? bindingsForText(String text) {
  final result = <String>[];
  for (final rune in text.runes) {
    final ch = String.fromCharCode(rune);
    if (RegExp(r'[a-z]').hasMatch(ch)) {
      result.add('&kp ${ch.toUpperCase()}');
    } else if (RegExp(r'[A-Z]').hasMatch(ch)) {
      result.add('&kp LS($ch)');
    } else if (RegExp(r'[0-9]').hasMatch(ch)) {
      result.add('&kp N$ch');
    } else if (_usCharBindings.containsKey(ch)) {
      result.add(_usCharBindings[ch]!);
    } else {
      return null;
    }
  }
  return result;
}

/// Renders a raw `&kp` parameter as a short human-readable label.
String describeKeycodeParam(int param) {
  final mods = (param >> 24) & 0xFF;
  final page = (param >> 16) & 0xFF;
  final usage = param & 0xFFFF;
  String? name;
  for (final k in keycodeCatalog) {
    if (k.page == page && k.usage == usage) {
      name = k.name;
      break;
    }
  }
  name ??= '0x${param.toRadixString(16).toUpperCase()}';
  modifierWrappers.forEach((bit, wrapper) {
    if ((mods & bit) != 0) name = '$wrapper($name)';
  });
  return name!;
}
