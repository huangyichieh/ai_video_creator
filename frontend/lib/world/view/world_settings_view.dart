import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/controller/jobs_controller.dart';
import '../../util/service/api_client.dart';
import '../../util/view/content_text_field.dart';
import '../../util/view/loading.dart';
import '../../workspace/controller/workspace_controller.dart';
import '../controller/world_settings_controller.dart';
import '../model/world_settings.dart';
import '../service/world_settings_service.dart';
import 'world_review_diff.dart';

class WorldSettingsView extends StatelessWidget {
  final WorkspaceController workspace;
  const WorldSettingsView(this.workspace, {super.key});
  @override
  Widget build(BuildContext context) {
    final tag = 'world-settings-${workspace.projectId.value}';
    final controller = Get.isRegistered<WorldSettingsController>(tag: tag)
        ? Get.find<WorldSettingsController>(tag: tag)
        : Get.put(
            WorldSettingsController(
              WorldSettingsService(Get.find<ApiClient>()),
              workspace,
              projectId: workspace.projectId.value,
              jobs: Get.find<JobsController>(),
            ),
            tag: tag,
            permanent: true,
          );
    return WorldSettingsEditor(controller);
  }
}

class WorldSettingsEditor extends StatelessWidget {
  final WorldSettingsController controller;
  const WorldSettingsEditor(this.controller, {super.key});
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(8),
    child: Obx(() {
      final form = controller.form.value;
      if (form == null) {
        return Column(
          children: [
            if (controller.error.value != null) Text(controller.error.value!),
            if (controller.error.value == null)
              const AppLoadingIcon()
            else
              TextButton(
                onPressed: controller.reload,
                child: const Text('Reload settings'),
              ),
          ],
        );
      }
      final busy = controller.busyOperation;
      final style = OutlinedButton.styleFrom(
        foregroundColor: const Color(0xff64b5f6),
        disabledForegroundColor: Colors.grey,
      );
      Widget field(String key, String label) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: ContentTextField(
          key: ValueKey('world-field-$key'),
          label: label,
          stacked: true,
          description: worldFieldHelp[key],
          labelAction: key == 'premise'
              ? Tooltip(
                  message:
                      'Fill the six fields below from your premise. Saves the current draft; review before approval.',
                  child: OutlinedButton(
                    key: const ValueKey('world-auto-generate'),
                    style: style,
                    onPressed: controller.canGenerate
                        ? controller.generate
                        : null,
                    child: Text(
                      busy == 'world.generate_settings'
                          ? 'Generating…'
                          : 'Auto generate',
                    ),
                  ),
                )
              : null,
          value: controller.draft[key] ?? '',
          readOnly: form.approved && key != 'name',
          enabled: !controller.busy,
          dirty: controller.dirtyField(key),
          minLines: key == 'name' || key == 'defaultLanguage' ? 1 : 2,
          maxLines: key == 'name' || key == 'defaultLanguage' ? 1 : 12,
          onChanged: (text) => controller.edit(key, text),
        ),
      );
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('World Setting', style: Theme.of(context).textTheme.titleLarge),
          if (form.approved)
            const Text('Approved', style: TextStyle(color: Colors.green)),
          const SizedBox(height: 8),
          LayoutBuilder(
            builder: (context, constraints) {
              Widget paired(String left, String right) {
                final first = field(
                  left,
                  worldFields[left] ?? 'Default language',
                );
                final second = field(
                  right,
                  worldFields[right] ?? 'Default language',
                );
                if (constraints.maxWidth < 700) {
                  return Column(children: [first, second]);
                }
                return Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(child: first),
                    const SizedBox(width: 16),
                    Expanded(child: second),
                  ],
                );
              }

              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  paired('name', 'defaultLanguage'),
                  paired('premise', 'eraSetting'),
                  paired('coreRules', 'technologyMagicLimits'),
                  paired('societyKeyPlaces', 'tone'),
                  field(
                    'forbiddenContradictions',
                    worldFields['forbiddenContradictions']!,
                  ),
                ],
              );
            },
          ),
          if (controller.dirty)
            const Text(
              'Unsaved changes',
              style: TextStyle(color: Color(0xff64b5f6)),
            ),
          if (controller.error.value != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                controller.error.value!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          if (controller.stale.value)
            TextButton(
              onPressed: controller.reload,
              child: const Text('Reload saved settings'),
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              OutlinedButton(
                key: const ValueKey('world-save'),
                style: style,
                onPressed: controller.canSave ? controller.save : null,
                child: Text(busy == 'save' ? 'Saving…' : 'Save'),
              ),
              const VerticalDivider(),
              OutlinedButton(
                key: const ValueKey('world-review'),
                style: style,
                onPressed: controller.canReview
                    ? () => controller.review()
                    : null,
                child: Text(
                  busy == 'world.review' && !controller.modifying
                      ? 'Reviewing…'
                      : 'AI Review',
                ),
              ),
              const VerticalDivider(),
              OutlinedButton(
                key: const ValueKey('world-modify'),
                style: style,
                onPressed: controller.canReview ? () => modify(context) : null,
                child: Text(
                  busy == 'world.review' && controller.modifying
                      ? 'Modifying…'
                      : 'Modify with prompt',
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          if (form.review != null)
            WorldReviewDiff(controller)
          else
            const Text('AI Review suggestions will appear here after review.'),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.center,
            child: OutlinedButton(
              key: const ValueKey('world-approve'),
              style: style,
              onPressed: controller.canApprove ? controller.approve : null,
              child: Text(
                form.approved
                    ? 'Approved'
                    : busy == 'world.approve_base'
                    ? 'Approving…'
                    : 'Approve settings',
              ),
            ),
          ),
        ],
      );
    }),
  );

  Future<void> modify(BuildContext context) async {
    final request = await Get.dialog<String>(const _ModifyPromptDialog());
    if (request != null && request.trim().isNotEmpty) {
      await controller.review(instructions: request.trim());
    }
  }
}

class _ModifyPromptDialog extends StatefulWidget {
  const _ModifyPromptDialog();
  @override
  State<_ModifyPromptDialog> createState() => _ModifyPromptDialogState();
}

class _ModifyPromptDialogState extends State<_ModifyPromptDialog> {
  String prompt = '';
  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Modify with prompt'),
    content: SizedBox(
      width: 600,
      child: ContentTextField(
        label: 'Modification request',
        value: prompt,
        minLines: 2,
        maxLines: 12,
        onChanged: (value) => setState(() => prompt = value),
      ),
    ),
    actions: [
      TextButton(onPressed: () => Get.back(), child: const Text('Cancel')),
      TextButton(
        onPressed: prompt.trim().isEmpty
            ? null
            : () => Get.back(result: prompt),
        child: const Text('Generate suggestions'),
      ),
    ],
  );
}
