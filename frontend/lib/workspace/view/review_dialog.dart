import 'package:flutter/material.dart';
import 'dart:convert';
import 'package:get/get.dart';
import '../../util/service/api_client.dart';
import '../../util/view/content_text_field.dart';
import '../controller/workspace_controller.dart';
import 'value_editor.dart';

/// Decisions retain the existing source fingerprints and approval semantics.
class ReviewDialog extends StatefulWidget {
  final WorkspaceController controller;
  final String kind;
  const ReviewDialog(this.controller, this.kind, {super.key});
  @override
  State<ReviewDialog> createState() => _ReviewDialogState();
}

class _ReviewDialogState extends State<ReviewDialog> {
  Map<String, dynamic>? data;
  final decisions = <int, String>{};
  final edits = <int, String>{};
  String? error;
  bool saving = false;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final value = await Get.find<ApiClient>().request(
        'GET',
        '/api/projects/${widget.controller.projectId.value}/reviews/${widget.kind}',
      );
      if (mounted) setState(() => data = Map<String, dynamic>.from(value));
    } catch (value) {
      if (mounted) setState(() => error = value.toString());
    }
  }

  Future<void> save(bool unchanged) async {
    setState(() => saving = true);
    final session = data!['session'] as Map;
    final changes = session['changes'] as List;
    if (['outline', 'script', 'prompts'].contains(widget.kind)) {
      final candidate = jsonDecode(jsonEncode(session['original']));
      final choices = <Map<String, dynamic>>[];
      for (var i = 0; i < changes.length; i++) {
        final change = changes[i];
        final choice = unchanged ? 'revert' : decisions[i];
        final text = choice == 'apply'
            ? edits[i] ?? change['suggested']
            : change['original'];
        dynamic target = candidate;
        final path = change['path'] as List;
        for (final part in path.take(path.length - 1)) {
          target = target[part];
        }
        target[path.last] = text;
        choices.add({'path': path, 'choice': choice, 'text': text});
      }
      final result = await widget.controller
          .runAction('studio.resolve_content_review', {
            'name': widget.kind,
            'text': unchanged
                ? data!['sourceText']
                : widget.kind == 'outline'
                ? candidate['text']
                : const JsonEncoder.withIndent('  ').convert(candidate),
            'expected': data!['expected'],
            'report_digest': data!['reportDigest'],
            'decisions': choices,
          });
      if (!mounted) return;
      if (result == null) {
        Get.back();
      } else {
        setState(() {
          error = result;
          saving = false;
        });
      }
      return;
    }
    final result = await widget.controller.runAction(
      'review_changes.save_review',
      {
        'kind': widget.kind,
        'expected': data!['expected'],
        'approval_expected': data!['approvalExpected'],
        'decisions': [
          for (var i = 0; i < changes.length; i++)
            {
              'choice': unchanged ? 'revert' : decisions[i],
              'text': edits[i] ?? changes[i]['suggested'],
            },
        ],
      },
    );
    if (!mounted) return;
    if (result == null) {
      Get.back();
    } else {
      setState(() {
        error = result;
        saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = data?['session'] as Map?;
    final changes = session?['changes'] as List? ?? [];
    return AlertDialog(
      title: Text('AI Review (${widget.kind})'),
      content: SizedBox(
        width: 500,
        height: MediaQuery.sizeOf(context).height * .7,
        child: data == null && error == null
            ? const Center(child: CircularProgressIndicator())
            : SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (error != null)
                      Text(error!, style: const TextStyle(color: Colors.red)),
                    if (session == null)
                      const Text(
                        'Run AI Review first. The task list shows Reviewing progress.',
                      ),
                    if (session != null) ...[
                      ExpansionTile(
                        title: const Text('Original data'),
                        children: [
                          ValueEditor(
                            value: session['original'],
                            readOnly: true,
                            onChanged: (_) {},
                          ),
                        ],
                      ),
                      for (var i = 0; i < changes.length; i++)
                        Card(
                          child: Padding(
                            padding: const EdgeInsets.all(8),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                Text(
                                  (changes[i]['path'] as List).join(' / '),
                                  style: const TextStyle(
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                Text('Original: ${changes[i]['original']}'),
                                ContentTextField(
                                  value: edits[i] ?? changes[i]['suggested'],
                                  label: 'Suggested / edited replacement',
                                  onChanged: (text) => edits[i] = text,
                                ),
                                Text(changes[i]['reason']),
                                Wrap(
                                  spacing: 8,
                                  children: [
                                    for (final choice in ['apply', 'revert'])
                                      ChoiceChip(
                                        label: Text(choice),
                                        selected: decisions[i] == choice,
                                        onSelected: (_) => setState(
                                          () => decisions[i] = choice,
                                        ),
                                      ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                        ),
                      if (changes.isEmpty)
                        const Text('No suggested replacements.'),
                    ],
                  ],
                ),
              ),
      ),
      actions: [
        TextButton(onPressed: () => Get.back(), child: const Text('Close')),
        FilledButton(
          onPressed:
              session == null || saving || decisions.length != changes.length
              ? null
              : () => save(false),
          child: Text(saving ? 'Saving…' : 'Save and approve'),
        ),
        OutlinedButton(
          onPressed: session == null || saving ? null : () => save(true),
          child: const Text('Remain unchanged and approve'),
        ),
      ],
    );
  }
}
