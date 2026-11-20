import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:story_studio/workspace/view/workspace_status_row.dart';

void main() {
  testWidgets('next step and cost share a row in 18px text', (tester) async {
    tester.view.physicalSize = const Size(1600, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: WorkspaceStatusRow(nextStep: 'World: AI Review', cost: '76.77'),
        ),
      ),
    );
    final next = find.text('Next step: World: AI Review');
    final cost = find.text('Project billed cost (USD): 76.77');
    expect(find.byType(VerticalDivider), findsOneWidget);
    expect(tester.getCenter(next).dy, tester.getCenter(cost).dy);
    for (final field in [next, cost]) {
      expect(DefaultTextStyle.of(tester.element(field)).style.fontSize, 18);
    }
  });

  testWidgets(
    'long next-step labels wrap without overflowing on narrow screens',
    (tester) async {
      tester.view.physicalSize = const Size(320, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: WorkspaceStatusRow(
              nextStep: 'Animation: Review dialogue and performance direction',
              cost: '76.77',
            ),
          ),
        ),
      );
      expect(find.text('Project billed cost (USD): 76.77'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
