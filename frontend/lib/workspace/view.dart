import '../util/view/field_row.dart';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../projects/controller/project_controller.dart';
import '../util/controller/jobs_controller.dart';
import '../util/model/app_route_parameters.dart';
import '../util/service/api_client.dart';
import '../util/view/dialog.dart';
import '../util/view/frame.dart';
import '../util/view/workspace_shell.dart';
import '../util/view/loading.dart';
import 'controller/workspace_controller.dart';
import 'model/workspace_snapshot.dart';
import 'service/workspace_service.dart';
import 'view/action_dialog.dart';
import 'view/artifact_card.dart';
import 'view/media_card.dart';
import 'view/value_editor.dart';
import 'view/review_dialog.dart';
import 'view/asset_cards.dart';
import 'view/revision_dialog.dart';
import 'view/task_button.dart';
import 'view/library_cards.dart';
import 'view/scene_cards.dart';
import 'view/keyframes_dialog.dart';
import 'view/workspace_tabs.dart';

const workspaceNames = {
  'world': 'World',
  'assets': 'Assets',
  'story': 'Story/Episode',
  'animations': 'Animation',
  'final': 'Final',
  'analytics': 'Analytics',
};
const workspaceTabs = {
  'world': ['World settings', 'Additional settings'],
  'assets': [
    'Character References',
    'Background',
    'SFX',
    'BGM',
    'Props',
    'Style Check',
  ],
  'story': ['Outline', 'Story', 'Script', 'Storyboard'],
  'animations': [
    'Check assets',
    'Scene images',
    'Direction & performance',
    'Animated scenes',
    'Voices & input',
    'VFX',
    'SFX',
    'Scene Review & Continuity',
  ],
  'final': [
    'Timeline & Compositing',
    'Subtitle',
    'Audio mixing & Visual finishing',
    'Outputs & Publish approval',
  ],
  'analytics': ['Generation cost', 'Provider quality', 'Project & activity'],
};

class WorkspacePage extends StatelessWidget {
  final String page;
  const WorkspacePage(this.page, {super.key});
  @override
  Widget build(BuildContext context) {
    final parameters = Get.find<AppRouteParameters>();
    final initial = Get.arguments is String
        ? Get.arguments as String
        : Get.parameters['project'] ?? parameters.projectId;
    if (initial == null) {
      return WorkspaceShell(
        title: workspaceNames[page]!,
        page: page,
        body: Center(
          child: TextButton(
            onPressed: () => Get.offAllNamed('/'),
            child: const Text('Choose a project'),
          ),
        ),
      );
    }
    final tag = '$page-$initial';
    final controller = Get.isRegistered<WorkspaceController>(tag: tag)
        ? Get.find<WorkspaceController>(tag: tag)
        : Get.put(
            WorkspaceController(
              WorkspaceService(Get.find<ApiClient>()),
              page,
              initial,
            ),
            tag: tag,
            permanent: true,
          );
    return WorkspaceShell(
      title: workspaceNames[page]!,
      page: page,
      projectId: initial,
      controller: controller,
      floatingActionButton: FloatingActionButton(
        onPressed: () async {
          final error = await controller.loadWorkspace();
          if (error != null) Get.dialog(TextDialog('Refresh failed', error));
        },
        child: const Icon(Icons.refresh),
      ),
      body: AppFrame(
        child: Obx(() {
          final value = controller.snapshot.value;
          if (controller.loading.value && value == null) {
            return const AppLoadingIcon();
          }
          return ListView(
            children: [
              if (controller.errorMessage.value != null)
                Text(
                  controller.errorMessage.value!,
                  style: const TextStyle(color: Colors.red),
                ),
              if (value != null) ...[
                Text(
                  value.project.title,
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                Text(
                  '${value.project.projectId.substring(0, 8)} · ${value.project.language}',
                ),
                Text(
                  'Project billed cost (USD): ${value.computed['projectCost'] ?? '0.00'}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                if (value.project.worldProjectId != null)
                  Text(
                    'Target length: ${(value.project.targetDurationSec ~/ 3600).toString().padLeft(2, '0')}:${(value.project.targetDurationSec ~/ 60 % 60).toString().padLeft(2, '0')}:${(value.project.targetDurationSec % 60).toString().padLeft(2, '0')}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                if (page == 'world' || page == 'assets')
                  Wrap(
                    spacing: 8,
                    children: [
                      TextButton.icon(
                        onPressed: () => Get.find<ProjectController>()
                            .openWorkspace('world'),
                        icon: const Icon(Icons.public),
                        label: const Text('World'),
                      ),
                      TextButton.icon(
                        onPressed: () => Get.find<ProjectController>()
                            .openWorkspace('assets'),
                        icon: const Icon(Icons.collections_outlined),
                        label: const Text('Assets'),
                      ),
                    ],
                  ),
                episodeSelector(controller),
                const SizedBox(height: 8),
                jobs(controller),
                if (value.computed['nextAction'] is Map)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(8),
                      child: Text(
                        'Next step:\n${value.computed['nextAction']['title']}',
                      ),
                    ),
                  ),
                WorkspaceTabs(
                  key: ValueKey('workspace-tabs-$page'),
                  labels: workspaceTabs[page]!,
                  selectedIndex: controller.selectedTab.value,
                  onChanged: (index) => controller.selectedTab.value = index,
                ),
                const SizedBox(height: 8),
                quickActions(controller),
                ...content(controller, value),
                if (![
                  'VFX',
                  'SFX',
                  'Scene Review & Continuity',
                ].contains(workspaceTabs[page]![controller.selectedTab.value]))
                  TextButton.icon(
                    onPressed: () => chooseOperation(controller),
                    icon: const Icon(Icons.tune),
                    label: const Text('All workspace actions'),
                  ),
              ],
            ],
          );
        }),
      ),
    );
  }

  Widget episodeSelector(WorkspaceController controller) {
    if (!['story', 'animations', 'final'].contains(page)) {
      return const SizedBox.shrink();
    }
    final snapshot = controller.snapshot.value!;
    final owner = snapshot.project.worldProjectId ?? snapshot.project.projectId;
    final list =
        Get.find<ProjectController>().projects
            .where((item) => item.worldProjectId == owner)
            .toList()
          ..sort(
            (a, b) => (b.createdAt ?? b.updatedAt).compareTo(
              a.createdAt ?? a.updatedAt,
            ),
          );
    return Column(
      children: [
        if (list.isNotEmpty)
          FieldRow(
            label: 'Episode',
            child: DropdownButtonFormField<String>(
              key: ValueKey(controller.projectId.value),
              initialValue:
                  list.any((p) => p.projectId == controller.projectId.value)
                  ? controller.projectId.value
                  : null,
              decoration: const InputDecoration(),
              isExpanded: true,
              items: list
                  .map(
                    (item) => DropdownMenuItem(
                      value: item.projectId,
                      child: Text(
                        '${item.title} · ${page == 'animations' ? item.animationStatus : item.storyStatus}',
                      ),
                    ),
                  )
                  .toList(),
              onChanged: (id) {
                if (id != null) controller.selectEpisode(id);
              },
            ),
          ),
        if (page == 'story')
          TextButton.icon(
            onPressed: () => createEpisode(controller, owner),
            icon: const Icon(Icons.add),
            label: const Text('Add episode'),
          ),
      ],
    );
  }

  Widget jobs(WorkspaceController controller) => Obx(() {
    final service = Get.find<JobsController>();
    final list = service.jobs
        .where((job) => job.projectId == controller.projectId.value)
        .take(8)
        .toList();
    if (list.isEmpty) return const SizedBox.shrink();
    final active = list.where((job) => job.active).length;
    return ExpansionTile(
      key: PageStorageKey('jobs-${controller.projectId.value}'),
      title: Text('Tasks · $active progressing'),
      initiallyExpanded: active > 0,
      children: [
        for (final job in list)
          ListTile(
            dense: true,
            leading: job.active
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : Icon(
                    job.status == 'failed'
                        ? Icons.error_outline
                        : Icons.check_circle_outline,
                  ),
            title: Text(job.operation),
            subtitle: job.progress != null
                ? Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(
                        '${job.progress!['percent']}% · ${job.progress!['stage']}',
                      ),
                      LinearProgressIndicator(
                        value:
                            ((job.progress!['percent'] as num).toDouble() / 100)
                                .clamp(0, 1),
                      ),
                    ],
                  )
                : Text(
                    job.status == 'queued'
                        ? 'Queuing…'
                        : job.status == 'running'
                        ? 'Generating…'
                        : job.error ?? job.status,
                  ),
            trailing: job.active
                ? IconButton(
                    tooltip: 'Stop after current task',
                    onPressed: () => service.stop(job.id),
                    icon: const Icon(Icons.stop_circle_outlined),
                  )
                : IconButton(
                    tooltip: job.error != null
                        ? 'Error report'
                        : 'Browse result',
                    onPressed: () => Get.dialog(
                      AlertDialog(
                        title: Text(job.operation),
                        content: SizedBox(
                          width: 500,
                          child: SingleChildScrollView(
                            child: FutureBuilder<dynamic>(
                              future: job.error == null
                                  ? Get.find<ApiClient>()
                                        .request('GET', '/api/jobs/${job.id}')
                                        .then((value) => value['result'])
                                  : Get.find<ApiClient>().request(
                                      'GET',
                                      '/api/projects/${job.projectId}/errors',
                                    ),
                              builder: (context, result) =>
                                  result.connectionState != ConnectionState.done
                                  ? const Center(
                                      child: CircularProgressIndicator(),
                                    )
                                  : SelectableText(
                                      const JsonEncoder.withIndent(
                                        '  ',
                                      ).convert(
                                        result.data ??
                                            result.error?.toString() ??
                                            job.error,
                                      ),
                                    ),
                            ),
                          ),
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => Get.back(),
                            child: const Text('Close'),
                          ),
                        ],
                      ),
                    ),
                    icon: const Icon(Icons.open_in_new),
                  ),
          ),
      ],
    );
  });

  Widget quickActions(WorkspaceController controller) {
    final tab = workspaceTabs[page]![controller.selectedTab.value];
    final actions =
        <({String title, String operation, Map<String, dynamic> args})>[];
    void add(
      String title,
      String operation, [
      Map<String, dynamic> args = const {},
    ]) => actions.add((title: title, operation: operation, args: args));
    switch (page) {
      case 'world':
        if (tab == 'World settings') {
          add('AI Review', 'world.review');
          add('Apply / revert suggestion', 'world.resolve_base');
          add('Approve', 'world.approve_base');
          add('Rename World', 'world.rename');
        } else {
          add('Add setting', 'world.add_setting');
          add('AI Review setting', 'world.review');
          add('Approve setting', 'world.approve_setting');
        }
      case 'assets':
        add('Asset generation preferences', 'batch.asset_provider');
        if (tab == 'Character References') {
          if (controller.snapshot.value!.characters.isEmpty) {
            add('AI Generate initial characters', 'world.generate_characters');
          }
          add('Add character', 'world.generate_characters', {'single': true});
          add('Generate all Visual (Text)', 'assets.generate', {
            'stage': 'character_references',
          });
          add('Approve all characters', 'assets.approve', {
            'gate': 'characters',
          });
        } else if (tab == 'Background') {
          add('Add background', 'background.add');
          add('Approve all backgrounds', 'background.approve');
        } else if (tab == 'Props') {
          add('Add prop', 'library.add_prop');
        } else if (tab == 'SFX') {
          add('Add SFX', 'audio_assets.add_sfx');
        } else if (tab == 'BGM') {
          add('Add BGM', 'bgm_library.add');
        } else {
          add('Save style rules', 'style.save');
          add('Style image reference', 'style.set_image');
          add('Style check', 'style_check.check_style');
        }
      case 'story':
        if (tab != 'Outline') {
          add('Generate ${tab.toLowerCase()}', 'pipeline.generate', {
            'step': tab == 'Storyboard' ? 'storyboard' : tab.toLowerCase(),
          });
          add('AI Review', 'batch.review_episode', {'kind': tab.toLowerCase()});
          add('Modify with prompt', 'pipeline.modify_with_openai', {
            'step': tab.toLowerCase(),
          });
          if (tab == 'Script') {
            add('Approve script', 'studio.approve_content', {'name': 'script'});
          }
        } else {
          add('AI Review outline', 'batch.review_episode', {'kind': 'outline'});
          add('Modify outline with prompt', 'episode_reviews.review_episode', {
            'stage': 'outline',
          });
          add('Edit episode outline', 'batch.edit_outline');
          add(
            'Remove episode (type full title to confirm)',
            'batch.remove_episode',
          );
          add('Approve outline', 'studio.approve_content', {'name': 'outline'});
        }
        add('Generate next stage', 'pipeline.run');
      case 'animations':
        switch (tab) {
          case 'Scene images':
            add('Generate / resume scene images', 'assets.generate', {
              'stage': 'scene_images',
            });
          case 'Direction & performance':
            add('Prepare direction', 'animation.prepare');
          case 'Animated scenes':
            add('Generate all animated scenes', 'batch.animated_scenes');
          case 'Voices & input':
            add('Generate all voice tracks', 'assets.generate', {
              'stage': 'voices',
            });
            add('Align subtitles', 'assets.generate', {'stage': 'subtitles'});
            add('Scene-input QA', 'assets.generate', {'stage': 'consistency'});
            add('Approve scene inputs', 'assets.approve', {'gate': 'assets'});
          case 'Scene Review & Continuity':
            add('Review', 'scene_continuity_review.review');
          case 'Check assets':
            break;
        }
      case 'final':
        if (tab == 'Timeline & Compositing') {
          add('Compose scenes', 'render.generate_scenes');
          add('Approve finished scenes', 'render.approve_scenes');
        }
        if (tab == 'Audio mixing & Visual finishing') {
          add('Video / mix settings', 'render.configure');
          add('Effect settings', 'finishing.configure');
        }
        if (tab == 'Outputs & Publish approval') {
          add('Assemble final video', 'render.assemble_final');
          add('Approve final video', 'render.approve');
        }
    }
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        if (page == 'story')
          OutlinedButton(
            onPressed: () =>
                Get.dialog(ReviewDialog(controller, tab.toLowerCase())),
            child: const Text('Review suggestions / approve'),
          ),
        ...actions.map(
          (action) => TaskButton(
            controller,
            operation: action.operation,
            arguments: action.args,
            label: action.title,
            onPressed: () =>
                openAction(controller, action.operation, action.args),
          ),
        ),
      ],
    );
  }

  List<Widget> content(
    WorkspaceController controller,
    WorkspaceSnapshot value,
  ) {
    final tab = workspaceTabs[page]![controller.selectedTab.value];
    if (page == 'animations' &&
        [
          'Scene images',
          'Direction & performance',
          'Animated scenes',
        ].contains(tab)) {
      return [
        SceneCards(
          controller,
          value,
          tab,
          (op, args) => openAction(controller, op, args),
        ),
      ];
    }
    if (page == 'assets' && ['SFX', 'BGM', 'Props'].contains(tab)) {
      return [
        LibraryCards(
          controller,
          value,
          tab,
          (op, args) => openAction(controller, op, args),
        ),
      ];
    }
    if (page == 'assets' && tab == 'Character References') {
      return [
        CharacterCards(
          controller,
          value,
          (op, args) => openAction(controller, op, args),
        ),
      ];
    }
    if (page == 'assets' && tab == 'Background') {
      return [
        BackgroundCards(
          controller,
          value,
          (op, args) => openAction(controller, op, args),
        ),
      ];
    }
    if (page == 'animations' && ['VFX', 'SFX'].contains(tab)) {
      return [
        ValueEditor(
          value: value.computed[tab.toLowerCase()] ?? [],
          readOnly: true,
          onChanged: (_) {},
        ),
      ];
    }
    if (page == 'animations' && tab == 'Check assets') {
      return [
        for (final row in (value.computed['assetCheck'] as List? ?? []))
          Card(
            color: row['Ready'] == true
                ? const Color(0xff173f32)
                : const Color(0xff51272d),
            child: ListTile(
              leading: Icon(
                Icons.circle,
                size: 12,
                color: row['Ready'] == true ? Colors.green : Colors.red,
              ),
              title: Text(row['Asset']?.toString() ?? ''),
              subtitle: Text('${row['Category']} · ${row['Status']}'),
            ),
          ),
        TextButton(
          onPressed: () => chooseOperation(controller),
          child: const Text('Create / edit required assets'),
        ),
        for (final kind in ['Props', 'SFX', 'BGM'])
          LibraryCards(
            controller,
            value,
            kind,
            (op, args) => openAction(controller, op, args),
            selected: (value.computed['assetCheck'] as List? ?? [])
                .where((row) => row['Category'] == kind)
                .map((row) => row['ID'].toString())
                .toSet(),
          ),
      ];
    }
    if (page == 'animations' && tab == 'Voices & input') {
      return [
        for (final row in (value.computed['voiceTracks'] as List? ?? []))
          _VoiceLineCard(controller, Map<String, dynamic>.from(row)),
        ...value.sections
            .where((s) => s.id == 'production.json')
            .map((s) => ArtifactCard(controller, s)),
      ];
    }
    if (page == 'animations' && tab == 'Scene Review & Continuity') {
      return [
        const Text(
          'Review samples every animated scene in story order at approximately one frame per second. Findings include timestamps; audio and between-frame glitches are not checked.',
        ),
        ...value.sections
            .where((s) => s.id == 'animation_continuity_review.json')
            .map((s) => ArtifactCard(controller, s)),
      ];
    }
    final ids = switch (page) {
      'world' => ['world_setting.json'],
      'assets' => switch (tab) {
        'Character References' => ['characters.json', 'library_media.json'],
        'Background' => ['background_library.json', 'production.json'],
        'SFX' => ['sfx_library.json'],
        'BGM' => ['bgm_library.json'],
        'Props' => ['library_media.json'],
        _ => ['production.json', 'style_check.json'],
      },
      'story' => switch (tab) {
        'Outline' => ['episode_outline.json', 'outline.json'],
        'Story' => ['story.json'],
        'Script' => ['script.json'],
        _ => ['scenes.json'],
      },
      'animations' => switch (tab) {
        'Scene images' => [
          'scenes.json',
          'production.json',
          'scene_image_keyframes.json',
        ],
        'Direction & performance' => ['animation.json'],
        _ => ['visual_scenes.json'],
      },
      'final' => switch (tab) {
        'Subtitle' => ['production.json'],
        'Audio mixing & Visual finishing' => ['finishing.json', 'render.json'],
        _ => ['render.json'],
      },
      _ => ['manifest.json', 'usage.json'],
    };
    return [
      if (page == 'analytics')
        ValueEditor(
          value: value.computed['costRecords'] ?? [],
          readOnly: true,
          onChanged: (_) {},
        ),
      for (final section in value.sections.where((s) => ids.contains(s.id)))
        ArtifactCard(controller, section),
      if (page == 'animations' && tab == 'Animated scenes')
        ...animatedCards(controller, value),
      ExpansionTile(
        key: PageStorageKey('media-${controller.projectId.value}-$tab'),
        title: const Text('Images / audio / video'),
        children: [
          for (final media in value.media)
            MediaCard(controller.projectId.value, media),
        ],
      ),
    ];
  }

  List<Widget> animatedCards(
    WorkspaceController controller,
    WorkspaceSnapshot value,
  ) {
    final section = value.sections.firstWhereOrNull(
      (s) => s.id == 'visual_scenes.json',
    );
    if (section?.data is! Map) return [];
    return [
      for (final entry in (section!.data as Map).entries)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  '${entry.key} · ${entry.value['status']} · revision ${entry.value['revision']}',
                  style: const TextStyle(fontWeight: FontWeight.bold),
                ),
                for (final media
                    in value.media
                        .where(
                          (m) =>
                              m.file.contains('/${entry.key}/') &&
                              m.kind == 'video',
                        )
                        .take(1))
                  MediaCard(controller.projectId.value, media),
                Wrap(
                  spacing: 8,
                  children: [
                    for (final action in [
                      ('Generate', 'scene.generate_visual'),
                      ('Modify', 'scene.modify_region'),
                      ('Extend', 'scene.extend_visual'),
                      ('Revisions', 'scene.revisions'),
                      ('Direction', 'scene.save_direction'),
                    ])
                      TextButton(
                        onPressed: () => openAction(controller, action.$2, {
                          'sid': entry.key,
                        }),
                        child: Text(action.$1),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ),
    ];
  }

  Future<void> openAction(
    WorkspaceController controller,
    String operation, [
    Map<String, dynamic> args = const {},
  ]) async {
    if (operation == 'native.keyframes') {
      await Get.dialog(KeyframesDialog(controller, args['sid']));
      return;
    }
    if ({
      'assets.generate',
      'pipeline.generate',
      'batch.animated_scenes',
      'animation.prepare',
      'scene.generate_visual',
      'render.generate_scenes',
      'scene_continuity_review.review',
      'batch.review_episode',
    }.contains(operation)) {
      final error = await controller.runPrepared(operation, args);
      if (error != null) {
        await Get.dialog(TextDialog('Could not queue task', error));
      }
      return;
    }
    if (operation == 'assets.revisions' || operation == 'scene.revisions') {
      await Get.dialog(
        RevisionDialog(
          controller,
          stage: args['stage'] ?? 'scene_images',
          subject: args['subject'] ?? args['sid'],
          video: operation == 'scene.revisions',
        ),
      );
      return;
    }
    final item = controller.snapshot.value?.operations.firstWhereOrNull(
      (item) => item.operation == operation,
    );
    if (item == null) {
      await Get.dialog(
        TextDialog(
          'Action unavailable',
          'Choose the corresponding domain action from All workspace actions. ($operation)',
        ),
      );
      return;
    }
    await Get.dialog(ActionDialog(controller, item, initial: args));
  }

  Future<void> chooseOperation(WorkspaceController controller) async {
    var query = '';
    final result = await Get.dialog<Operation>(
      StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Workspace actions'),
          content: SizedBox(
            width: 500,
            height: 500,
            child: Column(
              children: [
                FieldRow(
                  label: 'Search action',
                  child: TextField(
                    decoration: const InputDecoration(),
                    onChanged: (value) =>
                        setState(() => query = value.toLowerCase()),
                  ),
                ),
                Expanded(
                  child: ListView(
                    children: [
                      for (final action
                          in controller.snapshot.value!.operations.where(
                            (a) => a.title.toLowerCase().contains(query),
                          ))
                        ListTile(
                          title: Text(action.title),
                          subtitle: action.description.isEmpty
                              ? null
                              : Text(
                                  action.description,
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                ),
                          onTap: () => Get.back(result: action),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Get.back(), child: const Text('Close')),
          ],
        ),
      ),
    );
    if (result != null) await Get.dialog(ActionDialog(controller, result));
  }

  Future<void> createEpisode(
    WorkspaceController controller,
    String owner,
  ) async {
    final definitions = await controller.service.prepare(
      owner,
      'episodes.create',
      {},
    );
    final operation = Operation.fromJson(
      Map<String, dynamic>.from(definitions['definition']),
    );
    final source = WorkspaceController(controller.service, 'story', owner);
    source.projectId.value = owner;
    await Get.dialog(ActionDialog(source, operation));
    await Get.find<ProjectController>().loadProjects();
    await controller.loadWorkspace();
  }
}

class _VoiceLineCard extends StatefulWidget {
  final WorkspaceController controller;
  final Map<String, dynamic> row;
  const _VoiceLineCard(this.controller, this.row);
  @override
  State<_VoiceLineCard> createState() => _VoiceLineCardState();
}

class _VoiceLineCardState extends State<_VoiceLineCard> {
  late String character;
  late TextEditingController text;
  @override
  void initState() {
    super.initState();
    character = widget.row['characterId'];
    text = TextEditingController(text: widget.row['text']);
  }

  @override
  Widget build(BuildContext context) {
    final ids =
        widget.controller.snapshot.value!.characters
            .map((item) => item['characterId'] as String)
            .toSet()
          ..add('narrator');
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('${widget.row['id']} · ${widget.row['status']}'),
            const Text(
              'Character Lines',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: FieldRow(
                    label: 'Character',
                    child: DropdownButtonFormField<String>(
                      initialValue: character,
                      isExpanded: true,
                      items: ids
                          .map(
                            (id) =>
                                DropdownMenuItem(value: id, child: Text(id)),
                          )
                          .toList(),
                      onChanged: (value) => character = value!,
                      decoration: const InputDecoration(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  flex: 2,
                  child: FieldRow(
                    label: 'Line',
                    child: TextField(
                      controller: text,
                      maxLines: 3,
                      decoration: const InputDecoration(),
                    ),
                  ),
                ),
              ],
            ),
            Wrap(
              spacing: 8,
              children: [
                FilledButton(
                  onPressed: () async {
                    final error = await widget.controller
                        .runAction('voice_lines.save', {
                          'subject': widget.row['id'],
                          'character_id': character,
                          'text': text.text,
                          'expected': widget.row['version'],
                        });
                    if (error != null) {
                      Get.dialog(TextDialog('Save failed', error));
                    }
                  },
                  child: const Text('Save'),
                ),
                TextButton(
                  onPressed: () => setState(() {
                    character = widget.row['characterId'];
                    text.text = widget.row['text'];
                  }),
                  child: const Text('Reset'),
                ),
                TextButton(
                  onPressed: () => widget.controller.runAction(
                    'assets.generate',
                    {'stage': 'voices', 'subject': widget.row['id']},
                  ),
                  child: const Text('Generate voice track'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    text.dispose();
    super.dispose();
  }
}
