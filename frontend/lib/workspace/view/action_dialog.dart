import '../../util/view/field_row.dart';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/service/api_client.dart';
import '../../util/service/file_service.dart';
import '../../util/view/dialog.dart';
import '../../util/view/loading.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'camera_editor.dart';
import 'value_editor.dart';
import 'video_region_editor.dart';

class ActionDialog extends StatefulWidget {
  final WorkspaceController controller;
  final Operation operation;
  final Map<String, dynamic> initial;
  const ActionDialog(
    this.controller,
    this.operation, {
    this.initial = const {},
    super.key,
  });
  @override
  State<ActionDialog> createState() => _ActionDialogState();
}

class _ActionDialogState extends State<ActionDialog> {
  Map<String, dynamic> values = {};
  Map<String, dynamic> choices = {};
  Map<String, dynamic> editor = {};
  bool preparing = true, sending = false;
  String? error;
  String additional = '{}';
  @override
  void initState() {
    super.initState();
    prepare(widget.initial);
  }

  Future<void> prepare(Map<String, dynamic> args) async {
    try {
      final response = await widget.controller.service.prepare(
        widget.controller.projectId.value,
        widget.operation.operation,
        args,
      );
      if (mounted) {
        setState(() {
          values = Map<String, dynamic>.from(response['values']);
          choices = Map<String, dynamic>.from(response['choices']);
          editor = Map<String, dynamic>.from(response['editor'] ?? {});
          preparing = false;
          error = null;
        });
      }
    } catch (value) {
      if (mounted) {
        setState(() {
          error = value.toString();
          preparing = false;
        });
      }
    }
  }

  Future<void> submit() async {
    setState(() => sending = true);
    try {
      final args = {
        ...values,
        ...Map<String, dynamic>.from(jsonDecode(additional)),
      };
      for (final parameter in widget.operation.parameters) {
        if (parameter.required &&
            (args[parameter.name] == null || args[parameter.name] == '')) {
          throw Exception('Provide ${parameter.name}.');
        }
      }
      final result = await widget.controller.runAction(
        widget.operation.operation,
        args,
      );
      if (result != null) {
        setState(() {
          error = result;
          sending = false;
        });
        return;
      }
      Get.back();
    } catch (value) {
      if (mounted) {
        setState(() {
          error = value.toString();
          sending = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.operation.title),
    content: SizedBox(
      width: 500,
      height: MediaQuery.sizeOf(context).height * .7,
      child: preparing
          ? const AppLoadingIcon()
          : SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (widget.operation.description.isNotEmpty)
                    Text(widget.operation.description),
                  const SizedBox(height: 8),
                  if (error != null)
                    Text(error!, style: const TextStyle(color: Colors.red)),
                  for (final parameter in widget.operation.parameters)
                    if (![
                          'expected',
                          'expected_input_hash',
                          'expected_layout',
                          'source_hash',
                          'report_hash',
                          'report_revision',
                        ].contains(parameter.name) &&
                        !(widget.operation.operation == 'scene.modify_region' &&
                            [
                              'rectangle',
                              'start_time',
                              'end_time',
                            ].contains(parameter.name)))
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: field(parameter),
                      ),
                  TextButton.icon(
                    onPressed: sending ? null : upload,
                    icon: const Icon(Icons.upload_file),
                    label: const Text('Upload reference / audio / video'),
                  ),
                  if (widget.operation.operation.contains('modify_region')) ...[
                    if (editor['video'] != null)
                      VideoRegionEditor(
                        url: Get.find<ApiClient>().mediaUrl(
                          widget.controller.projectId.value,
                          editor['video'],
                        ),
                        duration: (editor['duration'] as num).toDouble(),
                        aspectRatio:
                            (editor['width'] as num) /
                            (editor['height'] as num),
                        onChanged: (value) => values.addAll(value),
                      ),
                  ],
                  if (widget.operation.operation.contains('save_camera'))
                    CameraEditor(
                      configuration: editor,
                      imageUrl: editor['image'] == null
                          ? referenceImage()
                          : Get.find<ApiClient>().mediaUrl(
                              widget.controller.projectId.value,
                              editor['image'],
                            ),
                      onChanged: (value) {
                        values['setup'] = value;
                        values['fov'] = value['fov'];
                      },
                    ),
                  ExpansionTile(
                    title: const Text('Additional parameters'),
                    children: [
                      FieldRow(
                        label: 'JSON object',
                        child: TextFormField(
                          initialValue: additional,
                          minLines: 3,
                          maxLines: 8,
                          decoration: const InputDecoration(),
                          onChanged: (value) => additional = value,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
    ),
    actions: [
      TextButton(
        onPressed: sending ? null : () => Get.back(),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: sending || preparing ? null : submit,
        child: Text(sending ? 'Queuing…' : 'Confirm'),
      ),
    ],
  );
  String? referenceImage() {
    final snapshot = widget.controller.snapshot.value;
    final image = snapshot?.media.firstWhereOrNull(
      (item) => item.kind == 'image',
    );
    return image == null
        ? null
        : Get.find<ApiClient>().mediaUrl(
            widget.controller.projectId.value,
            image.file,
          );
  }

  Widget field(OperationParameter parameter) {
    final name = parameter.name;
    final value = values[name];
    final options =
        (choices[name] as List?)?.whereType<String>().toList() ??
        (parameter.schema['enum'] as List?)?.whereType<String>().toList() ??
        [];
    if (options.isNotEmpty) {
      return FieldRow(
        label: name,
        child: DropdownButtonFormField<String>(
          key: ValueKey('$name-$value'),
          initialValue: options.contains(value) ? value : null,
          isExpanded: true,
          decoration: InputDecoration(),
          items: options
              .map((item) => DropdownMenuItem(value: item, child: Text(item)))
              .toList(),
          onChanged: (next) {
            setState(() => values[name] = next);
            if ([
              'sid',
              'scene_id',
              'stage',
              'character_id',
              'lid',
              'subject',
              'identity',
              'name',
            ].contains(name)) {
              prepare({...values, name: next});
            }
          },
        ),
      );
    }
    return ValueEditor(
      label: name + (parameter.required ? ' *' : ''),
      value: value,
      onChanged: (next) {
        setState(() => values[name] = next);
      },
    );
  }

  Future<void> upload() async {
    try {
      final result = await Get.find<FileService>().chooseUpload();
      if (result == null) return;
      final fields = widget.operation.parameters.map((p) => p.name).toList();
      final selected = await Get.dialog<String>(
        SimpleDialog(
          title: const Text('Use upload for parameter'),
          children: [
            for (final name in fields)
              SimpleDialogOption(
                onPressed: () => Get.back(result: name),
                child: Text(name),
              ),
          ],
        ),
      );
      if (selected != null) {
        setState(() => values[selected] = {'upload': result['upload']});
      }
    } catch (error) {
      Get.dialog(TextDialog('Upload failed', error.toString()));
    }
  }
}
