import 'package:get/get.dart';
import '../../projects/controller/project_controller.dart';
import '../../util/controller/jobs_controller.dart';
import '../../workspace/controller/workspace_controller.dart';
import '../model/world_settings.dart';
import '../service/world_settings_service.dart';

class WorldSettingsController extends GetxController {
  final String projectId;
  final WorldSettingsService service;
  final WorkspaceController workspace;
  final JobsController? jobs;
  final form = Rx<WorldSettingsForm?>(null);
  final draft = <String, String>{}.obs;
  final proposed = <String, String>{}.obs;
  final decisions = <String, String>{}.obs;
  final error = Rx<String?>(null);
  final pending = ''.obs;
  final stale = false.obs;
  bool modifying = false;
  Worker? snapshotWatcher, jobWatcher;
  final completed = <String>{};
  WorldSettingsController(
    this.service,
    this.workspace, {
    required this.projectId,
    this.jobs,
  });

  @override
  void onInit() {
    super.onInit();
    final initial = workspace.snapshot.value?.computed['worldSettings'];
    if (initial is Map) {
      accept(WorldSettingsForm.fromJson(Map<String, dynamic>.from(initial)));
    } else {
      reload();
    }
    snapshotWatcher = ever(workspace.snapshot, (snapshot) {
      final value = snapshot?.computed['worldSettings'];
      if (value is Map) {
        accept(WorldSettingsForm.fromJson(Map<String, dynamic>.from(value)));
      }
    });
    if (jobs != null) {
      completed.addAll(
        jobs!.jobs.where((job) => !job.active).map((job) => job.id),
      );
      jobWatcher = ever(jobs!.jobs, (_) {
        for (final job in jobs!.jobs.where(
          (job) =>
              job.projectId == projectId &&
              job.operation.startsWith('world.') &&
              !job.active &&
              !completed.contains(job.id),
        )) {
          completed.add(job.id);
          if (job.error != null) error.value = job.error;
        }
      });
    }
  }

  String get busyOperation {
    if (pending.value.isNotEmpty) return pending.value;
    final active = jobs?.jobs.firstWhereOrNull(
      (job) =>
          job.projectId == projectId &&
          job.active &&
          job.operation.startsWith('world.'),
    );
    return active?.operation ?? '';
  }

  bool get busy => busyOperation.isNotEmpty;
  bool get approved => form.value?.approved == true;
  String saved(String field) => field == 'defaultLanguage'
      ? form.value?.defaultLanguage ?? ''
      : form.value?.settings.fields[field] ?? '';
  bool dirtyField(String field) => draft[field] != saved(field);
  bool get dirty => form.value != null && draft.keys.any(dirtyField);
  bool get valid =>
      (draft['name'] ?? '').trim().isNotEmpty &&
      !(draft['name'] ?? '').contains(RegExp(r'[\r\n]')) &&
      (draft['defaultLanguage'] ?? '').trim().isNotEmpty &&
      worldFields.keys
          .where((key) => key != 'name')
          .any((key) => (draft[key] ?? '').trim().isNotEmpty);
  bool get canSave => dirty && valid && !busy && !stale.value;
  bool get canReview =>
      form.value != null &&
      !approved &&
      valid &&
      !dirty &&
      !busy &&
      !stale.value;
  bool get canGenerate =>
      form.value != null &&
      !approved &&
      valid &&
      (draft['premise'] ?? '').trim().isNotEmpty &&
      !busy &&
      !stale.value;
  Set<String> get changedFields {
    final review = form.value?.review;
    if (review == null) return {};
    return worldFields.keys
        .where(
          (key) =>
              review.original.fields[key] != review.suggested.fields[key] ||
              review.original.fields[key] != proposed[key],
        )
        .toSet();
  }

  bool get resolved => changedFields.every(
    (key) => decisions[key] == 'apply' || decisions[key] == 'revert',
  );
  bool get canApprove =>
      canReview &&
      form.value?.review?.current == true &&
      resolved &&
      (proposed['name'] ?? '').trim().isNotEmpty &&
      !(proposed['name'] ?? '').contains(RegExp(r'[\r\n]')) &&
      worldFields.keys
          .where((key) => key != 'name')
          .any((key) => (proposed[key] ?? '').trim().isNotEmpty);

  void accept(WorldSettingsForm next, {bool force = false}) {
    if (!force && form.value?.version == next.version) return;
    if (!force && dirty) {
      stale.value = true;
      error.value =
          'World settings changed elsewhere. Reload the saved version before continuing.';
      return;
    }
    form.value = next;
    draft.assignAll({
      ...next.settings.fields,
      'defaultLanguage': next.defaultLanguage,
    });
    final review = next.review;
    proposed.assignAll(
      (review?.accepted ?? review?.suggested ?? next.settings).fields,
    );
    decisions.clear();
    if (review != null && review.decision.isNotEmpty && review.current) {
      for (final key in changedFields) {
        decisions[key] = proposed[key] == review.original.fields[key]
            ? 'revert'
            : 'apply';
      }
    }
    stale.value = false;
  }

  void edit(String field, String value) => draft[field] = value;
  void editProposal(String field, String value) {
    proposed[field] = value;
    decisions.remove(field);
  }

  void choose(String field, String choice) {
    final review = form.value!.review!;
    if (choice == 'revert') {
      proposed[field] = review.original.fields[field]!;
    } else if (decisions[field] == 'revert') {
      proposed[field] = review.suggested.fields[field]!;
    }
    decisions[field] = choice;
  }

  void chooseAll(String choice) {
    for (final key in changedFields.toList()) {
      choose(key, choice);
    }
  }

  Future<void> reload() async {
    try {
      accept(await service.load(projectId), force: true);
      error.value = null;
    } catch (value) {
      error.value = value.toString();
    }
  }

  Future<void> save() async {
    if (!canSave) return;
    pending.value = 'save';
    error.value = null;
    try {
      final settings = WorldSettingsData.fromJson({
        for (final key in worldFields.keys) key: draft[key] ?? '',
      });
      accept(
        await service.save(
          projectId,
          settings,
          draft['defaultLanguage']!,
          form.value!.version,
        ),
        force: true,
      );
      if (Get.isRegistered<ProjectController>()) {
        await Get.find<ProjectController>().loadProjects();
      }
      await workspace.loadWorkspace();
    } catch (value) {
      error.value = value.toString();
    } finally {
      pending.value = '';
    }
  }

  Future<void> review({String instructions = ''}) async {
    if (!canReview) return;
    modifying = instructions.trim().isNotEmpty;
    pending.value = 'world.review';
    error.value = null;
    try {
      final result = await workspace.runPrepared('world.review', {
        'instructions': instructions,
        'expected_state': form.value!.version,
      });
      if (result != null) error.value = result;
    } catch (value) {
      error.value = value.toString();
    } finally {
      pending.value = '';
    }
  }

  Future<void> generate() async {
    if (!canGenerate) return;
    pending.value = 'world.generate_settings';
    error.value = null;
    try {
      // Save current inputs before queuing so page changes cannot lose the premise.
      if (dirty) {
        accept(
          await service.save(
            projectId,
            WorldSettingsData.fromJson({
              for (final key in worldFields.keys) key: draft[key] ?? '',
            }),
            draft['defaultLanguage']!,
            form.value!.version,
          ),
          force: true,
        );
        if (Get.isRegistered<ProjectController>()) {
          await Get.find<ProjectController>().loadProjects();
        }
      }
      final result = await workspace.runPrepared('world.generate_settings', {
        'expected_state': form.value!.version,
      });
      if (result != null) error.value = result;
    } catch (value) {
      error.value = value.toString();
    } finally {
      pending.value = '';
    }
  }

  Future<void> approve() async {
    if (!canApprove) return;
    pending.value = 'world.approve_base';
    error.value = null;
    try {
      final original = form.value!.review!.original.fields;
      final unchanged = worldFields.keys.every(
        (key) => proposed[key] == original[key],
      );
      final selected = WorldSettingsData.fromJson(
        Map<String, dynamic>.from(proposed),
      );
      accept(
        await service.resolve(
          projectId,
          selected,
          unchanged ? 'revert' : 'apply',
          form.value!.version,
        ),
        force: true,
      );
      final result = await workspace.runPrepared('world.approve_base', {
        'expected_state': form.value!.version,
      });
      if (result != null) error.value = result;
      await workspace.loadWorkspace();
    } catch (value) {
      error.value = value.toString();
    } finally {
      pending.value = '';
    }
  }

  @override
  void onClose() {
    snapshotWatcher?.dispose();
    jobWatcher?.dispose();
    super.onClose();
  }
}
