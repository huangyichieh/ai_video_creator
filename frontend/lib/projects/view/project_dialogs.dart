import '../../util/view/field_row.dart';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/view/dialog.dart';
import '../controller/project_controller.dart';

Future<void> createWorld(ProjectController controller) async {
  final name = TextEditingController(),
      premise = TextEditingController(),
      language = TextEditingController(text: 'zh-TW');
  final result = await Get.dialog<bool>(
    AlertDialog(
      title: const Text('New World'),
      content: SizedBox(
        width: 500,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            FieldRow(
              label: 'World name',
              child: TextField(
                controller: name,
                decoration: const InputDecoration(),
              ),
            ),
            const SizedBox(height: 8),
            FieldRow(
              label: 'World settings first draft',
              child: TextField(
                controller: premise,
                minLines: 3,
                maxLines: 6,
                decoration: const InputDecoration(),
              ),
            ),
            const SizedBox(height: 8),
            FieldRow(
              label: 'Default language',
              child: TextField(
                controller: language,
                decoration: const InputDecoration(),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Get.back(result: false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Get.back(result: true),
          child: const Text('Create'),
        ),
      ],
    ),
  );
  if (result != true) return;
  final task = controller.createWorld(name.text, premise.text, language.text);
  await Get.toNamed('/loading', arguments: {'task': task});
  final error = await task;
  if (error != null) {
    await Get.dialog(TextDialog('Create failed', error));
  } else {
    controller.openProject(controller.selectedProjectId.value);
  }
}

Future<void> removeProject(
  ProjectController controller,
  String id,
  String title,
) async {
  final removed = await Get.dialog<bool>(
    _RemoveProjectDialog(controller, id, title),
    barrierDismissible: false,
  );
  if (removed == true && controller.selectedProjectId.value.isEmpty) {
    Get.offAllNamed('/');
  }
}

class _RemoveProjectDialog extends StatefulWidget {
  final ProjectController controller;
  final String id, title;
  const _RemoveProjectDialog(this.controller, this.id, this.title);
  @override
  State<_RemoveProjectDialog> createState() => _RemoveProjectDialogState();
}

class _RemoveProjectDialogState extends State<_RemoveProjectDialog> {
  final confirmation = TextEditingController();
  bool removing = false;
  String? error;
  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !removing,
    child: AlertDialog(
      title: const Text('Remove project?'),
      content: SizedBox(
        width: 400,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Type the full project name to delete this project and its episodes:',
            ),
            const SizedBox(height: 8),
            SelectableText(
              widget.title,
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 16),
            FieldRow(
              label: 'Project name',
              child: TextField(
                key: const ValueKey('remove-project-confirmation'),
                controller: confirmation,
                autofocus: true,
                enabled: !removing,
                onChanged: (_) => setState(() => error = null),
                decoration: InputDecoration(errorText: error),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: removing ? null : () => Get.back(result: false),
          child: const Text('Cancel'),
        ),
        TextButton(
          key: const ValueKey('confirm-remove-project'),
          style: TextButton.styleFrom(foregroundColor: const Color(0xffff6b6b)),
          onPressed: removing || confirmation.text != widget.title
              ? null
              : remove,
          child: Text(removing ? 'Deleting…' : 'Confirm'),
        ),
      ],
    ),
  );

  Future<void> remove() async {
    if (removing || confirmation.text != widget.title) return;
    setState(() => removing = true);
    final result = await widget.controller.removeProject(
      widget.id,
      confirmation.text,
    );
    if (!mounted) return;
    if (result == null) {
      Get.back(result: true);
    } else {
      setState(() {
        removing = false;
        error = result;
      });
    }
  }

  @override
  void dispose() {
    confirmation.dispose();
    super.dispose();
  }
}
