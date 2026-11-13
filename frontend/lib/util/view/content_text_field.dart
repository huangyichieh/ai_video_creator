import 'package:flutter/material.dart';
import 'field_row.dart';

/// Shared text editor with content-appropriate height and external reset support.
class ContentTextField extends StatefulWidget {
  final String value, label;
  final ValueChanged<String> onChanged;
  final int? minLines, maxLines;
  final bool readOnly;
  const ContentTextField({
    required this.value,
    required this.label,
    required this.onChanged,
    this.minLines,
    this.maxLines,
    this.readOnly = false,
    super.key,
  });

  @override
  State<ContentTextField> createState() => _ContentTextFieldState();
}

class _ContentTextFieldState extends State<ContentTextField> {
  late final TextEditingController controller;
  @override
  void initState() {
    super.initState();
    controller = TextEditingController(text: widget.value);
  }

  @override
  void didUpdateWidget(covariant ContentTextField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (controller.text != widget.value) {
      controller.value = TextEditingValue(
        text: widget.value,
        selection: TextSelection.collapsed(offset: widget.value.length),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = widget.label.toLowerCase().replaceAll(RegExp('[^a-z]'), '');
    final compact =
        name.endsWith('id') ||
        name.endsWith('name') ||
        name.endsWith('title') ||
        {
          'language',
          'defaultlanguage',
          'provider',
          'model',
          'status',
          'role',
          'filename',
          'path',
          'url',
          'format',
          'gender',
          'agerange',
          'shottype',
        }.contains(name);
    final long =
        name == 'text' ||
        name.endsWith('script') ||
        RegExp(
          'prompt|instruction|outline|story|content|basetext|replacement',
        ).hasMatch(name);
    final description = RegExp(
      'description|appearance|personality|background|action|dialogue|narration|subtext|rules|traits|contradiction|speakingstyle',
    ).hasMatch(name);
    final min =
        widget.minLines ??
        (compact
            ? 1
            : long
            ? 4
            : description
            ? 3
            : 1);
    final max =
        widget.maxLines ??
        (compact
            ? 1
            : long
            ? 16
            : description
            ? 10
            : 8);
    return FieldRow(
      label: widget.label,
      child: TextField(
        controller: controller,
        readOnly: widget.readOnly,
        decoration: const InputDecoration(),
        minLines: min,
        maxLines: max,
        keyboardType: max > 1 ? TextInputType.multiline : TextInputType.text,
        onChanged: widget.readOnly ? null : widget.onChanged,
      ),
    );
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }
}
