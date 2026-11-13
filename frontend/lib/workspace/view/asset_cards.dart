import 'package:flutter/material.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'media_card.dart';
import 'value_editor.dart';
import 'task_button.dart';

typedef OpenAssetAction =
    void Function(String operation, Map<String, dynamic> arguments);

class CharacterCards extends StatelessWidget {
  final WorkspaceController controller;
  final WorkspaceSnapshot snapshot;
  final OpenAssetAction open;
  const CharacterCards(this.controller, this.snapshot, this.open, {super.key});
  Widget button(String title, String operation, Map<String, dynamic> args) =>
      TaskButton(
        controller,
        operation: operation,
        arguments: args,
        label: title,
        onPressed: () => open(operation, args),
      );
  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Text('Total characters: ${snapshot.characters.length}'),
      for (final character in snapshot.characters)
        Builder(
          builder: (context) {
            final cid = character['characterId'] as String;
            final state =
                snapshot.computed['characterStatus']?[cid] as Map? ?? {};
            final approved = state['approved'] == true;
            final requirements = state['requirements'] as Map? ?? {};
            final missing = requirements.entries
                .where((row) => row.value != true)
                .map((row) => row.key)
                .join(', ');
            return Card(
              child: ExpansionTile(
                key: PageStorageKey(
                  'character-${controller.projectId.value}-$cid',
                ),
                initiallyExpanded: !approved,
                title: Text(
                  '${character['name']} · ${approved ? 'Approved' : 'Pending'}',
                  style: TextStyle(
                    color: approved ? Colors.green : Colors.orange.shade800,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                subtitle: Text(cid),
                childrenPadding: const EdgeInsets.all(8),
                children: [
                  Wrap(
                    spacing: 8,
                    children: [
                      Tooltip(
                        message: approved
                            ? 'Already approved'
                            : missing.isEmpty
                            ? 'Approve current assets'
                            : 'Missing: $missing',
                        child: FilledButton(
                          onPressed: approved || missing.isNotEmpty
                              ? null
                              : () => open('assets.approve_character', {
                                  'subject': cid,
                                }),
                          child: Text(
                            approved ? 'Approved' : 'Approve character',
                          ),
                        ),
                      ),
                      button('Remove character', 'characters.remove', {
                        'character_id': cid,
                      }),
                      button(
                        'Edit character detail',
                        'pipeline.edit_character',
                        {'character_id': cid},
                      ),
                    ],
                  ),
                  DefaultTabController(
                    length: 3,
                    child: Column(
                      children: [
                        const TabBar(
                          tabs: [
                            Tab(text: 'Visual'),
                            Tab(text: 'Voice'),
                            Tab(text: 'Costumes'),
                          ],
                        ),
                        SizedBox(
                          height: 440,
                          child: TabBarView(
                            children: [
                              ListView(
                                children: [
                                  for (final image
                                      in snapshot.media
                                          .where(
                                            (m) =>
                                                m.kind == 'image' &&
                                                m.file.contains(cid) &&
                                                !m.file.contains('costume'),
                                          )
                                          .take(1))
                                    MediaCard(
                                      controller.projectId.value,
                                      image,
                                    ),
                                  Wrap(
                                    children: [
                                      button(
                                        'Generate visual',
                                        'assets.generate',
                                        {
                                          'stage': 'character_references',
                                          'subject': cid,
                                          'force': true,
                                        },
                                      ),
                                      button(
                                        'Generation mode / prompt',
                                        'visual.update',
                                        {
                                          'stage': 'character_references',
                                          'subject': cid,
                                        },
                                      ),
                                      button(
                                        'Image reference',
                                        'assets.set_character_image',
                                        {'subject': cid},
                                      ),
                                      button(
                                        'Design reference (OpenAI)',
                                        'visual.design_reference',
                                        {
                                          'stage': 'character_references',
                                          'subject': cid,
                                        },
                                      ),
                                      button(
                                        'Modify',
                                        'assets.modify_with_openai',
                                        {
                                          'stage': 'character_references',
                                          'subject': cid,
                                        },
                                      ),
                                      button(
                                        'Saved revisions',
                                        'assets.revisions',
                                        {
                                          'stage': 'character_references',
                                          'subject': cid,
                                        },
                                      ),
                                      button(
                                        'Apply revision',
                                        'asset_revisions.apply_revision',
                                        {
                                          'stage': 'character_references',
                                          'subject': cid,
                                        },
                                      ),
                                    ],
                                  ),
                                  ValueEditor(
                                    value: character,
                                    readOnly: true,
                                    onChanged: (_) {},
                                  ),
                                ],
                              ),
                              ListView(
                                children: [
                                  Wrap(
                                    children: [
                                      button(
                                        'Configure provider Voice ID',
                                        'identity.save',
                                        {},
                                      ),
                                      button(
                                        'Generate voice prompt',
                                        'voice_design.generate_voice_design',
                                        {'character_id': cid},
                                      ),
                                      button(
                                        'Generate voice sample',
                                        'library.talking_sample',
                                        {'character_id': cid, 'video': false},
                                      ),
                                      button(
                                        'Talking animation sample',
                                        'library.talking_sample',
                                        {'character_id': cid, 'video': true},
                                      ),
                                      button(
                                        'Approve voice',
                                        'library.approve_voice',
                                        {'cid': cid},
                                      ),
                                      button(
                                        'Apply voice revision',
                                        'library.apply_voice_revision',
                                        {'character_id': cid},
                                      ),
                                    ],
                                  ),
                                  for (final media in snapshot.media.where(
                                    (m) =>
                                        (m.kind == 'audio' ||
                                            m.kind == 'video') &&
                                        m.file.contains(cid),
                                  ))
                                    MediaCard(
                                      controller.projectId.value,
                                      media,
                                    ),
                                ],
                              ),
                              ListView(
                                children: [
                                  button(
                                    'Edit / add costumes',
                                    'identity.save',
                                    {},
                                  ),
                                  button(
                                    'Generate costume',
                                    'library.generate_costume',
                                    {'character_id': cid},
                                  ),
                                  button(
                                    'Apply costume revision',
                                    'library.apply_costume_revision',
                                    {'character_id': cid},
                                  ),
                                  for (final image in snapshot.media.where(
                                    (m) =>
                                        m.file.contains('costume') &&
                                        m.file.contains(cid),
                                  ))
                                    MediaCard(
                                      controller.projectId.value,
                                      image,
                                    ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            );
          },
        ),
    ],
  );
}

class BackgroundCards extends StatelessWidget {
  final WorkspaceController controller;
  final WorkspaceSnapshot snapshot;
  final OpenAssetAction open;
  const BackgroundCards(this.controller, this.snapshot, this.open, {super.key});
  Widget button(String title, String operation, Map<String, dynamic> args) =>
      TaskButton(
        controller,
        operation: operation,
        arguments: args,
        label: title,
        onPressed: () => open(operation, args),
      );
  @override
  Widget build(BuildContext context) => Column(
    children: [
      for (final location in snapshot.computed['backgrounds'] as List? ?? [])
        Card(
          child: ExpansionTile(
            key: PageStorageKey(
              'background-${controller.projectId.value}-${location['locationId']}',
            ),
            initiallyExpanded: location['approved'] != true,
            title: Text(
              '${location['name']} · ${location['approved'] == true ? 'Approved' : 'Pending'}',
              style: TextStyle(
                color: location['approved'] == true
                    ? Colors.green
                    : Colors.orange.shade800,
              ),
            ),
            subtitle: Text(
              location['locationId'],
              style: Theme.of(context).textTheme.bodySmall,
            ),
            childrenPadding: const EdgeInsets.all(8),
            children: [
              Text(location['description']),
              Wrap(
                children: [
                  button('Approve background', 'background.approve', {
                    'subject': location['locationId'],
                  }),
                  button('Remove background', 'background.remove', {
                    'subject': location['locationId'],
                  }),
                  button('Edit description', 'background.edit_description', {
                    'subject': location['locationId'],
                  }),
                ],
              ),
              ExpansionTile(
                title: const Text(
                  'Floor plan, object layout & reference settings',
                ),
                children: [
                  ValueEditor(
                    value: location['references'],
                    readOnly: true,
                    onChanged: (_) {},
                  ),
                  Wrap(
                    children: [
                      button(
                        'Edit reference settings',
                        'background.configure_references',
                        {'subject': location['locationId']},
                      ),
                      button(
                        'Generate floor plan & object layout',
                        'batch.location_layout',
                        {'lid': location['locationId']},
                      ),
                      button('Modify layout', 'batch.location_layout', {
                        'lid': location['locationId'],
                        'update': 'modify',
                      }),
                      button(
                        'Generate floor-plan images',
                        'location_layout.generate_floor_plan_images',
                        {'subject': location['locationId']},
                      ),
                      button(
                        'Floor-plan revisions',
                        'floor_plan_revisions.revisions',
                        {'lid': location['locationId']},
                      ),
                    ],
                  ),
                ],
              ),
              for (final image in snapshot.media.where(
                (m) =>
                    m.kind == 'image' &&
                    m.file.contains(location['locationId']),
              ))
                MediaCard(controller.projectId.value, image),
              Wrap(
                children: [
                  button(
                    'Add detail background',
                    'location_details.add_draft',
                    {'subject': location['locationId']},
                  ),
                  button(
                    'Generate / regenerate detail',
                    'location_details.generate',
                    {'lid': location['locationId']},
                  ),
                  button('Camera setup', 'location_details.save_camera', {
                    'lid': location['locationId'],
                  }),
                  button('Generate', 'assets.generate', {
                    'stage': 'backgrounds',
                    'subject': location['locationId'],
                  }),
                  button('Master revisions', 'location_pairs.versions', {
                    'lid': location['locationId'],
                    'group': 'masters',
                  }),
                  button('Approve masters', 'location_pairs.approve', {
                    'lid': location['locationId'],
                    'group': 'masters',
                  }),
                ],
              ),
            ],
          ),
        ),
    ],
  );
}
