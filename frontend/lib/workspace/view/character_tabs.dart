import 'package:flutter/material.dart';

/// Show the selected tab at its natural height; the workspace owns scrolling.
class CharacterTabs extends StatefulWidget {
  final String storageId;
  final List<Widget> children;
  const CharacterTabs({
    required this.storageId,
    required this.children,
    super.key,
  }) : assert(children.length == 3);

  @override
  State<CharacterTabs> createState() => _CharacterTabsState();
}

class _CharacterTabsState extends State<CharacterTabs>
    with SingleTickerProviderStateMixin {
  late final TabController controller;
  Object get tabKey => ValueKey('${widget.storageId}-selected-tab');

  @override
  void initState() {
    super.initState();
    controller = TabController(length: 3, vsync: this);
    controller.addListener(rememberTab);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final selected = PageStorage.maybeOf(
      context,
    )?.readState(context, identifier: tabKey);
    if (selected is int && selected >= 0 && selected < 3) {
      controller.index = selected;
    }
  }

  void rememberTab() {
    PageStorage.maybeOf(
      context,
    )?.writeState(context, controller.index, identifier: tabKey);
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      TabBar(
        controller: controller,
        tabs: const [
          Tab(text: 'Visual'),
          Tab(text: 'Voice'),
          Tab(text: 'Costumes'),
        ],
      ),
      AnimatedBuilder(
        animation: controller,
        builder: (context, _) => KeyedSubtree(
          key: PageStorageKey(
            '${widget.storageId}-tab-${controller.index}-content',
          ),
          child: widget.children[controller.index],
        ),
      ),
    ],
  );

  @override
  void dispose() {
    controller.removeListener(rememberTab);
    controller.dispose();
    super.dispose();
  }
}
