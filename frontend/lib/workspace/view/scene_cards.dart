import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'asset_cards.dart';
import 'media_card.dart';
import 'task_button.dart';

class SceneCards extends StatelessWidget {
  final WorkspaceController controller;
  final WorkspaceSnapshot snapshot;
  final String stage;
  final OpenAssetAction open;
  const SceneCards(
    this.controller,
    this.snapshot,
    this.stage,
    this.open, {
    super.key,
  });
  dynamic section(String name) =>
      snapshot.sections.firstWhereOrNull((item) => item.id == name)?.data;
  Widget button(String label, String operation, Map<String, dynamic> args) =>
      TaskButton(
        controller,
        operation: operation,
        label: label,
        arguments: args,
        onPressed: () => open(operation, args),
      );
  @override
  Widget build(BuildContext context) {
    final scenes = (section('scenes.json')?['scenes'] as List? ?? []);
    final images = section('production.json')?['records'] as Map? ?? {};
    final videos = section('visual_scenes.json') as Map? ?? {};
    final sets = section('scene_image_keyframes.json') as Map? ?? {};
    return Column(
      children: [
        for (final scene in scenes)
          Builder(
            builder: (context) {
              final sid = scene['sceneId'] as String;
              final image = images['scene_images:$sid'] as Map?;
              final record = stage == 'Animated scenes'
                  ? videos[sid] as Map?
                  : image;
              final frames = sets[sid]?['frames'] as List? ?? [];
              return Card(
                child: ExpansionTile(
                  key: PageStorageKey(
                    '${controller.projectId.value}-$stage-$sid',
                  ),
                  initiallyExpanded: record?['status'] != 'succeeded',
                  title: Text(
                    '$sid · ${scene['shotType']} · ${scene['durationSec']}s',
                  ),
                  subtitle: Text(scene['action'] ?? ''),
                  childrenPadding: const EdgeInsets.all(8),
                  children: [
                    Text(scene['visualDescription'] ?? ''),
                    Text('${scene['shotType']} · ${scene['durationSec']}s'),
                    Text(scene['action'] ?? ''),
                    if (stage == 'Scene images')
                      button('Multiple scene images', 'native.keyframes', {
                        'sid': sid,
                      }),
                    if (frames.length > 1)
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          for (final frame in frames)
                            Expanded(
                              child: Column(
                                children: [
                                  MediaCard(
                                    controller.projectId.value,
                                    MediaAsset(
                                      file: frame['asset']['filePath'],
                                      title: '${frame['timeSec']}s',
                                      kind: 'image',
                                    ),
                                  ),
                                ],
                              ),
                            ),
                        ],
                      )
                    else if (image?['asset']?['filePath'] != null)
                      MediaCard(
                        controller.projectId.value,
                        MediaAsset(
                          file: image!['asset']['filePath'],
                          title: 'Scene snapshot',
                          kind: 'image',
                        ),
                      ),
                    if (stage == 'Animated scenes' &&
                        record?['asset']?['filePath'] != null)
                      MediaCard(
                        controller.projectId.value,
                        MediaAsset(
                          file: record!['asset']['filePath'],
                          title:
                              'Revision ${record['revision']} · ${record['asset']['createdAt']}',
                          kind: 'video',
                        ),
                      ),
                    if (record?['error'] != null)
                      Text(
                        record!['error'].toString(),
                        style: const TextStyle(color: Colors.red),
                      ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        if (stage == 'Scene images') ...[
                          button('Generate / retry image', 'assets.generate', {
                            'stage': 'scene_images',
                            'subject': sid,
                            'force': true,
                          }),
                          button(
                            'Edit storyboard (save & approve)',
                            'storyboard_editor.save_scene',
                            {'scene_id': sid, 'auto_approve': true},
                          ),
                          button('Saved image revisions', 'assets.revisions', {
                            'stage': 'scene_images',
                            'subject': sid,
                          }),
                        ],
                        if (stage == 'Direction & performance') ...[
                          button(
                            'Edit animation direction',
                            'scene.save_direction',
                            {'sid': sid},
                          ),
                          button(
                            'Regenerate direction',
                            'scene.regenerate_direction',
                            {'sid': sid},
                          ),
                        ],
                        if (stage == 'Animated scenes') ...[
                          button(
                            'Generate animated scene',
                            'scene.generate_visual',
                            {'sid': sid, 'force': true},
                          ),
                          button(
                            'Edit animation direction',
                            'scene.save_direction',
                            {'sid': sid},
                          ),
                          button('Modify', 'scene.modify_region', {'sid': sid}),
                          button('Extend', 'scene.extend_visual', {'sid': sid}),
                          button('Saved video revisions', 'scene.revisions', {
                            'sid': sid,
                          }),
                        ],
                      ],
                    ),
                  ],
                ),
              );
            },
          ),
      ],
    );
  }
}
