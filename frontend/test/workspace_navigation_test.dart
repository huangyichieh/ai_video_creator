import 'dart:ui' show PointerDeviceKind;
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:story_studio/main_theme.dart';
import 'package:story_studio/projects/controller/project_controller.dart';
import 'package:story_studio/projects/model/project.dart';
import 'package:story_studio/projects/service/project_service.dart';
import 'package:story_studio/projects/service/project_session.dart';
import 'package:story_studio/projects/view/startup.dart';
import 'package:story_studio/util/model/app_route_parameters.dart';
import 'package:story_studio/util/service/api_client.dart';
import 'package:story_studio/util/view/workspace_shell.dart';

class FakeProjects extends ProjectService {
  final List<Project> values;
  final removed = <String>[];
  Completer<void>? removalGate;
  FakeProjects(List<Project> values)
    : values = List.of(values),
      super(ApiClient('http://localhost'));
  @override
  Future<List<Project>> list() async => values;
  @override
  Future<void> remove(String id, String confirmation) async {
    final project = values.firstWhere((p) => p.projectId == id);
    if (confirmation != project.title) {
      throw StateError('Project name does not match');
    }
    removed.add(id);
    if (removalGate != null) await removalGate!.future;
    values.removeWhere((p) => p.projectId == id || p.worldProjectId == id);
  }
}

void main() {
  late ProjectSession session;
  late ProjectController controller;
  final projects = [
    Project(projectId: 'world-one', title: 'First World'),
    Project(projectId: 'world-two', title: 'Second World'),
    Project(
      projectId: 'episode-one',
      title: 'Episode 1',
      worldProjectId: 'world-one',
    ),
  ];
  setUp(() {
    Get.testMode = true;
    session = ProjectSession(prefix: 'navigation-test');
    session.clear();
    controller = Get.put(
      ProjectController(FakeProjects(projects), session: session),
      permanent: true,
    );
  });
  tearDown(() async {
    session.clear();
    Get.reset();
  });

  test(
    'selection persists World and episode and clears a removed project',
    () async {
      await controller.initialLoad;
      controller.rememberProject('episode-one', 'animations');
      expect(session.projectId, 'world-one');
      expect(session.episodeId, 'episode-one');
      expect(session.page, 'animations');
      expect(controller.startupProject()?.title, 'First World');
      expect(
        controller.startupProject(explicitId: 'world-two')?.title,
        'Second World',
      );
      controller.rememberProject('world-two', 'world');
      expect(session.episodeId, isNull);
      session.remember('deleted', 'world');
      expect(controller.startupProject(), isNull);
      expect(session.projectId, isNull);
    },
  );

  testWidgets('project menu opens selected project and omits episode rows', (
    tester,
  ) async {
    await tester.runAsync(() => controller.initialLoad);
    await tester.pumpWidget(
      GetMaterialApp(
        theme: mainTheme,
        home: const WorkspaceShell(
          title: 'World',
          body: Text('Workspace body'),
        ),
        getPages: [
          GetPage(name: '/world', page: () => const Text('Opened World')),
        ],
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('-'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('project-menu-button')));
    await tester.pumpAndSettle();
    expect(find.text('Add project'), findsOneWidget);
    expect(find.text('First World'), findsOneWidget);
    expect(find.text('Second World'), findsOneWidget);
    expect(find.text('Episode 1'), findsNothing);
    await tester.tap(find.text('Second World'));
    await tester.pumpAndSettle();
    expect(controller.selectedProjectId.value, 'world-two');
    expect(find.text('Opened World'), findsOneWidget);
  });

  testWidgets('sidebar expands on hover without moving the workspace body', (
    tester,
  ) async {
    await tester.runAsync(() => controller.initialLoad);
    controller.rememberProject('world-one', 'world');
    await tester.pumpWidget(
      GetMaterialApp(
        theme: mainTheme,
        home: const WorkspaceShell(
          title: 'World',
          page: 'world',
          projectId: 'world-one',
          body: Center(child: Text('Workspace body')),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final rail = find.byKey(const ValueKey('workspace-rail'));
    expect(tester.getSize(rail).width, 64);
    final body = tester.getCenter(find.text('Workspace body'));
    final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: const Offset(400, 200));
    await mouse.moveTo(const Offset(24, 100));
    await tester.pumpAndSettle();
    expect(tester.getSize(rail).width, 208);
    expect(tester.getCenter(find.text('Workspace body')), body);
    await mouse.moveTo(const Offset(400, 200));
    await tester.pumpAndSettle();
    expect(tester.getSize(rail).width, 64);
    await mouse.removePointer();
    expect(tester.takeException(), isNull);
  });

  testWidgets('startup reopens the saved project page and episode', (
    tester,
  ) async {
    await tester.runAsync(() => controller.initialLoad);
    session.remember('world-one', 'animations', episodeId: 'episode-one');
    Get.put(AppRouteParameters(apiBase: 'http://localhost'));
    await tester.pumpWidget(
      GetMaterialApp(
        initialRoute: '/startup',
        getPages: [
          GetPage(name: '/startup', page: () => const StartupPage()),
          GetPage(name: '/', page: () => const Text('Project selection')),
          GetPage(
            name: '/animations',
            page: () => Text('Animation: ${Get.arguments}'),
          ),
        ],
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Animation: episode-one'), findsOneWidget);
    expect(controller.selectedProjectId.value, 'world-one');
  });

  testWidgets('compact header fits a narrow screen', (tester) async {
    tester.view.physicalSize = const Size(390, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.runAsync(() => controller.initialLoad);
    controller.rememberProject('world-one', 'story');
    await tester.pumpWidget(
      GetMaterialApp(
        theme: mainTheme,
        home: const WorkspaceShell(
          title: 'Story/Episode',
          body: Text('Workspace'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.byTooltip('Generation preferences'), findsOneWidget);
    expect(find.byTooltip('API key'), findsOneWidget);
    final center = tester.getCenter(
      find.byKey(const ValueKey('workspace-title')),
    );
    expect(center.dx, closeTo(195, 1));
  });

  testWidgets(
    'sidebar removal requires the exact name and shows deletion progress',
    (tester) async {
      await tester.runAsync(() => controller.initialLoad);
      controller.rememberProject('world-one', 'world');
      final service = controller.service as FakeProjects;
      service.removalGate = Completer<void>();
      await tester.pumpWidget(
        GetMaterialApp(
          theme: mainTheme,
          initialRoute: '/world',
          getPages: [
            GetPage(
              name: '/world',
              page: () => const WorkspaceShell(
                title: 'World',
                page: 'world',
                projectId: 'world-one',
                body: Text('Current workspace'),
              ),
            ),
            GetPage(name: '/', page: () => const Text('Project selection')),
          ],
        ),
      );
      await tester.pumpAndSettle();
      final remove = find.byKey(const ValueKey('remove-project-button'));
      expect(
        tester.widget<TextButton>(remove).style!.foregroundColor!.resolve({}),
        const Color(0xffff6b6b),
      );
      Future<void> open() async {
        await tester.tapAt(tester.getTopLeft(remove) + const Offset(32, 24));
        await tester.pumpAndSettle();
      }

      await open();
      final confirm = find.byKey(const ValueKey('confirm-remove-project'));
      final field = find.byKey(const ValueKey('remove-project-confirmation'));
      expect(tester.widget<TextButton>(confirm).onPressed, isNull);
      await tester.enterText(field, 'wrong project');
      await tester.pump();
      expect(tester.widget<TextButton>(confirm).onPressed, isNull);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(service.removed, isEmpty);
      await open();
      await tester.enterText(field, 'First World');
      await tester.pump();
      expect(tester.widget<TextButton>(confirm).onPressed, isNotNull);
      await tester.tap(confirm);
      await tester.pump();
      expect(find.text('Deleting…'), findsOneWidget);
      expect(tester.widget<TextButton>(confirm).onPressed, isNull);
      await tester.runAsync(() async {
        service.removalGate!.complete();
        await Future<void>.delayed(const Duration(milliseconds: 10));
      });
      await tester.pumpAndSettle();
      expect(service.removed, ['world-one']);
      expect(controller.selectedProjectId.value, isEmpty);
      expect(session.projectId, isNull);
      expect(find.text('Project selection'), findsOneWidget);
    },
  );
}
