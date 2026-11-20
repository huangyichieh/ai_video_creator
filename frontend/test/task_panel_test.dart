import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:story_studio/util/controller/jobs_controller.dart';
import 'package:story_studio/util/model/job.dart';
import 'package:story_studio/util/service/api_client.dart';
import 'package:story_studio/util/view/task_panel.dart';

class FakeJobs extends JobsController {
  int refreshes = 0;
  String? stopped;
  FakeJobs() : super(ApiClient('http://unused'));
  @override
  void onInit() {
    super.onInit();
    timer?.cancel();
  }

  @override
  Future<String?> refreshJobs() async {
    refreshes++;
    return null;
  }

  @override
  Future<String?> stop(String id) async {
    stopped = id;
    return null;
  }
}

class FakeApi extends ApiClient {
  final result = Completer<dynamic>();
  FakeApi() : super('http://unused');
  @override
  Future<dynamic> request(
    String method,
    String path, {
    Object? data,
    Map<String, String>? query,
  }) => result.future;
}

void main() {
  late FakeJobs jobs;
  setUp(() {
    Get.testMode = true;
    jobs = FakeJobs();
    Get.put<JobsController>(jobs, permanent: true);
  });
  tearDown(() => Get.reset());

  test('progress accepts missing/string values and clamps provider output', () {
    Job task(dynamic percent) => Job(
      id: 'id',
      projectId: 'world',
      operation: 'asset.generate',
      status: 'running',
      progress: {'percent': percent},
    );
    expect(taskProgress(task(null)), isNull);
    expect(taskProgress(task('42')), .42);
    expect(taskProgress(task(140)), 1);
    expect(taskProgress(task('NaN')), isNull);
    expect(
      taskCategory(
        Job(
          id: 'id',
          projectId: 'world',
          operation: 'asset.generate',
          status: 'queued',
          arguments: {'stage': 'character_references'},
        ),
      ),
      'Characters',
    );
  });

  testWidgets('icon opens right task panel; filter and live updates work', (
    tester,
  ) async {
    jobs.jobs.assignAll([
      Job(
        id: 'char-task',
        projectId: 'world',
        operation: 'asset.generate',
        status: 'queued',
        arguments: {'stage': 'character_references'},
      ),
      Job(
        id: 'background-task',
        projectId: 'world',
        operation: 'background.generate',
        status: 'succeeded',
      ),
    ]);
    await tester.pumpWidget(
      const GetMaterialApp(home: Scaffold(body: TaskMenuButton())),
    );
    expect(find.text('1'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('task-menu-button')));
    await tester.pumpAndSettle();
    expect(find.text('Generation tasks'), findsOneWidget);
    expect(jobs.refreshes, greaterThan(0));
    await tester.tap(find.text('Characters', skipOffstage: false).first);
    await tester.pumpAndSettle();
    expect(find.text('Characters · Queued'), findsOneWidget);
    expect(find.text('Background · Succeeded'), findsNothing);
    jobs.jobs[0] = Job(
      id: 'char-task',
      projectId: 'world',
      operation: 'asset.generate',
      status: 'running',
      arguments: {'stage': 'character_references'},
      progress: {'percent': '25', 'stage': 'Rendering'},
    );
    await tester.pump();
    expect(find.text('Rendering · 25%'), findsOneWidget);
    await tester.tap(find.text('Characters · Generating…'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Stop after current task'));
    await tester.pump();
    expect(jobs.stopped, 'char-task');
    await tester.tap(find.byTooltip('Close tasks'));
    await tester.pumpAndSettle();
    expect(find.text('Generation tasks'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'report opens immediately and shows loading until API completes',
    (tester) async {
      final api = Get.put<ApiClient>(FakeApi(), permanent: true) as FakeApi;
      jobs.jobs.assignAll([
        Job(
          id: 'failed-task',
          projectId: 'world',
          operation: 'background.generate',
          status: 'failed',
          error: 'Provider failed',
        ),
      ]);
      await tester.pumpWidget(
        const GetMaterialApp(home: Scaffold(body: TaskMenuButton())),
      );
      await tester.tap(find.byKey(const ValueKey('task-menu-button')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Background · Failed'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Error report'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.byType(AlertDialog), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      api.result.complete({'message': 'Failure details'});
      await tester.pumpAndSettle();
      expect(find.textContaining('Failure details'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
