import 'package:flutter/material.dart';
import '../controller/world_settings_controller.dart';
import '../model/world_settings.dart';

/// Line-based diff with a bounded LCS table for normal worldview paragraphs.
({Set<int> oldLines, Set<int> newLines}) changedLines(
  String before,
  String after,
) {
  final a = before.split('\n'), b = after.split('\n');
  if (a.length * b.length > 200000) {
    return (
      oldLines: {
        for (var i = 0; i < a.length; i++)
          if (i >= b.length || a[i] != b[i]) i,
      },
      newLines: {
        for (var i = 0; i < b.length; i++)
          if (i >= a.length || b[i] != a[i]) i,
      },
    );
  }
  final table = List.generate(
    a.length + 1,
    (_) => List.filled(b.length + 1, 0),
  );
  for (var i = a.length - 1; i >= 0; i--) {
    for (var j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] == b[j]
          ? 1 + table[i + 1][j + 1]
          : table[i + 1][j] > table[i][j + 1]
          ? table[i + 1][j]
          : table[i][j + 1];
    }
  }
  final oldChanged = <int>{}, newChanged = <int>{};
  var i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] == b[j]) {
      i++;
      j++;
    } else if (j < b.length &&
        (i == a.length || table[i][j + 1] >= table[i + 1][j])) {
      newChanged.add(j++);
    } else {
      oldChanged.add(i++);
    }
  }
  return (oldLines: oldChanged, newLines: newChanged);
}

TextSpan diffSpan(
  String text,
  Set<int> changed,
  Color color,
  TextStyle? style,
) {
  final lines = text.split('\n');
  return TextSpan(
    style: style,
    children: [
      for (var i = 0; i < lines.length; i++)
        TextSpan(
          text: lines[i] + (i < lines.length - 1 ? '\n' : ''),
          style: changed.contains(i) ? TextStyle(backgroundColor: color) : null,
        ),
    ],
  );
}

class _DiffController extends TextEditingController {
  String original;
  _DiffController(this.original, String value) : super(text: value);
  @override
  TextSpan buildTextSpan({
    required BuildContext context,
    TextStyle? style,
    required bool withComposing,
  }) {
    if (withComposing &&
        value.composing.isValid &&
        !value.composing.isCollapsed) {
      return super.buildTextSpan(
        context: context,
        style: style,
        withComposing: withComposing,
      );
    }
    return diffSpan(
      text,
      changedLines(original, text).newLines,
      const Color(0xff164631),
      style,
    );
  }
}

class DiffTextField extends StatefulWidget {
  final String original, value, label;
  final bool readOnly, dirty;
  final bool enabled;
  final ValueChanged<String> onChanged;
  const DiffTextField({
    required this.original,
    required this.value,
    required this.label,
    required this.readOnly,
    required this.dirty,
    required this.onChanged,
    this.enabled = true,
    super.key,
  });
  @override
  State<DiffTextField> createState() => _DiffTextFieldState();
}

class _DiffTextFieldState extends State<DiffTextField> {
  late final _DiffController controller;
  @override
  void initState() {
    super.initState();
    controller = _DiffController(widget.original, widget.value);
  }

  @override
  void didUpdateWidget(covariant DiffTextField oldWidget) {
    super.didUpdateWidget(oldWidget);
    controller.original = widget.original;
    if (controller.text != widget.value) {
      controller.value = TextEditingValue(
        text: widget.value,
        selection: TextSelection.collapsed(offset: widget.value.length),
      );
    }
  }

  @override
  Widget build(BuildContext context) => Semantics(
    label: widget.label,
    child: widget.readOnly
        ? Padding(
            padding: const EdgeInsets.all(12),
            child: SelectableText.rich(
              diffSpan(
                widget.value,
                changedLines(widget.original, widget.value).newLines,
                const Color(0xff164631),
                Theme.of(context).textTheme.bodyMedium,
              ),
            ),
          )
        : TextField(
            controller: controller,
            enabled: widget.enabled,
            minLines: 3,
            maxLines: 14,
            decoration: InputDecoration(
              enabledBorder: widget.dirty
                  ? const OutlineInputBorder(
                      borderSide: BorderSide(
                        color: Color(0xff64b5f6),
                        width: 1.5,
                      ),
                    )
                  : null,
              focusedBorder: const OutlineInputBorder(
                borderSide: BorderSide(color: Color(0xff64b5f6), width: 2),
              ),
            ),
            onChanged: widget.onChanged,
          ),
  );
  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }
}

class WorldReviewDiff extends StatelessWidget {
  final WorldSettingsController controller;
  const WorldReviewDiff(this.controller, {super.key});
  @override
  Widget build(BuildContext context) {
    final review = controller.form.value!.review!;
    final locked = controller.approved || controller.busy || !review.current;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'AI Review suggestions',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 8),
        SelectableText(review.summary),
        for (final reason in review.reasons) Text('• $reason'),
        for (final conflict in review.conflicts)
          Text(
            conflict,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
        if (!review.current)
          const Text('This review is outdated. Run AI Review again.'),
        if (!controller.approved)
          Wrap(
            spacing: 8,
            children: [
              TextButton(
                onPressed: locked ? null : () => controller.chooseAll('apply'),
                child: const Text('Apply all'),
              ),
              TextButton(
                onPressed: locked ? null : () => controller.chooseAll('revert'),
                child: const Text('Revert all'),
              ),
              if (!controller.resolved)
                const Padding(
                  padding: EdgeInsets.all(12),
                  child: Text(
                    'Choose Apply or Revert for each change before approving.',
                  ),
                ),
            ],
          ),
        const Row(
          children: [
            Expanded(
              child: Text(
                'Old version',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: Color(0xffff8a8a),
                ),
              ),
            ),
            SizedBox(width: 12),
            Expanded(
              child: Text(
                'New version',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: Color(0xff81c784),
                ),
              ),
            ),
          ],
        ),
        for (final entry in worldFields.entries)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  '${entry.value}:',
                  style: const TextStyle(fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 4),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          color: const Color(0xff20242b),
                          border: Border.all(
                            color: Theme.of(context).colorScheme.outlineVariant,
                          ),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: SelectableText.rich(
                            diffSpan(
                              review.original.fields[entry.key]!,
                              changedLines(
                                review.original.fields[entry.key]!,
                                controller.proposed[entry.key]!,
                              ).oldLines,
                              const Color(0xff632e35),
                              Theme.of(context).textTheme.bodyMedium,
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          DiffTextField(
                            key: ValueKey('world-diff-${entry.key}'),
                            label: '${entry.value} new version',
                            original: review.original.fields[entry.key]!,
                            value: controller.proposed[entry.key]!,
                            readOnly: controller.approved || !review.current,
                            enabled: !controller.busy,
                            dirty:
                                controller.proposed[entry.key] !=
                                (review.accepted ?? review.original)
                                    .fields[entry.key],
                            onChanged: (text) =>
                                controller.editProposal(entry.key, text),
                          ),
                          if (controller.changedFields.contains(entry.key))
                            Wrap(
                              spacing: 8,
                              children: [
                                for (final choice in ['apply', 'revert'])
                                  TextButton.icon(
                                    onPressed: locked
                                        ? null
                                        : () => controller.choose(
                                            entry.key,
                                            choice,
                                          ),
                                    icon: Icon(
                                      controller.decisions[entry.key] == choice
                                          ? Icons.check_circle
                                          : Icons.circle_outlined,
                                      size: 18,
                                    ),
                                    label: Text(
                                      choice == 'apply' ? 'Apply' : 'Revert',
                                    ),
                                  ),
                              ],
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
      ],
    );
  }
}
