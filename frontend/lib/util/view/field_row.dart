import 'package:flutter/material.dart';

/// Aligns field labels consistently without shrinking the value to its content.
class FieldRow extends StatelessWidget {
  final String label;
  final Widget child;
  const FieldRow({required this.label, required this.child, super.key});

  @override
  Widget build(BuildContext context) {
    if (label.isEmpty) return child;
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = (constraints.maxWidth * .3).clamp(0.0, 200.0);
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: width,
              child: Padding(
                padding: const EdgeInsets.only(top: 12),
                child: ExcludeSemantics(
                  child: Text(
                    '${label.replaceFirst(RegExp(r':\s*$'), '')}:',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Semantics(label: label, child: child),
            ),
          ],
        );
      },
    );
  }
}
