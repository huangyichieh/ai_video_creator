import 'package:get/get.dart';
import 'dart:convert';
import '../../projects/controller/project_controller.dart';
import '../../util/controller/jobs_controller.dart';
import '../model/workspace_snapshot.dart';
import '../service/workspace_service.dart';

class WorkspaceController extends GetxController {
  final WorkspaceService service;
  final String page;
  final String initialProjectId;
  final loading = false.obs;
  final errorMessage = Rx<String?>(null);
  final snapshot = Rx<WorkspaceSnapshot?>(null);
  final projectId = ''.obs;
  final selectedTab = 0.obs;
  final pendingCommands = <String>{}.obs;
  String commandKey(String operation, Map<String, dynamic> args) =>
      '$operation:${jsonEncode(args)}';
  Future<String?> runPrepared(
    String operation,
    Map<String, dynamic> args,
  ) async {
    final key = commandKey(operation, args);
    pendingCommands.add(key);
    try {
      final prepared = await service.prepare(projectId.value, operation, args);
      return await runAction(
        operation,
        Map<String, dynamic>.from(prepared['values']),
      );
    } catch (value) {
      return value.toString();
    } finally {
      pendingCommands.remove(key);
    }
  }

  final completed = <String>{};
  Worker? watcher;
  int requestVersion = 0;
  WorkspaceController(this.service, this.page, this.initialProjectId);
  @override
  void onInit() {
    super.onInit();
    projectId.value = initialProjectId;
    final jobs = Get.find<JobsController>();
    completed.addAll(
      jobs.jobs.where((job) => !job.active).map((job) => job.id),
    );
    watcher = ever(jobs.jobs, (_) {
      final changed = jobs.jobs
          .where(
            (job) =>
                (job.projectId == projectId.value ||
                    job.projectId == initialProjectId) &&
                !job.active &&
                !completed.contains(job.id),
          )
          .toList();
      if (changed.isNotEmpty) {
        completed.addAll(changed.map((job) => job.id));
        loadWorkspace();
      }
    });
    loadWorkspace();
  }

  Future<String?> loadWorkspace() async {
    final request = ++requestVersion;
    if (snapshot.value == null) loading.value = true;
    try {
      final projects = Get.find<ProjectController>();
      if (projects.projects.isEmpty) await projects.loadProjects();
      var selected = projects.projects.firstWhereOrNull(
        (p) => p.projectId == projectId.value,
      );
      if (selected == null && snapshot.value?.project.worldProjectId != null) {
        projectId.value = snapshot.value!.project.worldProjectId!;
        selected = projects.projects.firstWhereOrNull(
          (p) => p.projectId == projectId.value,
        );
      }
      if (['story', 'animations', 'final'].contains(page) &&
          selected?.worldProjectId == null) {
        final episodes =
            projects.projects
                .where((p) => p.worldProjectId == projectId.value)
                .toList()
              ..sort(
                (a, b) => (b.createdAt ?? b.updatedAt).compareTo(
                  a.createdAt ?? a.updatedAt,
                ),
              );
        if (episodes.isNotEmpty) projectId.value = episodes.first.projectId;
      }
      final value = await service.load(projectId.value, page);
      if (request == requestVersion) {
        snapshot.value = value;
        errorMessage.value = null;
        projects.updateProject(value.project);
        final root = value.project.worldProjectId ?? value.project.projectId;
        if (Get.currentRoute.split('?').first == '/$page' &&
            projects.selectedProjectId.value == root &&
            Get.parameters['project'] == initialProjectId) {
          projects.rememberProject(value.project.projectId, page);
        }
      }
      return null;
    } catch (error) {
      if (request == requestVersion) errorMessage.value = error.toString();
      return error.toString();
    } finally {
      if (request == requestVersion) loading.value = false;
    }
  }

  Future<String?> selectEpisode(String id) async {
    projectId.value = id;
    return loadWorkspace();
  }

  Future<String?> runAction(
    String operation,
    Map<String, dynamic> arguments,
  ) async {
    try {
      await service.run(projectId.value, operation, arguments);
      await Get.find<JobsController>().refreshJobs();
      return null;
    } catch (error) {
      return error.toString();
    }
  }

  Future<String?> saveArtifact(Artifact artifact, dynamic value) async {
    try {
      await service.save(projectId.value, artifact, value);
      return loadWorkspace();
    } catch (error) {
      return error.toString();
    }
  }

  @override
  void onClose() {
    watcher?.dispose();
    super.onClose();
  }
}
