import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:story_studio/util/service/api_client.dart';
import 'package:story_studio/util/view/content_text_field.dart';
import 'package:story_studio/workspace/controller/workspace_controller.dart';
import 'package:story_studio/workspace/service/workspace_service.dart';
import 'package:story_studio/world/controller/world_settings_controller.dart';
import 'package:story_studio/world/model/world_settings.dart';
import 'package:story_studio/world/service/world_settings_service.dart';
import 'package:story_studio/world/view/world_settings_view.dart';
import 'package:story_studio/world/view/world_review_diff.dart';

const original = WorldSettingsData(name: 'Island', premise: 'Old premise');
const suggested = WorldSettingsData(
  name: 'Island',
  premise: 'New premise',
  tone: 'Warm',
);

class FakeWorldService extends WorldSettingsService {
  FakeWorldService() : super(ApiClient('http://unused'));
  WorldSettingsData? saved, selected;
  String? language, decision;
  bool failSave = false;
  @override
  Future<WorldSettingsForm> save(
    String id,
    WorldSettingsData settings,
    String language,
    String expected,
  ) async {
    if (failSave) throw StateError('Save failed');
    saved = settings;
    this.language = language;
    return WorldSettingsForm(
      settings: settings,
      defaultLanguage: language,
      version: 'saved',
    );
  }

  @override
  Future<WorldSettingsForm> resolve(
    String id,
    WorldSettingsData settings,
    String decision,
    String expected,
  ) async {
    selected = settings;
    this.decision = decision;
    return WorldSettingsForm(
      settings: settings,
      version: 'resolved',
      review: WorldFormReview(
        original: original,
        suggested: suggested,
        accepted: settings,
        decision: decision,
        current: true,
      ),
    );
  }
}

class FakeWorkspace extends WorkspaceController {
  FakeWorkspace()
    : super(WorkspaceService(ApiClient('http://unused')), 'world', 'world-id');
  String? operation;
  Map<String, dynamic>? args;
  Completer<String?>? task;
  @override
  Future<String?> loadWorkspace() async => null;
  @override
  Future<String?> runPrepared(
    String operation,
    Map<String, dynamic> args,
  ) async {
    this.operation = operation;
    this.args = args;
    return task == null ? null : await task!.future;
  }
}

Widget host(WorldSettingsController controller) => MaterialApp(
  theme: ThemeData.dark(),
  home: Scaffold(
    body: SingleChildScrollView(child: WorldSettingsEditor(controller)),
  ),
);
Finder field(String key) => find.descendant(
  of: find.byKey(ValueKey('world-field-$key')),
  matching: find.byType(TextField),
);

void main() {
  late FakeWorldService service;
  late FakeWorkspace workspace;
  late WorldSettingsController controller;
  setUp(() {
    service = FakeWorldService();
    workspace = FakeWorkspace();
    controller = WorldSettingsController(
      service,
      workspace,
      projectId: 'world-id',
    );
    controller.accept(
      const WorldSettingsForm(settings: original, version: 'initial'),
    );
  });

  testWidgets('unsaved fields have blue boundaries; saving enables review', (
    tester,
  ) async {
    await tester.pumpWidget(host(controller));
    expect(
      tester
          .widget<OutlinedButton>(find.byKey(const ValueKey('world-save')))
          .onPressed,
      isNull,
    );
    await tester.enterText(field('premise'), 'Edited premise');
    await tester.pump();
    final input = tester.widget<TextField>(field('premise'));
    expect(
      input.decoration!.enabledBorder!.borderSide.color,
      const Color(0xff64b5f6),
    );
    expect(controller.canReview, isFalse);
    expect(controller.canSave, isTrue);
    await tester.runAsync(controller.save);
    await tester.pump();
    expect(service.saved!.premise, 'Edited premise');
    expect(service.language, 'zh-TW');
    expect(controller.dirty, isFalse);
    expect(controller.canReview, isTrue);
  });

  test('each difference requires a decision, including manual edits', () async {
    controller.accept(
      const WorldSettingsForm(
        settings: original,
        version: 'review',
        review: WorldFormReview(
          original: original,
          suggested: suggested,
          current: true,
        ),
      ),
    );
    expect(controller.canApprove, isFalse);
    controller.choose('premise', 'apply');
    controller.choose('tone', 'revert');
    expect(controller.canApprove, isTrue);
    controller.editProposal('premise', 'Manual proposal');
    expect(controller.canApprove, isFalse);
    controller.choose('premise', 'apply');
    await controller.approve();
    expect(service.selected!.premise, 'Manual proposal');
    expect(service.selected!.tone, '');
    expect(service.decision, 'apply');
    expect(workspace.operation, 'world.approve_base');
    expect(workspace.args!['expected_state'], 'resolved');
  });

  testWidgets('approved fields use selectable text and keep name editable', (
    tester,
  ) async {
    controller.accept(
      const WorldSettingsForm(
        settings: original,
        version: 'approved',
        approved: true,
      ),
    );
    await tester.pumpWidget(host(controller));
    expect(field('name'), findsOneWidget);
    expect(field('premise'), findsNothing);
    expect(find.text('Old premise'), findsOneWidget);
    expect(controller.canReview, isFalse);
    expect(controller.canApprove, isFalse);
    await tester.enterText(field('name'), 'Renamed island');
    await tester.pump();
    expect(controller.canSave, isTrue);
  });

  testWidgets('review starts immediately without replacing form fields', (
    tester,
  ) async {
    workspace.task = Completer<String?>();
    await tester.pumpWidget(host(controller));
    final task = controller.review();
    await tester.pump();
    expect(find.text('Reviewing…'), findsOneWidget);
    expect(tester.widget<TextField>(field('premise')).enabled, isFalse);
    expect(controller.canSave, isFalse);
    workspace.task!.complete(null);
    await task;
    await tester.pump();
    expect(tester.widget<TextField>(field('premise')).enabled, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('inline diff displays editable proposed fields beside original', (
    tester,
  ) async {
    controller.accept(
      const WorldSettingsForm(
        settings: original,
        version: 'review',
        review: WorldFormReview(
          original: original,
          suggested: suggested,
          current: true,
        ),
      ),
    );
    await tester.pumpWidget(host(controller));
    final right = find.descendant(
      of: find.byKey(const ValueKey('world-diff-premise')),
      matching: find.byType(TextField),
    );
    expect(right, findsOneWidget);
    await tester.enterText(right, 'Manually revised premise');
    await tester.pump();
    expect(controller.proposed['premise'], 'Manually revised premise');
    expect(controller.decisions.containsKey('premise'), isFalse);
    expect(find.byType(ContentTextField), findsNWidgets(9));
    expect(tester.takeException(), isNull);
  });

  testWidgets('fields stack label and short guidance above full-width inputs', (
    tester,
  ) async {
    await tester.pumpWidget(host(controller));
    final block = find.byKey(const ValueKey('world-field-eraSetting'));
    final label = find.descendant(
      of: block,
      matching: find.text('Era & setting:'),
    );
    final help = find.text(worldFieldHelp['eraSetting']!);
    final input = field('eraSetting');
    expect(
      tester.getBottomLeft(label).dy,
      lessThan(tester.getTopLeft(help).dy),
    );
    expect(
      tester.getBottomLeft(help).dy,
      lessThan(tester.getTopLeft(input).dy),
    );
    expect(tester.getTopLeft(input).dx, tester.getTopLeft(block).dx);
    expect(tester.widget<TextField>(input).minLines, 2);
  });

  testWidgets(
    'auto generation saves unsaved premise and shows immediate loading',
    (tester) async {
      workspace.task = Completer<String?>();
      controller.edit('premise', 'People control five elements');
      await tester.pumpWidget(host(controller));
      final task = controller.generate();
      await tester.pump();
      expect(find.text('Generating…'), findsOneWidget);
      await tester.runAsync(() async {
        await Future<void>.delayed(Duration.zero);
      });
      expect(service.saved!.premise, 'People control five elements');
      expect(workspace.operation, 'world.generate_settings');
      expect(workspace.args!['expected_state'], 'saved');
      expect(controller.canReview, isFalse);
      workspace.task!.complete(null);
      await task;
      await tester.pump();
      controller.accept(
        const WorldSettingsForm(
          settings: WorldSettingsData(
            name: 'Island',
            premise: 'People control five elements',
            tone: 'Hopeful',
          ),
          version: 'generated',
        ),
      );
      await tester.pump();
      expect(
        tester.widget<TextField>(field('tone')).controller!.text,
        'Hopeful',
      );
      expect(controller.canReview, isTrue);
    },
  );

  test(
    'auto generation requires a premise and cannot queue after save failure',
    () async {
      controller.edit('premise', '');
      expect(controller.canGenerate, isFalse);
      controller.edit('premise', 'New premise');
      service.failSave = true;
      await controller.generate();
      expect(workspace.operation, isNull);
      expect(controller.draft['premise'], 'New premise');
      expect(controller.error.value, contains('Save failed'));
      controller.accept(
        const WorldSettingsForm(
          settings: original,
          version: 'approved',
          approved: true,
        ),
        force: true,
      );
      expect(controller.canGenerate, isFalse);
    },
  );

  test(
    'line diff highlights changed lines without highlighting shared context',
    () {
      final diff = changedLines('Shared\nOld\nEnd', 'Shared\nNew\nAdded\nEnd');
      expect(diff.oldLines, {1});
      expect(diff.newLines, {1, 2});
      expect(changedLines('Same', 'Same').newLines, isEmpty);
    },
  );
}
