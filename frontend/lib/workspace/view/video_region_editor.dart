import '../../util/view/field_row.dart';
import 'dart:js_interop';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

/// One interval track controls playback, seeking and the protected edit window.
class VideoRegionEditor extends StatefulWidget {
  final String url;
  final double duration;
  final double aspectRatio;
  final void Function(Map<String, dynamic>) onChanged;
  const VideoRegionEditor({
    super.key,
    required this.url,
    required this.duration,
    required this.onChanged,
    this.aspectRatio = 16 / 9,
  });
  @override
  State<VideoRegionEditor> createState() => _VideoRegionEditorState();
}

class _VideoRegionEditorState extends State<VideoRegionEditor> {
  web.HTMLVideoElement? video;
  late double end;
  double start = 0, head = 0;
  Offset? a, b;
  String? error;
  final startField = TextEditingController(),
      endField = TextEditingController();
  String drag = '';
  double dragStart = 0, dragEnd = 0, origin = 0;
  @override
  void initState() {
    super.initState();
    end = widget.duration;
    syncFields();
  }

  String code(double time) {
    final ms = (time * 1000).round();
    return '${(ms ~/ 3600000).toString().padLeft(2, '0')}:${(ms ~/ 60000 % 60).toString().padLeft(2, '0')}:${(ms ~/ 1000 % 60).toString().padLeft(2, '0')}.${(ms % 1000).toString().padLeft(3, '0')}';
  }

  double parse(String text) {
    if (!RegExp(r'^\d{2,}:\d{2}:\d{2}(\.\d{1,3})?$').hasMatch(text.trim())) {
      throw const FormatException('Use HH:MM:SS or HH:MM:SS.sss');
    }
    final fields = text.split(':');
    final minutes = int.parse(fields[1]), seconds = double.parse(fields[2]);
    if (minutes >= 60 || seconds >= 60) {
      throw const FormatException('Minutes and seconds must be below 60');
    }
    return int.parse(fields[0]) * 3600 + minutes * 60 + seconds;
  }

  void syncFields() {
    startField.text = code(start);
    endField.text = code(end);
  }

  void emit() {
    final left = a, right = b;
    widget.onChanged({
      'start_time': start,
      'end_time': end,
      if (left != null && right != null)
        'rectangle': {
          'left': math.min(left.dx, right.dx),
          'top': math.min(left.dy, right.dy),
          'right': math.max(left.dx, right.dx),
          'bottom': math.max(left.dy, right.dy),
        },
    });
  }

  void manual() {
    try {
      final s = parse(startField.text), e = parse(endField.text);
      if (s < 0 || e > widget.duration || s >= e) {
        throw const FormatException('Start must precede end within the video');
      }
      setState(() {
        start = s;
        end = e;
        head = head.clamp(start, end);
        error = null;
      });
      video?.currentTime = head;
      emit();
    } catch (value) {
      setState(() => error = value.toString());
    }
  }

  Offset point(Offset value, Size size) => Offset(
    (value.dx / size.width).clamp(0, 1),
    (value.dy / size.height).clamp(0, 1),
  );
  void seek(double value) {
    setState(() => head = value.clamp(start, end));
    video?.currentTime = head;
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      AspectRatio(
        aspectRatio: widget.aspectRatio,
        child: LayoutBuilder(
          builder: (context, box) => GestureDetector(
            onPanStart: (event) {
              setState(() {
                a = point(event.localPosition, box.biggest);
                b = a;
              });
            },
            onPanUpdate: (event) {
              setState(() => b = point(event.localPosition, box.biggest));
              emit();
            },
            child: Stack(
              fit: StackFit.expand,
              children: [
                HtmlElementView.fromTagName(
                  tagName: 'video',
                  onElementCreated: (element) {
                    final item = element as web.HTMLVideoElement;
                    video = item;
                    item.src = widget.url;
                    item.preload = 'auto';
                    item.controls = false;
                    item.style.width = '100%';
                    item.style.height = '100%';
                    item.style.pointerEvents = 'none';
                    item.addEventListener(
                      'timeupdate',
                      ((web.Event event) {
                        if (!mounted) return;
                        if (item.currentTime >= end) {
                          item.pause();
                          item.currentTime = end;
                        }
                        setState(
                          () => head = item.currentTime.clamp(start, end),
                        );
                      }).toJS,
                    );
                  },
                ),
                CustomPaint(painter: _Region(a, b)),
              ],
            ),
          ),
        ),
      ),
      const Text(
        'Drag the area to modify. Play previews only the selected interval.',
      ),
      Row(
        children: [
          IconButton(
            onPressed: () async {
              final item = video;
              if (item == null) return;
              if (!item.paused) {
                item.pause();
                return;
              }
              if (item.currentTime < start || item.currentTime >= end) {
                item.currentTime = start;
              }
              try {
                await item.play().toDart;
              } catch (value) {
                if (mounted) setState(() => error = value.toString());
              }
            },
            icon: const Icon(Icons.play_arrow),
          ),
          Expanded(child: Text('${code(head)} / ${code(widget.duration)}')),
        ],
      ),
      LayoutBuilder(
        builder: (context, box) {
          final width = box.maxWidth;
          return GestureDetector(
            onTapDown: (event) =>
                seek(event.localPosition.dx / width * widget.duration),
            onPanStart: (event) {
              origin = event.localPosition.dx / width * widget.duration;
              dragStart = start;
              dragEnd = end;
              drag = (origin - start).abs() < widget.duration * .04
                  ? 'start'
                  : (origin - end).abs() < widget.duration * .04
                  ? 'end'
                  : 'move';
            },
            onPanUpdate: (event) {
              final value = event.localPosition.dx / width * widget.duration;
              setState(() {
                if (drag == 'start') {
                  start = value.clamp(0, end - .001);
                } else if (drag == 'end') {
                  end = value.clamp(start + .001, widget.duration);
                } else {
                  final length = dragEnd - dragStart;
                  start = (dragStart + value - origin).clamp(
                    0,
                    widget.duration - length,
                  );
                  end = start + length;
                }
                head = head.clamp(start, end);
                syncFields();
              });
              video?.pause();
              video?.currentTime = head;
              emit();
            },
            child: SizedBox(
              height: 44,
              child: CustomPaint(
                size: Size(width, 44),
                painter: _Timeline(
                  start / widget.duration,
                  end / widget.duration,
                  head / widget.duration,
                ),
              ),
            ),
          );
        },
      ),
      Row(
        children: [
          Expanded(
            child: FieldRow(
              label: 'Start (HH:MM:SS)',
              child: TextField(
                controller: startField,
                decoration: const InputDecoration(),
                onSubmitted: (_) => manual(),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: FieldRow(
              label: 'End (HH:MM:SS)',
              child: TextField(
                controller: endField,
                decoration: const InputDecoration(),
                onSubmitted: (_) => manual(),
              ),
            ),
          ),
        ],
      ),
      TextButton(onPressed: manual, child: const Text('Apply time range')),
      if (error != null)
        Text(error!, style: const TextStyle(color: Colors.red)),
    ],
  );
  @override
  void dispose() {
    video?.pause();
    video?.removeAttribute('src');
    video?.load();
    startField.dispose();
    endField.dispose();
    super.dispose();
  }
}

class _Timeline extends CustomPainter {
  final double start, end, head;
  _Timeline(this.start, this.end, this.head);
  @override
  void paint(Canvas c, Size s) {
    c.drawRect(Offset.zero & s, Paint()..color = const Color(0xff262730));
    final rect = Rect.fromLTRB(start * s.width, 2, end * s.width, s.height - 2);
    c.drawRect(rect, Paint()..color = Colors.red.withValues(alpha: .3));
    c.drawRect(
      rect,
      Paint()
        ..color = Colors.red
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
    for (final x in [start, end]) {
      c.drawLine(
        Offset(x * s.width, 0),
        Offset(x * s.width, s.height),
        Paint()
          ..color = Colors.red
          ..strokeWidth = 7,
      );
    }
    c.drawLine(
      Offset(head * s.width, 0),
      Offset(head * s.width, s.height),
      Paint()
        ..color = Colors.white
        ..strokeWidth = 3,
    );
  }

  @override
  bool shouldRepaint(covariant _Timeline old) =>
      old.start != start || old.end != end || old.head != head;
}

class _Region extends CustomPainter {
  final Offset? a, b;
  _Region(this.a, this.b);
  @override
  void paint(Canvas c, Size s) {
    if (a == null || b == null) return;
    c.drawRect(
      Rect.fromPoints(
        Offset(a!.dx * s.width, a!.dy * s.height),
        Offset(b!.dx * s.width, b!.dy * s.height),
      ),
      Paint()
        ..color = Colors.red
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
  }

  @override
  bool shouldRepaint(covariant _Region old) => old.a != a || old.b != b;
}
