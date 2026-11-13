import 'package:get/get.dart';
import '../model/project.dart';
import '../service/project_service.dart';
import '../service/project_session.dart';

class ProjectController extends GetxController {
  final ProjectService service;
  final loading = false.obs;
  final errorMessage = Rx<String?>(null);
  final projects = <Project>[].obs;
  final selectedProjectId = ''.obs;
  final currentPage = 'world'.obs;
  final ProjectSession session;
  late final Future<String?> initialLoad;
  ProjectController(this.service, {ProjectSession? session})
    : session = session ?? ProjectSession();

  Project? get selectedProject => projects.firstWhereOrNull(
    (project) => project.projectId == selectedProjectId.value,
  );

  void updateProject(Project project) {
    final index = projects.indexWhere((p) => p.projectId == project.projectId);
    if (index >= 0) projects[index] = project;
  }

  Project? startupProject({String? explicitId}) {
    final id = explicitId ?? session.projectId;
    final project = projects.firstWhereOrNull((item) => item.projectId == id);
    if (project == null && explicitId == null) session.clear();
    return project;
  }

  void rememberProject(String id, String page) {
    final project = projects.firstWhereOrNull((item) => item.projectId == id);
    if (project == null) return;
    final root = project.worldProjectId ?? project.projectId;
    selectedProjectId.value = root;
    currentPage.value = page;
    session.remember(
      root,
      page,
      episodeId: project.worldProjectId == null ? null : id,
    );
  }

  void openProject(String id) {
    rememberProject(id, 'world');
    Get.offAllNamed('/world', arguments: id, parameters: {'project': id});
  }

  void openWorkspace(String page) {
    if (selectedProject == null) return;
    var id = selectedProjectId.value;
    if (['story', 'animations', 'final'].contains(page)) {
      final episode = projects.firstWhereOrNull(
        (p) => p.projectId == session.episodeId && p.worldProjectId == id,
      );
      if (episode != null) id = episode.projectId;
    }
    rememberProject(id, page);
    Get.offNamed(
      '/$page',
      arguments: id,
      parameters: {'project': id},
      preventDuplicates: false,
    );
  }

  @override
  void onInit() {
    super.onInit();
    initialLoad = loadProjects();
  }

  Future<String?> loadProjects() async {
    loading.value = true;
    try {
      projects.assignAll(await service.list());
      errorMessage.value = null;
      return null;
    } catch (error) {
      errorMessage.value = error.toString();
      return error.toString();
    } finally {
      loading.value = false;
    }
  }

  Future<String?> createWorld(
    String name,
    String premise,
    String language,
  ) async {
    try {
      final project = await service.create(name, premise, language);
      final error = await loadProjects();
      if (error == null) rememberProject(project.projectId, 'world');
      return error;
    } catch (error) {
      return error.toString();
    }
  }

  Future<String?> removeProject(String id, String confirmation) async {
    try {
      await service.remove(id, confirmation);
      final error = await loadProjects();
      if (error == null &&
          !projects.any((p) => p.projectId == selectedProjectId.value)) {
        selectedProjectId.value = '';
        session.clear();
      }
      return error;
    } catch (error) {
      return error.toString();
    }
  }
}
