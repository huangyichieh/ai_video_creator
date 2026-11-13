import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/view/dialog.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'value_editor.dart';
import '../../util/view/field_row.dart';

class ArtifactCard extends StatelessWidget {
  final WorkspaceController controller;
  final Artifact artifact;
  const ArtifactCard(this.controller, this.artifact, {super.key});
  @override
  Widget build(BuildContext context) => Card(
    child: ExpansionTile(
      key: PageStorageKey('${controller.projectId.value}-${artifact.id}'),
      title: Text(artifact.title),
      initiallyExpanded: artifact.id == 'world_setting.json',
      trailing:
          {
            'world_setting.json',
            'characters.json',
            'story.json',
            'script.json',
            'scenes.json',
            'prompts.json',
            'outline.json',
          }.contains(artifact.id)
          ? IconButton(
              tooltip: 'Edit and save',
              onPressed: () =>
                  Get.dialog(_ArtifactDialog(controller, artifact)),
              icon: const Icon(Icons.edit_outlined),
            )
          : null,
      childrenPadding: const EdgeInsets.all(8),
      children: [
        ValueEditor(value: artifact.data, readOnly: true, onChanged: (_) {}),
      ],
    ),
  );
}

class _ArtifactDialog extends StatefulWidget {
  final WorkspaceController controller;
  final Artifact artifact;
  const _ArtifactDialog(this.controller, this.artifact);
  @override
  State<_ArtifactDialog> createState() => _ArtifactDialogState();
}

class _ArtifactDialogState extends State<_ArtifactDialog> {
  dynamic draft;
  bool editing = false, saving = false, jsonVisible = false;
  String? error;
  @override
  void initState() {
    super.initState();
    draft = jsonDecode(jsonEncode(widget.artifact.data));
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.artifact.title),
    content: SizedBox(
      width: 500,
      height: MediaQuery.sizeOf(context).height * .7,
      child: SingleChildScrollView(
        child: Column(
          children: [
            FieldRow(
              label: 'Edit mode',
              child: Align(
                alignment: Alignment.centerLeft,
                child: Switch(
                  value: editing,
                  onChanged: (value) => setState(() => editing = value),
                ),
              ),
            ),
            if (error != null)
              Text(error!, style: const TextStyle(color: Colors.red)),
            ValueEditor(
              value: draft,
              readOnly: !editing,
              onChanged: (value) => setState(() => draft = value),
            ),
            Wrap(
              spacing: 8,
              children: [
                TextButton(
                  onPressed: () => setState(() => jsonVisible = !jsonVisible),
                  child: Text(jsonVisible ? 'Hide JSON' : 'Show JSON'),
                ),
                if (jsonVisible)
                  TextButton.icon(
                    onPressed: () => setState(() {
                      editing = true;
                      jsonVisible = false;
                    }),
                    icon: const Icon(Icons.edit_outlined),
                    label: const Text('Edit fields'),
                  ),
              ],
            ),
            if (jsonVisible)
              SelectableText(const JsonEncoder.withIndent('  ').convert(draft)),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(onPressed: () => Get.back(), child: const Text('Close')),
      TextButton(
        onPressed: saving
            ? null
            : () => setState(() {
                draft = jsonDecode(jsonEncode(widget.artifact.data));
                error = null;
              }),
        child: const Text('Reset'),
      ),
      FilledButton(
        onPressed: !editing || saving ? null : save,
        child: Text(saving ? 'Saving…' : 'Save'),
      ),
    ],
  );
  Future<void> save() async {
    setState(() => saving = true);
    final result = await widget.controller.saveArtifact(widget.artifact, draft);
    if (!mounted) return;
    setState(() {
      saving = false;
      error = result;
    });
    if (result == null) Get.dialog(const TextDialog('Saved', 'Changes saved.'));
  }
}
