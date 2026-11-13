import '../../util/view/field_row.dart';
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/service/api_client.dart';
import '../../util/controller/jobs_controller.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'media_card.dart';

class KeyframesDialog extends StatefulWidget {
  final WorkspaceController controller;
  final String scene;
  const KeyframesDialog(this.controller, this.scene, {super.key});
  @override
  State<KeyframesDialog> createState() => _KeyframesDialogState();
}

class _KeyframesDialogState extends State<KeyframesDialog> {
  late final String token;
  Map<String, dynamic>? draft;
  final times = <String, double>{};
  final prompt = TextEditingController();
  String? reference, error;
  bool busy = false, fetching = false;
  Timer? poll;
  @override
  void initState() {
    super.initState();
    token = 'frames${DateTime.now().microsecondsSinceEpoch}';
    start();
  }

  Future<void> start() async {
    final message = await widget.controller.runPrepared(
      'scene_keyframes.start',
      {'sid': widget.scene, 'token': token},
    );
    if (!mounted) return;
    if (message != null) {
      setState(() => error = message);
      return;
    }
    poll = Timer.periodic(const Duration(seconds: 1), (_) => load());
    await load();
  }

  Future<void> load() async {
    if (fetching) return;
    fetching = true;
    try {
      final value = Map<String, dynamic>.from(
        await Get.find<ApiClient>().request(
          'GET',
          '/api/projects/${widget.controller.projectId.value}/keyframes/$token',
        ),
      );
      if (mounted && value.isNotEmpty) {
        for (final frame in value['frames'] as List) {
          times.putIfAbsent(
            frame['id'],
            () =>
                (frame['timeSec'] as num?)?.toDouble() ??
                ((times.values.fold<double>(0, (a, b) => a > b ? a : b)) +
                        (value['duration'] as num).toDouble() / 4)
                    .clamp(0, (value['duration'] as num).toDouble()),
          );
        }
        setState(() {
          draft = value;
          if (!(value['frames'] as List).any(
            (frame) => frame['id'] == reference,
          )) {
            reference = (value['frames'] as List).first['id'];
          }
        });
      }
      final failed = Get.find<JobsController>().jobs.firstWhereOrNull(
        (job) => job.arguments?['token'] == token && job.status == 'failed',
      );
      if (mounted && failed != null) setState(() => error = failed.error);
    } catch (value) {
      if (mounted) setState(() => error = value.toString());
    } finally {
      fetching = false;
    }
  }

  Future<void> run(String action, Map<String, dynamic> args) async {
    setState(() => busy = true);
    final message = await widget.controller.runPrepared(
      'scene_keyframes.$action',
      {'token': token, ...args},
    );
    if (mounted) {
      setState(() {
        busy = false;
        error = message;
      });
    }
  }

  MediaAsset media(Map frame) => MediaAsset(
    file: frame['asset']['filePath'],
    title: '${frame['timeSec'] ?? 0}s',
    kind: 'image',
  );
  @override
  Widget build(BuildContext context) {
    final frames = draft?['frames'] as List? ?? [];
    final candidates = draft?['candidates'] as List? ?? [];
    return AlertDialog(
      title: Text('${widget.scene} · Multiple scene images'),
      content: SizedBox(
        width: 700,
        height: MediaQuery.sizeOf(context).height * .72,
        child: draft == null && error == null
            ? const Center(child: CircularProgressIndicator())
            : SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (error != null)
                      Text(error!, style: const TextStyle(color: Colors.red)),
                    Text(
                      'Duration ${draft?['duration'] ?? 0}s · maximum 5 images',
                    ),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        for (final frame in frames)
                          Expanded(
                            child: Padding(
                              padding: const EdgeInsets.all(4),
                              child: Column(
                                children: [
                                  if (frame['asset'] != null)
                                    MediaCard(
                                      widget.controller.projectId.value,
                                      media(frame),
                                    ),
                                  FieldRow(
                                    label: 'Time (s)',
                                    child: TextFormField(
                                      key: ValueKey('time-${frame['id']}'),
                                      initialValue: times[frame['id']]
                                          .toString(),
                                      decoration: const InputDecoration(),
                                      keyboardType: TextInputType.number,
                                      onChanged: (value) {
                                        final number = double.tryParse(value);
                                        if (number != null) {
                                          times[frame['id']] = number;
                                        }
                                      },
                                    ),
                                  ),
                                  IconButton(
                                    tooltip: 'Remove image',
                                    onPressed: frames.length <= 1 || busy
                                        ? null
                                        : () => run('remove_image', {
                                            'identity': frame['id'],
                                          }),
                                    icon: const Icon(Icons.delete_outline),
                                  ),
                                ],
                              ),
                            ),
                          ),
                      ],
                    ),
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(8),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            const Text(
                              'Add scene image',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                            FieldRow(
                              label: 'Reference image',
                              child: DropdownButtonFormField<String>(
                                isExpanded: true,
                                initialValue: reference,
                                decoration: const InputDecoration(),
                                items: [
                                  for (var i = 0; i < frames.length; i++)
                                    DropdownMenuItem(
                                      value: frames[i]['id'],
                                      child: Text('Image ${i + 1}'),
                                    ),
                                ],
                                onChanged: (value) =>
                                    setState(() => reference = value),
                              ),
                            ),
                            FieldRow(
                              label: 'Description',
                              child: TextField(
                                controller: prompt,
                                maxLines: 4,
                                minLines: 2,
                                decoration: const InputDecoration(),
                              ),
                            ),
                            FilledButton(
                              onPressed:
                                  busy ||
                                      reference == null ||
                                      frames.length >= 5
                                  ? null
                                  : () {
                                      if (prompt.text.trim().isEmpty) {
                                        setState(
                                          () => error = 'Enter a description.',
                                        );
                                        return;
                                      }
                                      run('generate', {
                                        'identity':
                                            'image${DateTime.now().microsecondsSinceEpoch}',
                                        'reference_id': reference,
                                        'prompt': prompt.text,
                                        'preview': true,
                                      });
                                    },
                              child: Text(
                                busy
                                    ? 'Queuing…'
                                    : 'Generate / regenerate image',
                              ),
                            ),
                            for (final frame in candidates) ...[
                              if (frame['status'] == 'generating')
                                const Padding(
                                  padding: EdgeInsets.all(8),
                                  child: Column(
                                    children: [
                                      CircularProgressIndicator(),
                                      Text('Generating…'),
                                    ],
                                  ),
                                ),
                              if (frame['asset'] != null)
                                MediaCard(
                                  widget.controller.projectId.value,
                                  media(frame),
                                ),
                              if (frame['status'] == 'failed')
                                Text(
                                  frame['error']?.toString() ??
                                      'Generation failed',
                                  style: const TextStyle(color: Colors.red),
                                ),
                              if (frame['status'] == 'ready')
                                TextButton(
                                  onPressed: busy
                                      ? null
                                      : () => run('add_generated', {
                                          'identity': frame['id'],
                                        }),
                                  child: const Text('Add generated image'),
                                ),
                            ],
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
      ),
      actions: [
        FilledButton(
          onPressed: busy || draft == null
              ? null
              : () => run('confirm', {
                  'times': {
                    for (final frame in frames) frame['id']: times[frame['id']],
                  },
                  'keep_open': true,
                }),
          child: Text(busy ? 'Saving…' : 'Save'),
        ),
        TextButton(onPressed: () => Get.back(), child: const Text('Close')),
      ],
    );
  }

  @override
  void dispose() {
    poll?.cancel();
    prompt.dispose();
    super.dispose();
  }
}
