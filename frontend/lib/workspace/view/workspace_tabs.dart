import 'package:flutter/material.dart';

class WorkspaceTabs extends StatefulWidget {
  final List<String> labels;
  final int selectedIndex;
  final ValueChanged<int> onChanged;
  const WorkspaceTabs({
    required this.labels,
    required this.selectedIndex,
    required this.onChanged,
    super.key,
  });

  @override
  State<WorkspaceTabs> createState() => _WorkspaceTabsState();
}

class _WorkspaceTabsState extends State<WorkspaceTabs>
    with SingleTickerProviderStateMixin {
  late TabController controller;

  @override
  void initState() {
    super.initState();
    controller = TabController(
      length: widget.labels.length,
      initialIndex: widget.selectedIndex,
      vsync: this,
    );
  }

  @override
  void didUpdateWidget(covariant WorkspaceTabs oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (controller.index != widget.selectedIndex) {
      controller.animateTo(widget.selectedIndex);
    }
  }

  @override
  Widget build(BuildContext context) => TabBar(
    controller: controller,
    isScrollable: true,
    tabAlignment: TabAlignment.start,
    labelColor: Theme.of(context).colorScheme.primary,
    unselectedLabelColor: Theme.of(context).colorScheme.onSurfaceVariant,
    indicatorColor: Theme.of(context).colorScheme.primary,
    onTap: widget.onChanged,
    tabs: [for (final label in widget.labels) Tab(text: label)],
  );

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }
}
