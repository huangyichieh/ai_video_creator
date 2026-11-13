import 'package:flutter/material.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'asset_cards.dart';
import 'media_card.dart';
import 'task_button.dart';

class LibraryCards extends StatelessWidget {
  final WorkspaceController controller;
  final WorkspaceSnapshot snapshot;
  final String kind;
  final Set<String>? selected;
  final OpenAssetAction open;
  const LibraryCards(
    this.controller,
    this.snapshot,
    this.kind,
    this.open, {
    this.selected,
    super.key,
  });
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
    final Map data = kind == 'Props'
        ? ((snapshot.computed['props']?['props'] as Map?) ?? {})
        : ((snapshot.computed['audio']?[kind] as Map?) ?? {});
    return Column(
      children: [
        for (final entry in data.entries.where(
          (entry) => selected == null || selected!.contains(entry.key),
        ))
          Builder(
            builder: (context) {
              final id = entry.key as String, item = entry.value as Map;
              final visual = snapshot.computed['selectedProps'] as Map? ?? {};
              final ready = kind == 'Props'
                  ? visual.entries.any(
                      (entry) =>
                          entry.key.toString().startsWith('$id:') &&
                          (entry.value as Map).isNotEmpty,
                    )
                  : item['ready'] == true;
              return Card(
                child: ExpansionTile(
                  key: PageStorageKey(
                    '${controller.projectId.value}-$kind-$id',
                  ),
                  initiallyExpanded: false,
                  title: Text(
                    '${ready ? '●' : '●'} ${item['title'] ?? id} · ${ready ? 'Ready' : 'Draft'}',
                    style: TextStyle(
                      color: ready ? Colors.green : Colors.orange.shade800,
                    ),
                  ),
                  subtitle: Text(id),
                  childrenPadding: const EdgeInsets.all(8),
                  children: [
                    Text(item['description'] ?? ''),
                    if (kind == 'Props') ...[
                      button('Edit prop', 'prop_drafts.save', {'identity': id}),
                      button('Remove prop', 'prop_removal.remove', {'pid': id}),
                      for (final mode in item['modes'] as List? ?? [])
                        ExpansionTile(
                          title: Text(mode.toString()),
                          children: [
                            if ((visual['$id:$mode'] as Map? ?? {}).isNotEmpty)
                              MediaCard(
                                controller.projectId.value,
                                MediaAsset(
                                  file:
                                      visual['$id:$mode']['asset']['filePath'],
                                  title: mode.toString(),
                                  kind: 'image',
                                ),
                              ),
                            Wrap(
                              spacing: 8,
                              children: [
                                button(
                                  'Generate prop visual',
                                  'library.image',
                                  {
                                    'key': 'prop:$id:$mode',
                                    'prompt': item['description'],
                                  },
                                ),
                                button(
                                  'Apply saved / other mode revision',
                                  'prop_revisions.apply',
                                  {'pid': id, 'mode': mode},
                                ),
                                button('Remove mode', 'prop_removal.remove', {
                                  'pid': id,
                                  'mode': mode,
                                }),
                              ],
                            ),
                          ],
                        ),
                    ] else ...[
                      if (item['file'] != null)
                        MediaCard(
                          controller.projectId.value,
                          MediaAsset(
                            file: item['file'],
                            title: item['title'] ?? id,
                            kind: 'audio',
                          ),
                        ),
                      Wrap(
                        spacing: 8,
                        children: [
                          button(
                            'Edit details',
                            'check_asset_sync.save_audio_details',
                            {
                              'kind': kind,
                              'identity': id,
                              'title': item['title'],
                              'description': item['description'],
                            },
                          ),
                          button('Upload audio', 'audio_assets.upload', {
                            'kind': kind,
                            'identity': id,
                          }),
                          button(
                            'Generate (ElevenLabs)',
                            'storyboard_audio.generate',
                            {
                              'kind': kind,
                              'key': id,
                              'prompt': item['description'] ?? item['title'],
                              'duration': kind == 'SFX' ? 3 : 30,
                            },
                          ),
                          if (kind == 'BGM') ...[
                            button(
                              'AI choice (Pixabay)',
                              'pixabay_music.choose',
                              {'music_id': id},
                            ),
                            button(
                              'Register licensed Pixabay track',
                              'pixabay_music.register',
                              {},
                            ),
                            button(
                              'Use Pixabay track',
                              'pixabay_music.use_track',
                              {'music_id': id},
                            ),
                          ],
                        ],
                      ),
                    ],
                  ],
                ),
              );
            },
          ),
      ],
    );
  }
}
