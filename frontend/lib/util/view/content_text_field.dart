import 'package:flutter/material.dart';
import 'field_row.dart';

/// Shared text editor with content-appropriate height and external reset support.
class ContentTextField extends StatefulWidget {
  final String value, label;
  final ValueChanged<String> onChanged;
  final int? minLines, maxLines;
  final bool readOnly;
  final bool dirty;
  final bool enabled;
  final bool stacked;
  final String? description;
  final Widget? labelAction;
  const ContentTextField({
    required this.value,
    required this.label,
    required this.onChanged,
    this.minLines,
    this.maxLines,
    this.readOnly = false,
    this.dirty = false,
    this.enabled = true,
    this.stacked = false,
    this.description,
    this.labelAction,
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
    Widget layout(Widget child) {
      if (!widget.stacked) return FieldRow(label: widget.label, child: child);
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                '${widget.label}:',
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
              if (widget.labelAction != null) widget.labelAction!,
            ],
          ),
          if (widget.description != null) ...[
            const SizedBox(height: 4),
            Text(
              widget.description!,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
          const SizedBox(height: 8),
          Semantics(label: widget.label, child: child),
        ],
      );
    }

    if (widget.readOnly) {
      return layout(
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: SelectableText(widget.value),
        ),
      );
    }
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
    return layout(
      TextField(
        controller: controller,
        enabled: widget.enabled,
        readOnly: widget.readOnly,
        decoration: InputDecoration(
          enabledBorder: widget.dirty
              ? const OutlineInputBorder(
                  borderSide: BorderSide(color: Color(0xff64b5f6), width: 1.5),
                )
              : null,
          focusedBorder: widget.dirty
              ? const OutlineInputBorder(
                  borderSide: BorderSide(color: Color(0xff64b5f6), width: 2),
                )
              : null,
        ),
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
