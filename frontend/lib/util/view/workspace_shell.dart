import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../projects/controller/project_controller.dart';
import '../../projects/view/project_dialogs.dart';
import '../../settings/view.dart';
import '../../workspace/controller/workspace_controller.dart';

class WorkspaceShell extends StatefulWidget {
  final String title;
  final String? page, projectId;
  final WorkspaceController? controller;
  final Widget body;
  final Widget? floatingActionButton;
  const WorkspaceShell({
    required this.title,
    required this.body,
    this.page,
    this.projectId,
    this.controller,
    this.floatingActionButton,
    super.key,
  });
  @override
  State<WorkspaceShell> createState() => _WorkspaceShellState();
}

class _WorkspaceShellState extends State<WorkspaceShell> {
  bool expanded = false;
  @override
  void initState() {
    super.initState();
    remember();
  }

  @override
  void didUpdateWidget(covariant WorkspaceShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.projectId != widget.projectId ||
        oldWidget.page != widget.page) {
      remember();
    }
  }

  void remember() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || widget.projectId == null || widget.page == null) return;
      Get.find<ProjectController>().rememberProject(
        widget.projectId!,
        widget.page!,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final projects = Get.find<ProjectController>();
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: false,
        titleSpacing: 8,
        title: LayoutBuilder(
          builder: (context, constraints) {
            final side = constraints.maxWidth * .34;
            final compact = constraints.maxWidth < 800;
            return Row(
              children: [
                SizedBox(
                  width: side,
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: projectMenu(projects),
                  ),
                ),
                Expanded(
                  child: Text(
                    widget.title,
                    key: const ValueKey('workspace-title'),
                    textAlign: TextAlign.center,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                SizedBox(
                  width: side,
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      Obx(() {
                        final ready =
                            projects.selectedProjectId.value.isNotEmpty &&
                            widget.controller?.snapshot.value != null;
                        void open() => Get.dialog(
                          GenerationPreferencesDialog(widget.controller!),
                        );
                        if (compact) {
                          return IconButton(
                            tooltip: 'Generation preferences',
                            onPressed: ready ? open : null,
                            icon: const Icon(Icons.tune),
                          );
                        }
                        return TextButton(
                          onPressed: ready ? open : null,
                          child: const Text('Generation preferences'),
                        );
                      }),
                      IconButton(
                        tooltip: 'API key',
                        icon: const Icon(Icons.key),
                        onPressed: () => Get.dialog(const KeyDialog()),
                      ),
                    ],
                  ),
                ),
              ],
            );
          },
        ),
      ),
      floatingActionButton: widget.floatingActionButton,
      body: Stack(
        children: [
          Positioned.fill(
            child: Padding(
              padding: const EdgeInsets.only(left: 64),
              child: widget.body,
            ),
          ),
          Positioned(
            left: 0,
            top: 0,
            bottom: 0,
            child: MouseRegion(
              onEnter: (_) => setState(() => expanded = true),
              onExit: (_) => setState(() => expanded = false),
              child: AnimatedContainer(
                key: const ValueKey('workspace-rail'),
                duration: const Duration(milliseconds: 140),
                width: expanded ? 208 : 64,
                clipBehavior: Clip.hardEdge,
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surface,
                  boxShadow: expanded
                      ? const [BoxShadow(blurRadius: 12, color: Colors.black38)]
                      : [],
                ),
                child: OverflowBox(
                  alignment: Alignment.topLeft,
                  minWidth: 208,
                  maxWidth: 208,
                  child: SizedBox(
                    width: 208,
                    child: Column(
                      children: [
                        Expanded(
                          child: SingleChildScrollView(
                            child: Column(
                              children: [
                                const SizedBox(height: 8),
                                for (final entry in const [
                                  ('world', 'World', Icons.public),
                                  (
                                    'assets',
                                    'Assets',
                                    Icons.collections_outlined,
                                  ),
                                  (
                                    'story',
                                    'Story/Episode',
                                    Icons.menu_book_outlined,
                                  ),
                                  (
                                    'animations',
                                    'Animation',
                                    Icons.movie_creation_outlined,
                                  ),
                                  (
                                    'final',
                                    'Final',
                                    Icons.video_library_outlined,
                                  ),
                                  ('analytics', 'Analytics', Icons.bar_chart),
                                ])
                                  Obx(() {
                                    final selected = widget.page == entry.$1;
                                    final enabled =
                                        projects.selectedProject != null;
                                    final color = selected
                                        ? Theme.of(context).colorScheme.primary
                                        : enabled
                                        ? Theme.of(
                                            context,
                                          ).colorScheme.onSurfaceVariant
                                        : Theme.of(context).disabledColor;
                                    return Semantics(
                                      button: true,
                                      label: entry.$2,
                                      selected: selected,
                                      enabled: enabled,
                                      excludeSemantics: true,
                                      onTap: enabled
                                          ? () =>
                                                projects.openWorkspace(entry.$1)
                                          : null,
                                      child: Tooltip(
                                        message: entry.$2,
                                        child: Material(
                                          color: selected
                                              ? Theme.of(
                                                  context,
                                                ).colorScheme.primaryContainer
                                              : Colors.transparent,
                                          child: InkWell(
                                            key: ValueKey(
                                              'workspace-${entry.$1}',
                                            ),
                                            onTap: enabled
                                                ? () => projects.openWorkspace(
                                                    entry.$1,
                                                  )
                                                : null,
                                            child: SizedBox(
                                              height: 56,
                                              child: Row(
                                                children: [
                                                  SizedBox(
                                                    width: 64,
                                                    child: Icon(
                                                      entry.$3,
                                                      color: color,
                                                    ),
                                                  ),
                                                  Expanded(
                                                    child: Text(
                                                      entry.$2,
                                                      style: TextStyle(
                                                        color: color,
                                                      ),
                                                    ),
                                                  ),
                                                ],
                                              ),
                                            ),
                                          ),
                                        ),
                                      ),
                                    );
                                  }),
                              ],
                            ),
                          ),
                        ),
                        Align(
                          alignment: Alignment.centerLeft,
                          child: SizedBox(
                            width: 64,
                            child: IconButton(
                              tooltip: expanded
                                  ? 'Collapse navigation'
                                  : 'Expand navigation',
                              onPressed: () =>
                                  setState(() => expanded = !expanded),
                              icon: Icon(
                                expanded
                                    ? Icons.chevron_left
                                    : Icons.chevron_right,
                              ),
                            ),
                          ),
                        ),
                        const Divider(height: 1),
                        Obx(() {
                          final project = projects.selectedProject;
                          return Tooltip(
                            message: 'Remove project',
                            child: TextButton(
                              key: const ValueKey('remove-project-button'),
                              style: TextButton.styleFrom(
                                foregroundColor: const Color(0xffff6b6b),
                                padding: EdgeInsets.zero,
                                minimumSize: const Size(208, 48),
                                alignment: Alignment.centerLeft,
                              ),
                              onPressed: project == null
                                  ? null
                                  : () => removeProject(
                                      projects,
                                      project.projectId,
                                      project.title,
                                    ),
                              child: const Row(
                                children: [
                                  SizedBox(
                                    width: 64,
                                    child: Icon(Icons.delete_outline),
                                  ),
                                  Expanded(
                                    child: Text(
                                      'Remove project',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          );
                        }),
                        const SizedBox(height: 8),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget projectMenu(ProjectController projects) => Obx(
    () => MenuAnchor(
      style: const MenuStyle(
        maximumSize: WidgetStatePropertyAll(Size(360, 480)),
      ),
      menuChildren: [
        MenuItemButton(
          leadingIcon: const Icon(Icons.add),
          onPressed: () => createWorld(projects),
          child: const Text('Add project'),
        ),
        const Divider(),
        if (projects.loading.value && projects.projects.isEmpty)
          const MenuItemButton(child: Text('Loading projects…')),
        if (!projects.loading.value && projects.projects.isEmpty)
          const MenuItemButton(child: Text('No projects yet')),
        for (final project in projects.projects.where(
          (p) => p.worldProjectId == null,
        ))
          MenuItemButton(
            leadingIcon: Icon(
              project.projectId == projects.selectedProjectId.value
                  ? Icons.check
                  : Icons.folder_outlined,
            ),
            onPressed: () => projects.openProject(project.projectId),
            child: SizedBox(
              width: 200,
              child: Text(
                project.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ),
      ],
      builder: (context, menu, child) => TextButton.icon(
        key: const ValueKey('project-menu-button'),
        onPressed: () => menu.isOpen ? menu.close() : menu.open(),
        icon: const Icon(Icons.folder_open),
        label: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                projects.selectedProject?.title ?? '-',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const Icon(Icons.arrow_drop_down),
          ],
        ),
      ),
    ),
  );
}
