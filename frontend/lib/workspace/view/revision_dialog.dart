import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/service/api_client.dart';
import '../controller/workspace_controller.dart';
import '../model/workspace_snapshot.dart';
import 'media_card.dart';

class RevisionDialog extends StatefulWidget {
  final WorkspaceController controller;
  final String stage, subject;
  final bool video;
  const RevisionDialog(
    this.controller, {
    required this.stage,
    required this.subject,
    this.video = false,
    super.key,
  });
  @override
  State<RevisionDialog> createState() => _RevisionDialogState();
}

class _RevisionDialogState extends State<RevisionDialog> {
  Map? result;
  String? error;
  bool saving = false;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final data = await Get.find<ApiClient>().request(
        'GET',
        '/api/projects/${widget.controller.projectId.value}/revisions?subject=${Uri.encodeQueryComponent(widget.subject)}&stage=${Uri.encodeQueryComponent(widget.stage)}&video=${widget.video}',
      );
      if (mounted) setState(() => result = data);
    } catch (value) {
      if (mounted) setState(() => error = value.toString());
    }
  }

  Future<void> apply(Map record) async {
    setState(() => saving = true);
    try {
      final args = widget.video
          ? {
              'sid': widget.subject,
              'expected': result!['expected'],
              'asset_id': record['asset']['assetId'],
            }
          : {
              'stage': widget.stage,
              'subject': widget.subject,
              'asset_id': record['asset']['assetId'],
            };
      if (widget.video) {
        final prepared = await widget.controller.service.prepare(
          widget.controller.projectId.value,
          'scene.apply_revision',
          args,
        );
        args['options'] = prepared['values']['options'];
      }
      final message = await widget.controller.runAction(
        widget.video
            ? 'scene.apply_revision'
            : 'asset_revisions.apply_revision',
        args,
      );
      if (!mounted) return;
      if (message == null) {
        Get.back();
      } else {
        setState(() {
          saving = false;
          error = message;
        });
      }
    } catch (value) {
      if (mounted) {
        setState(() {
          saving = false;
          error = value.toString();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text('${widget.subject} · revisions'),
    content: SizedBox(
      width: 500,
      height: MediaQuery.sizeOf(context).height * .7,
      child: result == null && error == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              children: [
                if (error != null)
                  Text(error!, style: const TextStyle(color: Colors.red)),
                for (final record in result?['records'] as List? ?? [])
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(8),
                      child: Column(
                        children: [
                          Text(
                            'Revision ${record['revision']} · ${record['asset']?['createdAt'] ?? ''}',
                          ),
                          if (record['asset']?['filePath'] != null)
                            MediaCard(
                              widget.controller.projectId.value,
                              MediaAsset(
                                file: record['asset']['filePath'],
                                title: record['asset']['model'] ?? '',
                                kind: widget.video ? 'video' : 'image',
                              ),
                            ),
                          FilledButton(
                            onPressed: saving ? null : () => apply(record),
                            child: Text(
                              saving ? 'Applying…' : 'Apply this revision',
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
    ),
    actions: [
      TextButton(onPressed: () => Get.back(), child: const Text('Close')),
    ],
  );
}
