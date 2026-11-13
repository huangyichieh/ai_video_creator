import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../util/view/frame.dart';
import '../util/view/loading.dart';
import '../util/view/workspace_shell.dart';
import 'view/project_dialogs.dart';
import 'controller/project_controller.dart';

class ProjectsPage extends StatelessWidget {
  const ProjectsPage({super.key});
  @override
  Widget build(BuildContext context) {
    final controller = Get.find<ProjectController>();
    return WorkspaceShell(
      title: 'Story Studio',
      floatingActionButton: FloatingActionButton(
        onPressed: controller.loadProjects,
        child: const Icon(Icons.refresh),
      ),
      body: AppFrame(
        child: Obx(
          () => controller.loading.value && controller.projects.isEmpty
              ? const AppLoadingIcon()
              : ListView(
                  children: [
                    if (controller.errorMessage.value != null)
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text(
                            controller.errorMessage.value!,
                            style: const TextStyle(color: Colors.red),
                          ),
                        ),
                      ),
                    FilledButton.icon(
                      onPressed: () => createWorld(controller),
                      icon: const Icon(Icons.add),
                      label: const Text('New project'),
                    ),
                    const SizedBox(height: 8),
                    for (final project in controller.projects.where(
                      (item) => item.worldProjectId == null,
                    ))
                      Card(
                        child: ListTile(
                          title: Text(project.title),
                          subtitle: Text(
                            '${project.projectId.substring(0, 8)} · ${project.language}',
                          ),
                          onTap: () =>
                              controller.openProject(project.projectId),
                          trailing: IconButton(
                            tooltip: 'Delete project',
                            icon: const Icon(Icons.delete_outline),
                            onPressed: () => removeProject(
                              controller,
                              project.projectId,
                              project.title,
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
        ),
      ),
    );
  }
}
