import 'dart:async';
import 'package:get/get.dart';
import '../../util/controller/jobs_controller.dart';
import '../../workspace/controller/workspace_controller.dart';
import '../model/world_settings.dart';
import '../service/world_settings_service.dart';

class AdditionalSettingsController extends GetxController {
  final WorkspaceController workspace;
  final WorldSettingsService service;
  final JobsController? jobs;
  final String projectId;
  final settings = <WorldAddition>[].obs;
  final drafts = <String, String>{}.obs;
  final errors = <String, String>{}.obs;
  final pending = <String, String>{}.obs;
  final approvedWorld = false.obs;
  final loaded = false.obs;
  final versions = <String, String>{};
  final timers = <String, Timer>{};
  final saves = <String, Future<bool>>{};
  Worker? watcher, jobWatcher;
  final completed = <String>{};
  AdditionalSettingsController(
    this.workspace,
    this.service, {
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
    watcher = ever(workspace.snapshot, (snapshot) {
      final value = snapshot?.computed['worldSettings'];
      if (value is Map) {
        accept(WorldSettingsForm.fromJson(Map<String, dynamic>.from(value)));
      }
    });
    if (jobs != null) {
      completed.addAll(jobs!.jobs.where((j) => !j.active).map((j) => j.id));
      jobWatcher = ever(jobs!.jobs, (_) {
        for (final job in jobs!.jobs.where(
          (j) =>
              j.projectId == projectId &&
              !j.active &&
              !completed.contains(j.id),
        )) {
          completed.add(job.id);
          if (job.operation.startsWith('world.') &&
              job.arguments?['setting_id'] is String &&
              job.error != null) {
            errors[job.arguments!['setting_id']] = job.error!;
          }
        }
      });
    }
  }

  WorldAddition? item(String id) =>
      settings.firstWhereOrNull((item) => item.settingId == id);
  String busy(String id) =>
      pending[id] ??
      (jobs?.jobs
              .firstWhereOrNull(
                (j) =>
                    j.projectId == projectId &&
                    j.active &&
                    j.arguments?['setting_id'] == id,
              )
              ?.operation ??
          '');
  bool dirty(String id) => drafts[id] != item(id)?.text;
  bool canApprove(String id) =>
      busy(id).isEmpty &&
      item(id)?.approved == false &&
      (drafts[id]?.trim().isNotEmpty ?? false);
  bool canResolve(String id) =>
      busy(id).isEmpty &&
      !dirty(id) &&
      item(id)?.review?.current == true &&
      (item(id)?.review?.suggested.isNotEmpty ?? false) &&
      item(id)?.review?.decision == '';

  Future<void> resolveSuggestion(String id, String decision) async {
    if (!canResolve(id)) return;
    pending[id] = decision;
    errors.remove(id);
    try {
      accept(
        await service.resolveAddition(projectId, id, decision, versions[id]!),
        savedId: id,
      );
    } catch (error) {
      errors[id] = error.toString();
    } finally {
      pending.remove(id);
    }
  }

  Future<void> approveCurrent(String id) async {
    if (!canApprove(id)) return;
    if (!await save(id)) return;
    await run(id, 'world.approve_draft', {
      'setting_id': id,
      'text': drafts[id]!,
      'expected_setting': versions[id],
    });
  }

  void accept(WorldSettingsForm form, {String? savedId}) {
    for (final next in form.additions) {
      final old = item(next.settingId);
      final clean = old == null || drafts[next.settingId] == old.text;
      if (clean) drafts[next.settingId] = next.text;
      if (clean || next.settingId == savedId) {
        versions[next.settingId] = next.version;
      }
    }
    final ids = form.additions.map((a) => a.settingId).toSet();
    for (final id in drafts.keys.toList().where((id) => !ids.contains(id))) {
      timers.remove(id)?.cancel();
      drafts.remove(id);
      versions.remove(id);
      errors.remove(id);
    }
    settings.assignAll(form.additions);
    approvedWorld.value = form.approved;
    loaded.value = true;
  }

  Future<void> reload() async {
    try {
      accept(await service.load(projectId));
      errors.remove('page');
    } catch (error) {
      errors['page'] = error.toString();
    }
  }

  void edit(String id, String text) {
    drafts[id] = text;
    errors.remove(id);
    timers.remove(id)?.cancel();
    timers[id] = Timer(const Duration(milliseconds: 600), () => save(id));
  }

  Future<bool> save(String id) async {
    timers.remove(id)?.cancel();
    final previous = saves[id];
    if (previous != null && !await previous) return false;
    if (!dirty(id)) return true;
    if ((drafts[id] ?? '').trim().isEmpty) {
      errors[id] = 'Content cannot be empty.';
      return false;
    }
    final task = saveCurrent(id);
    saves[id] = task;
    try {
      return await task;
    } finally {
      if (identical(saves[id], task)) saves.remove(id);
    }
  }

  Future<bool> saveCurrent(String id) async {
    pending[id] = 'save';
    try {
      final form = await service.updateAddition(
        projectId,
        id,
        drafts[id]!,
        versions[id]!,
      );
      accept(form, savedId: id);
      errors.remove(id);
      return true;
    } catch (error) {
      errors[id] = error.toString();
      return false;
    } finally {
      pending.remove(id);
    }
  }

  Future<bool> add(String text) async {
    if (text.trim().isEmpty || !approvedWorld.value) return false;
    pending['add'] = 'add';
    try {
      accept(await service.add(projectId, text));
      errors.remove('page');
      return true;
    } catch (error) {
      errors['page'] = error.toString();
      return false;
    } finally {
      pending.remove('add');
    }
  }

  Future<void> review(String id) async {
    if (!await save(id)) return;
    await run(id, 'world.review', {
      'setting_id': id,
      'expected_setting': versions[id],
    });
  }

  Future<void> approve(
    String id,
    String text,
    String decision,
    String expected,
  ) => run(id, 'world.approve_setting', {
    'setting_id': id,
    'text': text,
    'decision': decision,
    'expected_setting': expected,
  });

  Future<void> reloadDraft(String id) async {
    timers.remove(id)?.cancel();
    final current = item(id);
    if (current != null) drafts[id] = current.text;
    await reload();
    if (item(id) != null) versions[id] = item(id)!.version;
    errors.remove(id);
  }

  Future<void> run(
    String id,
    String operation,
    Map<String, dynamic> args,
  ) async {
    pending[id] = operation;
    errors.remove(id);
    try {
      final error = await workspace.runPrepared(operation, args);
      if (error != null) errors[id] = error;
    } catch (error) {
      errors[id] = error.toString();
    } finally {
      pending.remove(id);
    }
  }

  Future<void> remove(String id) async {
    timers.remove(id)?.cancel();
    final previous = saves[id];
    if (previous != null) await previous;
    pending[id] = 'remove';
    errors.remove(id);
    try {
      accept(await service.removeAddition(projectId, id, versions[id]!));
    } catch (error) {
      errors[id] = error.toString();
    } finally {
      pending.remove(id);
    }
  }

  @override
  void onClose() {
    for (final timer in timers.values) {
      timer.cancel();
    }
    watcher?.dispose();
    jobWatcher?.dispose();
    super.onClose();
  }
}
