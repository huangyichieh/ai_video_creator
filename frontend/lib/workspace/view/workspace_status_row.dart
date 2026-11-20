import 'package:flutter/material.dart';

class WorkspaceStatusRow extends StatelessWidget {
  final String nextStep, cost;
  const WorkspaceStatusRow({
    required this.nextStep,
    required this.cost,
    super.key,
  });

  @override
  Widget build(BuildContext context) => DefaultTextStyle.merge(
    style: const TextStyle(fontSize: 18),
    child: Wrap(
      spacing: 12,
      runSpacing: 4,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Text('Next step: $nextStep'),
        const VerticalDivider(),
        Text('Project billed cost (USD): $cost'),
      ],
    ),
  );
}
