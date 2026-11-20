import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/controller/jobs_controller.dart';
import '../../util/service/api_client.dart';
import '../../util/view/content_text_field.dart';
import '../../workspace/controller/workspace_controller.dart';
import '../controller/additional_settings_controller.dart';
import '../model/world_settings.dart';
import '../service/world_settings_service.dart';

class AdditionalSettingsView extends StatelessWidget {
  final WorkspaceController workspace;
  const AdditionalSettingsView(this.workspace, {super.key});
  @override
  Widget build(BuildContext context) {
    final tag = 'additional-settings-${workspace.projectId.value}';
    final controller = Get.isRegistered<AdditionalSettingsController>(tag: tag)
        ? Get.find<AdditionalSettingsController>(tag: tag)
        : Get.put(
            AdditionalSettingsController(
              workspace,
              WorldSettingsService(Get.find<ApiClient>()),
              projectId: workspace.projectId.value,
              jobs: Get.find<JobsController>(),
            ),
            tag: tag,
            permanent: true,
          );
    return AdditionalSettingsEditor(controller);
  }
}

class AdditionalSettingsEditor extends StatelessWidget {
  final AdditionalSettingsController controller;
  const AdditionalSettingsEditor(this.controller, {super.key});
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(8),
    child: Obx(() {
      if (!controller.loaded.value && controller.errors['page'] == null) {
        return const Center(child: CircularProgressIndicator());
      }
      final draft = controller.settings.where((a) => !a.approved).toList();
      final approved = controller.settings.where((a) => a.approved).toList();
      Widget list(String title, List<WorldAddition> items) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            '$title (${items.length})',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 12),
          if (items.isEmpty)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('No settings yet.'),
            ),
          ListView.separated(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            itemCount: items.length,
            separatorBuilder: (_, index) => const SizedBox(height: 12),
            itemBuilder: (context, index) => card(context, items[index]),
          ),
        ],
      );
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: Tooltip(
              message: controller.approvedWorld.value
                  ? 'Add an additional World setting'
                  : 'Approve World settings first',
              child: OutlinedButton.icon(
                key: const ValueKey('add-world-setting'),
                onPressed:
                    controller.approvedWorld.value &&
                        !controller.pending.containsKey('add')
                    ? () => Get.dialog(_AddSettingDialog(controller))
                    : null,
                icon: const Icon(Icons.add),
                label: const Text('Add Setting'),
              ),
            ),
          ),
          if (!controller.approvedWorld.value)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('Approve World settings before adding details.'),
            ),
          if (controller.errors['page'] != null)
            Text(
              controller.errors['page']!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          const Divider(height: 32),
          LayoutBuilder(
            builder: (context, constraints) {
              if (constraints.maxWidth < 760) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    list('Draft settings', draft),
                    const SizedBox(height: 24),
                    list('Approved settings', approved),
                  ],
                );
              }
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: list('Draft settings', draft)),
                  const SizedBox(width: 16),
                  Expanded(child: list('Approved settings', approved)),
                ],
              );
            },
          ),
        ],
      );
    }),
  );

  Widget card(BuildContext context, WorldAddition item) {
    final id = item.settingId;
    final busy = controller.busy(id);
    final saving = busy == 'save';
    return Card(
      key: ValueKey('additional-$id'),
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SelectableText(
              '${item.approved ? 'Approved' : 'Draft'} · $id',
              style: TextStyle(
                fontWeight: FontWeight.bold,
                color: item.approved ? Colors.green : Colors.orange,
              ),
            ),
            const SizedBox(height: 12),
            if (item.approved)
              SelectableText(item.text)
            else ...[
              LayoutBuilder(
                builder: (context, constraints) {
                  final content = ContentTextField(
                    key: ValueKey('addition-text-$id'),
                    label: 'Content',
                    value: controller.drafts[id] ?? item.text,
                    stacked: true,
                    minLines: 3,
                    maxLines: 12,
                    dirty: controller.dirty(id),
                    enabled: busy.isEmpty || saving,
                    labelAction: OutlinedButton(
                      key: ValueKey('addition-review-$id'),
                      onPressed: busy.isEmpty
                          ? () => controller.review(id)
                          : null,
                      child: Text(
                        busy == 'world.review' ? 'Reviewing…' : 'AI Review',
                      ),
                    ),
                    onChanged: (text) => controller.edit(id, text),
                  );
                  final suggestion = Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Wrap(
                        spacing: 8,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          const Text(
                            'Suggestion:',
                            style: TextStyle(fontWeight: FontWeight.bold),
                          ),
                          OutlinedButton(
                            key: ValueKey('addition-apply-$id'),
                            onPressed: controller.canResolve(id)
                                ? () =>
                                      controller.resolveSuggestion(id, 'apply')
                                : null,
                            child: Text(
                              busy == 'apply' ? 'Applying…' : 'Apply',
                            ),
                          ),
                          OutlinedButton(
                            key: ValueKey('addition-discard-$id'),
                            onPressed: controller.canResolve(id)
                                ? () =>
                                      controller.resolveSuggestion(id, 'revert')
                                : null,
                            child: Text(
                              busy == 'revert' ? 'Discarding…' : 'Discard',
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Container(
                        key: ValueKey('addition-suggestion-$id'),
                        constraints: const BoxConstraints(minHeight: 100),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          border: Border.all(
                            color: Theme.of(context).colorScheme.outline,
                          ),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: SelectableText(item.review?.suggested ?? ''),
                      ),
                    ],
                  );
                  if (constraints.maxWidth < 480) {
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        content,
                        const SizedBox(height: 16),
                        suggestion,
                      ],
                    );
                  }
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(child: content),
                      const SizedBox(width: 16),
                      Expanded(child: suggestion),
                    ],
                  );
                },
              ),
              const SizedBox(height: 8),
              Text(
                saving
                    ? 'Saving…'
                    : controller.dirty(id)
                    ? 'Unsaved changes'
                    : 'Saved',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              if (controller.errors[id] != null)
                Text(
                  controller.errors[id]!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              if (controller.errors[id] != null)
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton(
                    onPressed: busy.isEmpty
                        ? () => controller.reloadDraft(id)
                        : null,
                    child: const Text('Reload saved content'),
                  ),
                ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  Tooltip(
                    message: controller.canApprove(id)
                        ? 'Approve current content; consistency checks run automatically'
                        : 'Enter content and wait for the current task to finish',
                    child: OutlinedButton(
                      onPressed: controller.canApprove(id)
                          ? () => controller.approveCurrent(id)
                          : null,
                      child: Text(
                        busy == 'world.approve_draft'
                            ? 'Approving…'
                            : 'Approve',
                      ),
                    ),
                  ),
                  OutlinedButton(
                    onPressed: busy.isEmpty
                        ? () => remove(context, item)
                        : null,
                    child: Text(busy == 'remove' ? 'Removing…' : 'Remove'),
                  ),
                ],
              ),
              if (item.review != null) ...[
                const SizedBox(height: 12),
                SelectableText(item.review!.summary),
                if (!item.review!.current)
                  const Text('Review is outdated. Run AI Review again.'),
                for (final conflict in item.review!.conflicts)
                  Text(
                    conflict,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
              ],
            ],
          ],
        ),
      ),
    );
  }

  Future<void> remove(BuildContext context, WorldAddition item) async {
    final confirmed = await Get.dialog<bool>(
      AlertDialog(
        title: const Text('Remove draft setting?'),
        content: SelectableText(
          'Remove draft ${item.settingId}? Approved settings remain unchanged.',
        ),
        actions: [
          TextButton(
            onPressed: () => Get.back(result: false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Get.back(result: true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed == true) await controller.remove(item.settingId);
  }
}

class _AddSettingDialog extends StatefulWidget {
  final AdditionalSettingsController controller;
  const _AddSettingDialog(this.controller);
  @override
  State<_AddSettingDialog> createState() => _AddSettingDialogState();
}

class _AddSettingDialogState extends State<_AddSettingDialog> {
  String text = '', error = '';
  bool saving = false;
  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Add Setting'),
    content: SizedBox(
      width: 600,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ContentTextField(
            label: 'Content',
            value: text,
            stacked: true,
            minLines: 4,
            maxLines: 12,
            enabled: !saving,
            onChanged: (value) => setState(() => text = value),
          ),
          if (error.isNotEmpty)
            Text(
              error,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: saving ? null : () => Get.back(),
        child: const Text('Cancel'),
      ),
      TextButton(
        onPressed: saving || text.trim().isEmpty
            ? null
            : () async {
                setState(() => saving = true);
                final added = await widget.controller.add(text);
                if (!mounted) return;
                if (added) {
                  Get.back();
                } else {
                  setState(() {
                    saving = false;
                    error =
                        widget.controller.errors['page'] ??
                        'Could not add setting.';
                  });
                }
              },
        child: Text(saving ? 'Adding…' : 'Add'),
      ),
    ],
  );
}
