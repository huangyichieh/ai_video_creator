import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/model/app_route_parameters.dart';
import '../../util/view/loading.dart';
import '../../util/view/workspace_shell.dart';
import '../controller/project_controller.dart';
import '../service/project_session.dart';

class StartupPage extends StatefulWidget {
  const StartupPage({super.key});
  @override
  State<StartupPage> createState() => _StartupPageState();
}

class _StartupPageState extends State<StartupPage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => restore());
  }

  Future<void> restore() async {
    final controller = Get.find<ProjectController>();
    final error = controller.loading.value
        ? await controller.initialLoad
        : controller.errorMessage.value;
    if (!mounted) return;
    final parameters = Get.find<AppRouteParameters>();
    final project = error == null
        ? controller.startupProject(explicitId: parameters.projectId)
        : null;
    if (project == null) {
      Get.offAllNamed('/');
      return;
    }
    final requested = parameters.projectId == null
        ? controller.session.page
        : parameters.page;
    final page = ProjectSession.pages.contains(requested) ? requested : 'world';
    controller.rememberProject(project.projectId, page);
    controller.openWorkspace(page);
  }

  @override
  Widget build(BuildContext context) =>
      const WorkspaceShell(title: 'Story Studio', body: AppLoadingIcon());
}
