import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:story_studio/util/service/api_client.dart';
import 'package:story_studio/world/controller/additional_settings_controller.dart';
import 'package:story_studio/world/model/world_settings.dart';
import 'package:story_studio/world/service/world_settings_service.dart';
import 'package:story_studio/world/view/additional_settings_view.dart';
import 'world_settings_test.dart' show FakeWorkspace;

const draft = WorldAddition(
  settingId: 'draft-one',
  text: 'Original draft',
  version: 'draft-v1',
);
const approved = WorldAddition(
  settingId: 'approved-one',
  text: 'Immutable rule',
  version: 'approved-v1',
  approved: true,
);
WorldSettingsForm form(List<WorldAddition> additions) => WorldSettingsForm(
  settings: const WorldSettingsData(name: 'Island', premise: 'Memories'),
  version: 'base',
  approved: true,
  additions: additions,
);

class FakeAdditionService extends WorldSettingsService {
  List<WorldAddition> values = [draft, approved];
  int updates = 0;
  String? savedText, expected;
  bool failResolve = false;
  Completer<void>? gate;
  FakeAdditionService() : super(ApiClient('http://unused'));
  @override
  Future<WorldSettingsForm> load(String id) async => form(values);
  @override
  Future<WorldSettingsForm> resolveAddition(
    String id,
    String settingId,
    String decision,
    String expected,
  ) async {
    if (failResolve) throw StateError('Resolution failed');
    values = [
      for (final item in values)
        if (item.settingId == settingId)
          WorldAddition(
            settingId: settingId,
            text: decision == 'apply' ? item.review!.suggested : item.text,
            version: 'resolved-v1',
            review: AdditionReview(
              original: item.review!.original,
              suggested: '',
              current: true,
              decision: decision,
            ),
          )
        else
          item,
    ];
    return form(values);
  }

  @override
  Future<WorldSettingsForm> updateAddition(
    String id,
    String settingId,
    String text,
    String expected,
  ) async {
    updates++;
    savedText = text;
    this.expected = expected;
    if (gate != null) await gate!.future;
    values = [
      for (final item in values)
        if (item.settingId == settingId)
          WorldAddition(
            settingId: settingId,
            text: text,
            version: 'saved-$updates',
          )
        else
          item,
    ];
    return form(values);
  }

  @override
  Future<WorldSettingsForm> add(String id, String text) async {
    values = [
      ...values,
      WorldAddition(settingId: 'new-id', text: text, version: 'new-v1'),
    ];
    return form(values);
  }

  @override
  Future<WorldSettingsForm> removeAddition(
    String id,
    String settingId,
    String expected,
  ) async {
    values = values.where((item) => item.settingId != settingId).toList();
    return form(values);
  }
}

void main() {
  late FakeWorkspace workspace;
  late FakeAdditionService service;
  late AdditionalSettingsController controller;
  setUp(() {
    Get.testMode = true;
    workspace = FakeWorkspace();
    service = FakeAdditionService();
    controller = AdditionalSettingsController(
      workspace,
      service,
      projectId: 'world-id',
    );
    controller.accept(form(service.values));
  });
  tearDown(() {
    controller.onClose();
    Get.reset();
  });

  testWidgets('drafts autosave and approved content has no editing controls', (
    tester,
  ) async {
    await tester.pumpWidget(
      GetMaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: AdditionalSettingsEditor(controller),
          ),
        ),
      ),
    );
    expect(find.text('Draft settings (1)'), findsOneWidget);
    expect(find.text('Approved settings (1)'), findsOneWidget);
    expect(find.text('Immutable rule'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
    final field = find.byType(TextField);
    await tester.enterText(field, 'Edited draft');
    expect(controller.dirty('draft-one'), isTrue);
    await tester.pump(const Duration(milliseconds: 650));
    await tester.pump();
    expect(service.savedText, 'Edited draft');
    expect(service.expected, 'draft-v1');
    expect(controller.dirty('draft-one'), isFalse);
    expect(controller.canApprove('draft-one'), isTrue);
    expect(tester.takeException(), isNull);
  });

  test(
    'review saves pending edits before queueing with the new version',
    () async {
      controller.edit('draft-one', 'Latest draft');
      await controller.review('draft-one');
      expect(service.savedText, 'Latest draft');
      expect(workspace.operation, 'world.review');
      expect(workspace.args!['expected_setting'], 'saved-1');
      expect(controller.timers, isEmpty);
    },
  );

  testWidgets('Approve saves and directly submits unreviewed draft content', (
    tester,
  ) async {
    workspace.task = Completer<String?>();
    controller.edit('draft-one', 'Latest content without review');
    await tester.pumpWidget(
      GetMaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: AdditionalSettingsEditor(controller),
          ),
        ),
      ),
    );
    await tester.ensureVisible(find.text('Approve'));
    await tester.tap(find.text('Approve'));
    await tester.pump();
    expect(service.savedText, 'Latest content without review');
    expect(workspace.operation, 'world.approve_draft');
    expect(workspace.args!['text'], 'Latest content without review');
    expect(workspace.args!['expected_setting'], 'saved-1');
    expect(find.text('Approving…'), findsOneWidget);
    workspace.task!.complete(null);
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
  });

  test(
    'typing during autosave retains new draft text and advances save guard',
    () async {
      service.gate = Completer<void>();
      controller.edit('draft-one', 'First edit');
      final first = controller.save('draft-one');
      controller.edit('draft-one', 'Newer edit');
      service.gate!.complete();
      await first;
      expect(controller.drafts['draft-one'], 'Newer edit');
      expect(controller.dirty('draft-one'), isTrue);
      await controller.save('draft-one');
      expect(service.savedText, 'Newer edit');
      expect(service.expected, 'saved-1');
      expect(controller.dirty('draft-one'), isFalse);
    },
  );

  testWidgets(
    'inline Apply replaces content, clears suggestion and enables direct approval',
    (tester) async {
      const reviewed = WorldAddition(
        settingId: 'draft-one',
        text: 'Original draft',
        version: 'reviewed-v1',
        review: AdditionReview(
          original: 'Original draft',
          suggested: 'Suggested draft',
          summary: 'Clearer rule',
          current: true,
        ),
      );
      service.values = [reviewed, approved];
      controller.accept(form(service.values));
      await tester.pumpWidget(
        GetMaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: AdditionalSettingsEditor(controller),
            ),
          ),
        ),
      );
      expect(controller.canApprove('draft-one'), isTrue);
      expect(find.text('Suggested draft'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('addition-apply-draft-one')));
      await tester.pumpAndSettle();
      expect(controller.drafts['draft-one'], 'Suggested draft');
      expect(controller.item('draft-one')!.review!.suggested, isEmpty);
      expect(controller.canResolve('draft-one'), isFalse);
      expect(controller.canApprove('draft-one'), isTrue);
      await tester.tap(find.text('Approve'));
      await tester.pumpAndSettle();
      expect(workspace.operation, 'world.approve_draft');
      expect(workspace.args!['text'], 'Suggested draft');
      expect(workspace.args!['expected_setting'], 'resolved-v1');
      controller.accept(
        form([
          WorldAddition(
            settingId: 'draft-one',
            text: 'Suggested draft',
            version: 'approved-new',
            approved: true,
          ),
          approved,
        ]),
      );
      await tester.pump();
      expect(find.text('Draft settings (0)'), findsOneWidget);
      expect(find.text('Approved settings (2)'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  test(
    'Discard clears suggestion, preserves content and remains cleared on refresh',
    () async {
      const reviewed = WorldAddition(
        settingId: 'draft-one',
        text: 'Original draft',
        version: 'reviewed',
        review: AdditionReview(
          original: 'Original draft',
          suggested: 'Suggested draft',
          current: true,
        ),
      );
      service.values = [reviewed, approved];
      controller.accept(form(service.values));
      await controller.resolveSuggestion('draft-one', 'revert');
      expect(controller.drafts['draft-one'], 'Original draft');
      expect(controller.item('draft-one')!.review!.suggested, isEmpty);
      await controller.reload();
      expect(controller.canResolve('draft-one'), isFalse);
      expect(controller.canApprove('draft-one'), isTrue);
      controller.edit('draft-one', 'Manual change');
      expect(controller.canApprove('draft-one'), isTrue);
      await controller.save('draft-one');
      expect(controller.item('draft-one')!.review, isNull);
    },
  );

  test(
    'Apply and Discard require a current review and keep text on failure',
    () async {
      expect(controller.canResolve('draft-one'), isFalse);
      const reviewed = WorldAddition(
        settingId: 'draft-one',
        text: 'Original draft',
        version: 'reviewed',
        review: AdditionReview(
          original: 'Original draft',
          suggested: 'Suggested draft',
          current: true,
        ),
      );
      controller.accept(form([reviewed, approved]));
      service.failResolve = true;
      await controller.resolveSuggestion('draft-one', 'apply');
      expect(controller.drafts['draft-one'], 'Original draft');
      expect(
        controller.item('draft-one')!.review!.suggested,
        'Suggested draft',
      );
      expect(controller.errors['draft-one'], contains('Resolution failed'));
      controller.edit('draft-one', 'Unsaved edit');
      expect(controller.canResolve('draft-one'), isFalse);
    },
  );

  testWidgets('add dialog creates a draft and remove requires confirmation', (
    tester,
  ) async {
    await tester.pumpWidget(
      GetMaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: AdditionalSettingsEditor(controller),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Add Setting'));
    await tester.pumpAndSettle();
    final field = find.descendant(
      of: find.byType(AlertDialog),
      matching: find.byType(TextField),
    );
    await tester.enterText(field, 'A new rule');
    await tester.pump();
    await tester.tap(find.widgetWithText(TextButton, 'Add'));
    await tester.pumpAndSettle();
    expect(controller.item('new-id')!.text, 'A new rule');
    await tester.tap(find.widgetWithText(OutlinedButton, 'Remove').first);
    await tester.pumpAndSettle();
    expect(find.text('Remove draft setting?'), findsOneWidget);
    await tester.tap(find.widgetWithText(TextButton, 'Cancel'));
    await tester.pumpAndSettle();
    expect(controller.item('draft-one'), isNotNull);
  });
}
