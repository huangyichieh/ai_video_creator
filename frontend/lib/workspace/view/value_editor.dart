import 'package:flutter/material.dart';
import 'dart:convert';
import '../../util/view/content_text_field.dart';
import '../../util/view/field_row.dart';

/// JSON is data here, not a third-party SDK model. API wrappers use generated mappers.
class ValueEditor extends StatelessWidget {
  final dynamic value;
  final ValueChanged<dynamic> onChanged;
  final String label;
  final bool readOnly;
  final int depth;
  const ValueEditor({
    required this.value,
    required this.onChanged,
    this.label = '',
    this.readOnly = false,
    this.depth = 0,
    super.key,
  });
  @override
  Widget build(BuildContext context) {
    if (!readOnly && value is String) {
      final text = (value as String).trim();
      if (text.startsWith('{') || text.startsWith('[')) {
        try {
          final structured = jsonDecode(text);
          return ValueEditor(
            value: structured,
            label: label,
            depth: depth,
            onChanged: (next) => onChanged(jsonEncode(next)),
          );
        } on FormatException {
          // Incomplete JSON and ordinary prose remain editable text.
        }
      }
    }
    if (value is Map) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final entry in (value as Map).entries)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: _container(context, entry.key.toString(), entry.value, (
                next,
              ) {
                final copy = Map<String, dynamic>.from(value);
                copy[entry.key] = next;
                onChanged(copy);
              }),
            ),
        ],
      );
    }
    if (value is List) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (var index = 0; index < (value as List).length; index++)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (!readOnly)
                      Align(
                        alignment: Alignment.centerRight,
                        child: IconButton(
                          tooltip: 'Remove item',
                          onPressed: () {
                            final next = List.of(value);
                            next.removeAt(index);
                            onChanged(next);
                          },
                          icon: const Icon(Icons.remove_circle_outline),
                        ),
                      ),
                    ValueEditor(
                      key: ValueKey('$label-$index'),
                      value: value[index],
                      label: label,
                      onChanged: (next) {
                        final copy = List.of(value);
                        copy[index] = next;
                        onChanged(copy);
                      },
                      readOnly: readOnly,
                      depth: depth + 1,
                    ),
                  ],
                ),
              ),
            ),
          if (!readOnly)
            TextButton.icon(
              onPressed: () {
                final next = List.of(value);
                next.add(
                  value.isNotEmpty && value.first is Map
                      ? <String, dynamic>{}
                      : '',
                );
                onChanged(next);
              },
              icon: const Icon(Icons.add),
              label: const Text('Add item'),
            ),
        ],
      );
    }
    if (value is bool) {
      return FieldRow(
        label: label,
        child: Align(
          alignment: Alignment.centerLeft,
          child: Switch(value: value, onChanged: readOnly ? null : onChanged),
        ),
      );
    }
    if (value is! num) {
      return ContentTextField(
        label: label,
        value: value?.toString() ?? '',
        readOnly: readOnly,
        onChanged: onChanged,
      );
    }
    return FieldRow(
      label: label,
      child: TextFormField(
        key: ValueKey('$label-${value.runtimeType}'),
        readOnly: readOnly,
        initialValue: value?.toString() ?? '',
        decoration: const InputDecoration(),
        minLines: 1,
        maxLines: 1,
        keyboardType: const TextInputType.numberWithOptions(
          decimal: true,
          signed: true,
        ),
        onChanged: readOnly
            ? null
            : (text) => onChanged(
                value is int
                    ? int.tryParse(text) ?? text
                    : value is num
                    ? double.tryParse(text) ?? text
                    : text,
              ),
      ),
    );
  }

  Widget _container(
    BuildContext context,
    String key,
    dynamic child,
    ValueChanged<dynamic> update,
  ) {
    final editor = ValueEditor(
      value: child,
      label: key,
      onChanged: update,
      readOnly: readOnly || key == 'characterId',
      depth: depth + 1,
    );
    if (child is Map || child is List) {
      return ExpansionTile(
        key: PageStorageKey('$depth-$label-$key'),
        title: Text(key, style: const TextStyle(fontWeight: FontWeight.w600)),
        initiallyExpanded: depth < 1,
        childrenPadding: const EdgeInsets.all(8),
        children: [editor],
      );
    }
    return editor;
  }
}
