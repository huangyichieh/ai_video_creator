import 'dart:convert';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../controller/jobs_controller.dart';
import '../model/job.dart';
import '../service/api_client.dart';

const taskCategories = [
  'All',
  'Story',
  'Characters',
  'Background',
  'Props',
  'Audio',
  'Animation',
  'Final',
  'Other',
];

String taskCategory(Job job) {
  final text = '${job.operation} ${job.arguments?['stage'] ?? ''}'
      .toLowerCase();
  if (text.contains('character') ||
      text.contains('costume') ||
      text.contains('voice_profile')) {
    return 'Characters';
  }
  if (text.contains('background') ||
      text.contains('location') ||
      text.contains('floor_plan') ||
      text.contains('layout') ||
      text.contains('panorama')) {
    return 'Background';
  }
  if (text.contains('prop')) return 'Props';
  if (text.contains('voice') ||
      text.contains('sfx') ||
      text.contains('music') ||
      text.contains('bgm') ||
      text.contains('audio')) {
    return 'Audio';
  }
  if (text.contains('final') ||
      text.contains('render') ||
      text.contains('publish')) {
    return 'Final';
  }
  if (text.contains('animation') ||
      text.contains('scene') ||
      text.contains('performance') ||
      text.contains('direction') ||
      text.contains('video') ||
      text.contains('vfx') ||
      text.contains('lip_sync') ||
      text.contains('continuity')) {
    return 'Animation';
  }
  if (text.contains('world') ||
      text.contains('story') ||
      text.contains('episode') ||
      text.contains('creative') ||
      text.contains('pipeline')) {
    return 'Story';
  }
  return 'Other';
}

String taskStatus(Job job) => switch (job.status) {
  'queued' => 'Queued',
  'running' => 'Generating…',
  'stopping' => 'Stopping after current task',
  'succeeded' => 'Succeeded',
  'failed' => 'Failed',
  'canceled' || 'cancelled' => 'Canceled',
  _ => job.status,
};

double? taskProgress(Job job) {
  final raw = job.progress?['percent'];
  final value = raw is num ? raw.toDouble() : double.tryParse('$raw');
  return value == null || !value.isFinite ? null : (value / 100).clamp(0, 1);
}

class TaskMenuButton extends StatelessWidget {
  final bool compact;
  const TaskMenuButton({this.compact = false, super.key});
  @override
  Widget build(BuildContext context) {
    final jobs = Get.isRegistered<JobsController>()
        ? Get.find<JobsController>()
        : null;
    Widget button(int active) => IconButton(
      key: const ValueKey('task-menu-button'),
      tooltip: 'Generation tasks · $active progressing',
      constraints: compact
          ? const BoxConstraints.tightFor(width: 36, height: 40)
          : null,
      padding: compact ? const EdgeInsets.all(6) : const EdgeInsets.all(8),
      icon: Badge(
        isLabelVisible: active > 0,
        label: Text('$active'),
        backgroundColor: Theme.of(context).colorScheme.primary,
        textColor: Theme.of(context).colorScheme.onPrimary,
        child: const Icon(Icons.assignment_outlined),
      ),
      onPressed: jobs == null
          ? null
          : () => Get.dialog(
              TaskPanel(jobs),
              barrierDismissible: true,
              barrierColor: Colors.black54,
            ),
    );
    return jobs == null
        ? button(0)
        : Obx(() => button(jobs.jobs.where((job) => job.active).length));
  }
}

class TaskPanel extends StatefulWidget {
  final JobsController controller;
  const TaskPanel(this.controller, {super.key});
  @override
  State<TaskPanel> createState() => _TaskPanelState();
}

class _TaskPanelState extends State<TaskPanel> {
  String category = 'All';
  bool refreshing = false;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => refresh());
  }

  Future<void> refresh() async {
    if (!mounted || refreshing) return;
    setState(() => refreshing = true);
    await widget.controller.refreshJobs();
    if (mounted) setState(() => refreshing = false);
  }

  @override
  Widget build(BuildContext context) {
    final screen = MediaQuery.sizeOf(context);
    return SafeArea(
      child: Align(
        alignment: Alignment.topRight,
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: Material(
            color: Theme.of(context).colorScheme.surfaceContainerHigh,
            borderRadius: BorderRadius.circular(24),
            clipBehavior: Clip.antiAlias,
            child: SizedBox(
              width: screen.width < 700
                  ? screen.width - 16
                  : (screen.width * .58).clamp(600, 1000),
              height: math.max(
                0,
                screen.height - MediaQuery.paddingOf(context).vertical - 16,
              ),
              child: Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(20, 12, 8, 8),
                    child: Row(
                      children: [
                        const Icon(Icons.task_alt),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            'Generation tasks',
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                        ),
                        IconButton(
                          tooltip: 'Refresh tasks',
                          onPressed: refreshing ? null : refresh,
                          icon: refreshing
                              ? const SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Icon(Icons.refresh),
                        ),
                        IconButton(
                          tooltip: 'Close tasks',
                          onPressed: () => Get.back(),
                          icon: const Icon(Icons.close),
                        ),
                      ],
                    ),
                  ),
                  DefaultTabController(
                    length: taskCategories.length,
                    child: TabBar(
                      isScrollable: true,
                      tabAlignment: TabAlignment.start,
                      onTap: (index) =>
                          setState(() => category = taskCategories[index]),
                      tabs: [
                        for (final name in taskCategories) Tab(text: name),
                      ],
                    ),
                  ),
                  Expanded(
                    child: Obx(() {
                      final jobs =
                          widget.controller.jobs
                              .where(
                                (job) =>
                                    category == 'All' ||
                                    taskCategory(job) == category,
                              )
                              .toList()
                            ..sort((a, b) {
                              if (a.active != b.active) {
                                return a.active ? -1 : 1;
                              }
                              return b.createdAt.compareTo(a.createdAt);
                            });
                      return Column(
                        children: [
                          if (widget.controller.errorMessage.value != null)
                            Padding(
                              padding: const EdgeInsets.all(16),
                              child: Text(
                                widget.controller.errorMessage.value!,
                                style: TextStyle(
                                  color: Theme.of(context).colorScheme.error,
                                ),
                              ),
                            ),
                          Padding(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 20,
                              vertical: 8,
                            ),
                            child: Align(
                              alignment: Alignment.centerLeft,
                              child: Text(
                                '${jobs.where((job) => job.active).length} progressing · ${jobs.length} tasks',
                              ),
                            ),
                          ),
                          Expanded(
                            child: jobs.isEmpty
                                ? const Center(
                                    child: Text('No tasks in this category.'),
                                  )
                                : ListView.builder(
                                    padding: const EdgeInsets.fromLTRB(
                                      16,
                                      0,
                                      16,
                                      16,
                                    ),
                                    itemCount: jobs.length,
                                    itemBuilder: (context, index) => _TaskCard(
                                      key: ValueKey(jobs[index].id),
                                      job: jobs[index],
                                      controller: widget.controller,
                                    ),
                                  ),
                          ),
                        ],
                      );
                    }),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _TaskCard extends StatelessWidget {
  final Job job;
  final JobsController controller;
  const _TaskCard({required this.job, required this.controller, super.key});
  @override
  Widget build(BuildContext context) {
    final color = job.status == 'failed'
        ? Theme.of(context).colorScheme.error
        : Theme.of(context).colorScheme.primary;
    final percent = taskProgress(job);
    final model = job.arguments?['model'] ?? job.progress?['model'];
    final subject =
        job.arguments?['subject'] ??
        job.arguments?['subject_id'] ??
        job.arguments?['scene_id'] ??
        job.arguments?['character_id'];
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: Theme.of(context).colorScheme.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        key: PageStorageKey('task-${job.id}'),
        shape: const Border(),
        collapsedShape: const Border(),
        leading: Icon(
          job.status == 'failed'
              ? Icons.error_outline
              : job.status == 'succeeded'
              ? Icons.check_circle_outline
              : job.status == 'canceled' || job.status == 'cancelled'
              ? Icons.cancel_outlined
              : Icons.pending_actions,
          color: color,
        ),
        title: Text('${taskCategory(job)} · ${taskStatus(job)}'),
        subtitle: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                '${job.operation} · ${job.id.substring(0, math.min(8, job.id.length))}${subject == null ? '' : ' · $subject'}',
              ),
              if (model != null) Text('Model: $model'),
              if (job.active) ...[
                const SizedBox(height: 8),
                Text(
                  '${job.progress?['stage'] ?? taskStatus(job)}${percent == null ? '' : ' · ${(percent * 100).round()}%'}',
                ),
                const SizedBox(height: 6),
                LinearProgressIndicator(
                  value: percent ?? (job.status == 'queued' ? 0 : null),
                ),
              ],
            ],
          ),
        ),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SelectableText(
                  key: PageStorageKey('task-metadata-${job.id}'),
                  'Project: ${job.projectId}\nTask ID: ${job.id}\nCreated: ${job.createdAt}\nUpdated: ${job.updatedAt}',
                ),
                if (job.error != null)
                  SelectableText(
                    key: PageStorageKey('task-error-${job.id}'),
                    job.error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                Wrap(
                  spacing: 8,
                  children: [
                    if (job.active)
                      TextButton.icon(
                        onPressed: job.status == 'stopping'
                            ? null
                            : () => controller.stop(job.id),
                        icon: const Icon(Icons.stop_circle_outlined),
                        label: const Text('Stop after current task'),
                      ),
                    TextButton.icon(
                      onPressed: () => report(job),
                      icon: const Icon(Icons.open_in_new),
                      label: Text(
                        job.error == null ? 'Browse result' : 'Error report',
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  void report(Job job) {
    // Open first; retrieve details after the dialog is mounted.
    Get.dialog(
      AlertDialog(
        title: Text(job.error == null ? 'Task result' : 'Error report'),
        content: SizedBox(width: 600, child: _TaskReport(job)),
        actions: [
          TextButton(onPressed: () => Get.back(), child: const Text('Close')),
        ],
      ),
    );
  }
}

class _TaskReport extends StatefulWidget {
  final Job job;
  const _TaskReport(this.job);
  @override
  State<_TaskReport> createState() => _TaskReportState();
}

class _TaskReportState extends State<_TaskReport> {
  late final Future<dynamic> data;
  @override
  void initState() {
    super.initState();
    final job = widget.job;
    data = Future(() async {
      final api = Get.find<ApiClient>();
      return job.error == null
          ? (await api.request('GET', '/api/jobs/${job.id}'))['result']
          : await api.request('GET', '/api/projects/${job.projectId}/errors');
    });
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<dynamic>(
    future: data,
    builder: (context, snapshot) =>
        snapshot.connectionState != ConnectionState.done
        ? const SizedBox(
            height: 100,
            child: Center(child: CircularProgressIndicator()),
          )
        : SingleChildScrollView(
            child: SelectableText(
              const JsonEncoder.withIndent('  ').convert(
                snapshot.data ??
                    snapshot.error?.toString() ??
                    widget.job.error ??
                    'No result.',
              ),
            ),
          ),
  );
}
