import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../util/view/field_row.dart';

/// Shared native canvas for floor-plan camera placement and image-region edits.
class CameraEditor extends StatefulWidget {
  final String? imageUrl;
  final bool rectangle;
  final Map<String, dynamic> configuration;
  final ValueChanged<Map<String, dynamic>> onChanged;
  const CameraEditor({
    super.key,
    this.imageUrl,
    this.rectangle = false,
    this.configuration = const {},
    required this.onChanged,
  });
  @override
  State<CameraEditor> createState() => _CameraEditorState();
}

class _CameraEditorState extends State<CameraEditor> {
  Offset start = const Offset(.5, .5), end = const Offset(.8, .5);
  double fov = 70, height = 1.5, pitch = 0;
  @override
  void initState() {
    super.initState();
    final setup = widget.configuration['setup'] as Map?;
    if (setup != null) {
      final g = widget.configuration;
      final scale = (g['scale'] as num?)?.toDouble() ?? 1;
      start = Offset(
        ((setup['position']['x'] as num).toDouble() -
                    (g['minX'] as num).toDouble()) *
                scale /
                800 +
            50 / 800,
        (550 -
                ((setup['position']['y'] as num).toDouble() -
                        (g['minY'] as num).toDouble()) *
                    scale) /
            600,
      );
      final yaw = (setup['yaw'] as num).toDouble() * math.pi / 180;
      end = start + Offset(math.sin(yaw) * .15, -math.cos(yaw) * .2);
      height = (setup['heightMeters'] as num?)?.toDouble() ?? 1.5;
      pitch = (setup['pitch'] as num?)?.toDouble() ?? 0;
    }
  }

  Offset position(Offset point, Size size) => Offset(
    (point.dx / size.width).clamp(0, 1),
    (point.dy / size.height).clamp(0, 1),
  );
  void emit() {
    widget.onChanged(
      widget.rectangle
          ? {
              'left': math.min(start.dx, end.dx),
              'top': math.min(start.dy, end.dy),
              'right': math.max(start.dx, end.dx),
              'bottom': math.max(start.dy, end.dy),
            }
          : {
              'position': {
                'x': widget.configuration.isEmpty
                    ? start.dx
                    : (start.dx * 800 - 50) /
                              (widget.configuration['scale'] as num) +
                          (widget.configuration['minX'] as num),
                'y': widget.configuration.isEmpty
                    ? start.dy
                    : (550 - start.dy * 600) /
                              (widget.configuration['scale'] as num) +
                          (widget.configuration['minY'] as num),
              },
              'yaw':
                  (math.atan2(
                            (end.dx - start.dx) *
                                (widget.configuration.isEmpty ? 1 : 800),
                            (start.dy - end.dy) *
                                (widget.configuration.isEmpty ? 1 : 600),
                          ) *
                          180 /
                          math.pi +
                      360) %
                  360,
              'fov': fov,
              'heightMeters': height,
              'pitch': pitch,
            },
    );
  }

  @override
  Widget build(BuildContext context) => Column(
    children: [
      LayoutBuilder(
        builder: (context, box) {
          final size = Size(box.maxWidth, 280);
          return GestureDetector(
            onPanStart: (value) {
              setState(() {
                start = position(value.localPosition, size);
                end = start;
              });
              emit();
            },
            onPanUpdate: (value) {
              setState(() => end = position(value.localPosition, size));
              emit();
            },
            child: SizedBox(
              width: size.width,
              height: size.height,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  if (widget.imageUrl != null)
                    Image.network(widget.imageUrl!, fit: BoxFit.fill)
                  else
                    Container(
                      color: Theme.of(context).colorScheme.surfaceContainerHigh,
                    ),
                  CustomPaint(
                    painter: _CameraPainter(start, end, fov, widget.rectangle),
                  ),
                ],
              ),
            ),
          );
        },
      ),
      if (!widget.rectangle) ...[
        FieldRow(
          label: 'FOV (${fov.round()}°)',
          child: Slider(
            value: fov,
            min: 20,
            max: 110,
            onChanged: (value) {
              setState(() => fov = value);
              emit();
            },
          ),
        ),
        FieldRow(
          label: 'Camera height (${height.toStringAsFixed(2)} m)',
          child: Slider(
            value: height,
            min: .1,
            max:
                ((widget.configuration['roomHeight'] as num?)?.toDouble() ??
                    6) -
                .01,
            onChanged: (value) {
              setState(() => height = value);
              emit();
            },
          ),
        ),
        FieldRow(
          label: 'Tilt (${pitch.round()}°)',
          child: Slider(
            value: pitch,
            min: -60,
            max: 60,
            onChanged: (value) {
              setState(() => pitch = value);
              emit();
            },
          ),
        ),
        TextButton(
          onPressed: () {
            setState(() {
              height = 1.5;
              pitch = 0;
            });
            emit();
          },
          child: const Text('Reset camera pose'),
        ),
      ],
      Text(
        widget.rectangle
            ? 'Drag the area to modify.'
            : 'Click and drag to set camera position and facing direction.',
      ),
    ],
  );
}

class _CameraPainter extends CustomPainter {
  final Offset start, end;
  final double fov;
  final bool rectangle;
  _CameraPainter(this.start, this.end, this.fov, this.rectangle);
  @override
  void paint(Canvas canvas, Size size) {
    final a = Offset(start.dx * size.width, start.dy * size.height),
        b = Offset(end.dx * size.width, end.dy * size.height);
    final paint = Paint()
      ..color = Colors.red
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    if (rectangle) {
      canvas.drawRect(Rect.fromPoints(a, b), paint);
      return;
    }
    canvas.drawCircle(a, 4, Paint()..color = Colors.red);
    final angle = math.atan2(b.dy - a.dy, b.dx - a.dx),
        sweep = fov * math.pi / 180;
    final path = Path()
      ..moveTo(a.dx, a.dy)
      ..arcTo(
        Rect.fromCircle(center: a, radius: 100),
        angle - sweep / 2,
        sweep,
        false,
      )
      ..close();
    canvas.drawPath(path, Paint()..color = Colors.red.withValues(alpha: .18));
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _CameraPainter oldDelegate) => true;
}
