import '../util/view/field_row.dart';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../util/service/api_client.dart';
import '../util/view/dialog.dart';
import '../workspace/controller/workspace_controller.dart';

class KeyDialog extends StatefulWidget {
  const KeyDialog({super.key});
  @override
  State<KeyDialog> createState() => _KeyDialogState();
}

class _KeyDialogState extends State<KeyDialog> {
  final api = Get.find<ApiClient>();
  final fields = <String, TextEditingController>{};
  List<dynamic> keys = [];
  Map<String, dynamic> quotas = {};
  bool loading = true, saving = false;
  final cleared = <String>{};
  String? error;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final result = await api.request('GET', '/api/settings/keys');
      if (mounted) {
        setState(() {
          keys = result;
          loading = false;
        });
      }
      final balances = await api.request('GET', '/api/settings/quotas');
      if (mounted) setState(() => quotas = Map<String, dynamic>.from(balances));
    } catch (value) {
      if (mounted) {
        setState(() {
          error = value.toString();
          loading = false;
        });
      }
    }
  }

  Future<void> save() async {
    setState(() => saving = true);
    try {
      await api.request(
        'PUT',
        '/api/settings/keys',
        data: {
          'values': {
            for (final field in fields.entries)
              if (field.value.text.trim().isNotEmpty)
                field.key: field.value.text,
            for (final key in cleared) key: '',
          },
        },
      );
      for (final field in fields.values) {
        field.clear();
      }
      cleared.clear();
      await load();
      if (mounted) setState(() => saving = false);
    } catch (value) {
      if (mounted) {
        setState(() {
          error = value.toString();
          saving = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('API key'),
    content: SizedBox(
      width: 500,
      child: SingleChildScrollView(
        child: loading
            ? const Center(child: CircularProgressIndicator())
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (error != null)
                    Text(error!, style: const TextStyle(color: Colors.red)),
                  for (final group in [
                    ['OPENAI_API_KEY', 'OPENAI_ADMIN_KEY'],
                    ['RUNWAYML_API_SECRET'],
                    ['ELEVENLABS_API_KEY'],
                  ])
                    Card(
                      color: Theme.of(context).colorScheme.surfaceContainerHigh,
                      child: Padding(
                        padding: const EdgeInsets.all(8),
                        child: Column(
                          children: [
                            for (final key in group) ...[
                              Align(
                                alignment: Alignment.centerLeft,
                                child: Text(
                                  key,
                                  style: const TextStyle(
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ),
                              if (!keys.any(
                                (row) =>
                                    row['name'] == key &&
                                    row['provided'] == true,
                              ))
                                const Align(
                                  alignment: Alignment.centerLeft,
                                  child: Text(
                                    'Key is not provided',
                                    style: TextStyle(color: Colors.red),
                                  ),
                                ),
                              Align(
                                alignment: Alignment.centerLeft,
                                child: Text(
                                  keys.firstWhereOrNull(
                                        (row) => row['name'] == key,
                                      )?['masked'] ??
                                      '',
                                ),
                              ),
                              const SizedBox(height: 8),
                              FieldRow(
                                label: 'New key (blank keeps saved key)',
                                child: TextField(
                                  controller: fields.putIfAbsent(
                                    key,
                                    () => TextEditingController(),
                                  ),
                                  obscureText: true,
                                  decoration: const InputDecoration(),
                                ),
                              ),
                              TextButton(
                                onPressed: () => setState(
                                  () => cleared.contains(key)
                                      ? cleared.remove(key)
                                      : cleared.add(key),
                                ),
                                child: Text(
                                  cleared.contains(key)
                                      ? 'Will clear on save (undo)'
                                      : 'Clear saved key',
                                ),
                              ),
                              if (quotas.containsKey(key))
                                quotaView(quotas[key]),
                              const SizedBox(height: 8),
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
      TextButton(onPressed: () => Get.back(), child: const Text('Close')),
      FilledButton(
        onPressed: saving ? null : save,
        child: Text(saving ? 'Saving…' : 'Save'),
      ),
    ],
  );
  Widget quotaView(dynamic response) {
    if (response['error'] != null) {
      return Text(response['error'], style: const TextStyle(color: Colors.red));
    }
    final value = response['value'] as Map? ?? {};
    String amount(dynamic number) =>
        double.tryParse(number.toString())?.toStringAsFixed(2) ??
        number.toString();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (value['used'] != null)
          Text(
            'Spent: ${amount(value['used'])} ${value['unit']}',
            style: const TextStyle(color: Colors.red),
          ),
        if (value['last_month_used'] != null)
          Text(
            'Last month (${value['last_month_label']}): ${amount(value['last_month_used'])} USD',
            style: const TextStyle(color: Colors.red, fontSize: 12),
          ),
        if (value['remaining'] != null)
          Text(
            'Remaining: ${amount(value['remaining'])} ${value['unit']}',
            style: const TextStyle(color: Colors.green),
          ),
        if (value['resets_at'] != null)
          Text(
            'Resets: ${value['resets_at']}',
            style: const TextStyle(fontSize: 12),
          ),
        Text(
          'Checked: ${value['checked_at'] ?? ''}',
          style: const TextStyle(fontSize: 12),
        ),
      ],
    );
  }

  @override
  void dispose() {
    for (final field in fields.values) {
      field.dispose();
    }
    super.dispose();
  }
}

class GenerationPreferencesDialog extends StatefulWidget {
  final WorkspaceController controller;
  const GenerationPreferencesDialog(this.controller, {super.key});
  @override
  State<GenerationPreferencesDialog> createState() =>
      _GenerationPreferencesDialogState();
}

class _GenerationPreferencesDialogState
    extends State<GenerationPreferencesDialog> {
  late Map<String, dynamic> selected;
  late Map<String, dynamic> models, processes;
  bool demo = false;
  @override
  void initState() {
    super.initState();
    final value = widget.controller.snapshot.value!.modelPreferences;
    demo = value['demo'] == true;
    selected = Map<String, dynamic>.from(value['selected']);
    models = Map<String, dynamic>.from(value['models']);
    processes = Map<String, dynamic>.from(value['processes']);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Generation preferences · OpenAI'),
    content: SizedBox(
      width: 500,
      height: MediaQuery.sizeOf(context).height * .7,
      child: SingleChildScrollView(
        child: Column(
          children: [
            const Tooltip(
              message:
                  'Sol: balance of quality/cost. Astra: highest reasoning quality, slower and more expensive. Luna: economical and fast. GPT-5.6 Sol: mature alternative.',
              child: Icon(Icons.info_outline),
            ),
            FieldRow(
              label: 'Demo story and reviews (offline)',
              child: Align(
                alignment: Alignment.centerLeft,
                child: Switch(
                  value: demo,
                  onChanged: (value) => setState(() => demo = value),
                ),
              ),
            ),
            Row(
              children: [
                TextButton(
                  onPressed: () => setState(
                    () => selected = {
                      for (final key in processes.keys) key: 'gpt-6-luna',
                    },
                  ),
                  child: const Text('Economic'),
                ),
                TextButton(
                  onPressed: () => setState(
                    () => selected = {
                      for (final key in processes.keys)
                        key: key == 'metadata' ? 'gpt-6-luna' : 'gpt-6.1-sol',
                    },
                  ),
                  child: const Text('Balance'),
                ),
              ],
            ),
            for (final entry in processes.entries)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: FieldRow(
                  label: entry.value.toString(),
                  child: DropdownButtonFormField<String>(
                    key: ValueKey('${entry.key}-${selected[entry.key]}'),
                    initialValue: selected[entry.key],
                    isExpanded: true,
                    decoration: InputDecoration(),
                    items: models.entries
                        .map(
                          (item) => DropdownMenuItem(
                            value: item.key,
                            child: Text(item.value.toString()),
                          ),
                        )
                        .toList(),
                    onChanged: (value) => selected[entry.key] = value,
                  ),
                ),
              ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(onPressed: () => Get.back(), child: const Text('Close')),
      FilledButton(
        onPressed: () async {
          final error = await widget.controller.runAction(
            'generation_preferences.save_preferences',
            {'models': selected, 'demo': demo},
          );
          if (error == null) {
            Get.back();
          } else {
            Get.dialog(TextDialog('Save failed', error));
          }
        },
        child: const Text('Save'),
      ),
    ],
  );
}
