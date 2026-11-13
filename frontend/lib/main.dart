import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:web/web.dart' as web;
import 'main_theme.dart';
import 'projects/view.dart';
import 'projects/view/startup.dart';
import 'projects/controller/project_controller.dart';
import 'projects/service/project_service.dart';
import 'world/view.dart';
import 'assets/view.dart';
import 'story/view.dart';
import 'animations/view.dart';
import 'final_video/view.dart';
import 'analytics/view.dart';
import 'util/model/app_route_parameters.dart';
import 'util/service/api_client.dart';
import 'util/service/file_service.dart';
import 'util/controller/jobs_controller.dart';
import 'util/view/loading.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final parameters = AppRouteParameters.fromUri(Uri.base);
  Get.put(parameters, permanent: true);
  if (parameters.takeSecret() != null) {
    final uri = Uri.base;
    final query = Map<String, String>.from(uri.queryParameters)
      ..remove('secret');
    final fragment = Uri.tryParse(uri.fragment);
    final fragmentQuery = Map<String, String>.from(
      fragment?.queryParameters ?? {},
    )..remove('secret');
    web.window.history.replaceState(
      null,
      '',
      uri
          .replace(
            queryParameters: query,
            fragment: fragment
                ?.replace(queryParameters: fragmentQuery)
                .toString(),
          )
          .toString(),
    );
  }
  final api = Get.put(ApiClient(parameters.apiBase), permanent: true);
  Get.put(FileService(api), permanent: true);
  Get.put(ProjectController(ProjectService(api)), permanent: true);
  Get.put(JobsController(api), permanent: true);
  runApp(const StoryStudioApp());
}

class StoryStudioApp extends StatelessWidget {
  const StoryStudioApp({super.key});
  @override
  Widget build(BuildContext context) => GetMaterialApp(
    title: 'Story Studio',
    debugShowCheckedModeBanner: false,
    defaultTransition: Transition.noTransition,
    theme: mainTheme,
    initialRoute: '/startup',
    getPages: [
      GetPage(name: '/startup', page: () => const StartupPage()),
      GetPage(name: '/', page: () => const ProjectsPage()),
      GetPage(name: '/loading', page: () => const LoadingPage()),
      GetPage(name: '/world', page: () => const WorldPage()),
      GetPage(name: '/assets', page: () => const AssetsPage()),
      GetPage(name: '/story', page: () => const StoryPage()),
      GetPage(name: '/animations', page: () => const AnimationsPage()),
      GetPage(name: '/final', page: () => const FinalPage()),
      GetPage(name: '/analytics', page: () => const AnalyticsPage()),
    ],
  );
}
