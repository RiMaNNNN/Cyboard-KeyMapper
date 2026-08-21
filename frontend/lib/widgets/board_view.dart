/// Physical board rendering from the keyboard's own layout geometry.
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models.dart';

/// Draws every key of a physical layout at its true position, labels each with
/// the active layer's binding, and reports taps.
class BoardView extends StatelessWidget {
  const BoardView({
    super.key,
    required this.layout,
    required this.labels,
    this.labelColors = const [],
    this.selectedPosition,
    this.highlightPositions = const {},
    this.onKeyTap,
    this.onKeyHover,
    this.trackballs = const [],
    this.onTrackballTap,
  });

  /// Geometry source (centi-keyunits).
  final PhysicalLayoutModel layout;

  /// One label per key position (shorter than the key count is tolerated).
  final List<String> labels;

  /// Optional per-position label colors (parallel to [labels]; null entries
  /// fall back to the default text color).
  final List<Color?> labelColors;

  /// Currently selected key position, if any.
  final int? selectedPosition;

  /// Positions drawn with the highlight color (e.g. unlock combo keys).
  final Set<int> highlightPositions;

  /// Called with the tapped key position.
  final ValueChanged<int>? onKeyTap;

  /// Called with the hovered key position (null when the pointer leaves).
  final ValueChanged<int?>? onKeyHover;

  /// Installed trackball sides ('left' and/or 'right'), drawn as round ball
  /// buttons in the gap between the halves. Deliberately outside the key
  /// hover/assign flow: clicking one only fires [onTrackballTap].
  final List<String> trackballs;

  /// Called with the tapped trackball side ('left' or 'right').
  final ValueChanged<String>? onTrackballTap;

  @override
  Widget build(BuildContext context) {
    if (layout.keys.isEmpty) {
      return const Center(child: Text('No physical layout data'));
    }
    int minX = layout.keys.first.x, minY = layout.keys.first.y;
    int maxX = minX, maxY = minY;
    for (final k in layout.keys) {
      minX = math.min(minX, k.x);
      minY = math.min(minY, k.y);
      maxX = math.max(maxX, k.x + k.width);
      maxY = math.max(maxY, k.y + k.height);
    }
    final boardWidth = (maxX - minX).toDouble();
    final boardHeight = (maxY - minY).toDouble();

    return LayoutBuilder(
      builder: (context, constraints) {
        final scale = math.min(
          constraints.maxWidth / boardWidth,
          (constraints.maxHeight.isFinite ? constraints.maxHeight : 560) /
              boardHeight,
        );
        final theme = Theme.of(context);
        return SizedBox(
          width: boardWidth * scale,
          height: boardHeight * scale,
          child: Stack(
            children: [
              for (int i = 0; i < layout.keys.length; i++)
                _positionedKey(
                  context,
                  theme,
                  i,
                  layout.keys[i],
                  minX,
                  minY,
                  scale,
                ),
              for (final side in trackballs)
                _positionedTrackball(
                  theme,
                  side,
                  minX,
                  boardWidth,
                  boardHeight,
                  scale,
                ),
            ],
          ),
        );
      },
    );
  }

  /// A round trackball button in the middle gap between the halves, hugging
  /// its own half's inner edge and sitting low, near the thumb clusters.
  /// Clicks fire [onTrackballTap] only; the ball takes no part in key
  /// hovering or Super Fast Assign.
  Widget _positionedTrackball(
    ThemeData theme,
    String side,
    int minX,
    double boardWidth,
    double boardHeight,
    double scale,
  ) {
    final diameter = (boardHeight * scale * 0.16).clamp(34.0, 64.0);
    final centerX = boardWidth * scale / 2;
    // Inner edge of each half: rightmost extent of keys left of center,
    // and leftmost extent of keys right of center.
    double leftEdge = centerX;
    double rightEdge = centerX;
    for (final k in layout.keys) {
      final keyLeft = (k.x - minX) * scale;
      final keyRight = (k.x + k.width - minX) * scale;
      final keyCenter = (keyLeft + keyRight) / 2;
      if (keyCenter < centerX) {
        leftEdge = math.max(leftEdge == centerX ? 0 : leftEdge, keyRight);
      } else {
        rightEdge = math.min(rightEdge == centerX ? double.infinity : rightEdge,
            keyLeft);
      }
    }
    if (!rightEdge.isFinite) rightEdge = centerX;
    final left = side == 'left'
        ? math.min(leftEdge + 6, centerX - diameter - 2)
        : math.max(rightEdge - diameter - 6, centerX + 2);
    return Positioned(
      left: left,
      top: boardHeight * scale * 0.58 - diameter / 2,
      width: diameter,
      height: diameter,
      child: Tooltip(
        message: '${side == 'left' ? 'Left' : 'Right'} trackball — click for '
            'settings',
        child: Material(
          shape: const CircleBorder(),
          elevation: 3,
          color: theme.colorScheme.secondaryContainer,
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onTrackballTap == null ? null : () => onTrackballTap!(side),
            child: Container(
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                  color: theme.colorScheme.outline,
                  width: 1.5,
                ),
                gradient: RadialGradient(
                  center: const Alignment(-0.35, -0.35),
                  colors: [
                    theme.colorScheme.surfaceBright,
                    theme.colorScheme.secondaryContainer,
                  ],
                ),
              ),
              child: Icon(
                Icons.trip_origin,
                size: diameter * 0.42,
                color: theme.colorScheme.onSecondaryContainer,
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _positionedKey(
    BuildContext context,
    ThemeData theme,
    int position,
    PhysicalKeyModel key,
    int minX,
    int minY,
    double scale,
  ) {
    final selected = position == selectedPosition;
    final highlighted = highlightPositions.contains(position);
    final label = position < labels.length ? labels[position] : '';
    final labelColor = position < labelColors.length
        ? labelColors[position]
        : null;
    // Label size follows the drawn key size so text stays readable at any
    // window width; long labels wrap up to three lines.
    final fontSize = (key.height * scale * 0.26).clamp(10.0, 20.0);

    Widget cap = MouseRegion(
      onEnter: onKeyHover == null ? null : (_) => onKeyHover!(position),
      onExit: onKeyHover == null ? null : (_) => onKeyHover!(null),
      child: GestureDetector(
        onTap: onKeyTap == null ? null : () => onKeyTap!(position),
        child: Container(
          margin: EdgeInsets.all(2.0 * scale / 0.6 * 0.06),
          decoration: BoxDecoration(
            color: highlighted
                ? theme.colorScheme.tertiaryContainer
                : selected
                ? theme.colorScheme.primaryContainer
                : theme.colorScheme.surfaceContainerHighest,
            border: Border.all(
              color: selected
                  ? theme.colorScheme.primary
                  : theme.colorScheme.outlineVariant,
              width: selected ? 2 : 1,
            ),
            borderRadius: BorderRadius.circular(6),
          ),
          alignment: Alignment.center,
          padding: const EdgeInsets.all(2),
          child: Text(
            label,
            textAlign: TextAlign.center,
            softWrap: true,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontSize: fontSize,
              fontWeight: FontWeight.w600,
              height: 1.05,
              color: labelColor ?? theme.colorScheme.onSurface,
            ),
          ),
        ),
      ),
    );

    if (key.r != 0) {
      // Rotation is in centi-degrees around the absolute point (rx, ry).
      cap = Transform.rotate(
        angle: key.r / 100 * math.pi / 180,
        origin: Offset(
          (key.rx - key.x) * scale - key.width * scale / 2,
          (key.ry - key.y) * scale - key.height * scale / 2,
        ),
        child: cap,
      );
    }

    return Positioned(
      left: (key.x - minX) * scale,
      top: (key.y - minY) * scale,
      width: key.width * scale,
      height: key.height * scale,
      child: cap,
    );
  }
}
