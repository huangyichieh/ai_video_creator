import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:story_studio/workspace/view/character_tabs.dart';

Widget characterCard(String id) => ExpansionTile(
  key: PageStorageKey('character-$id'),
  title: Text('Character $id'),
  initiallyExpanded: true,
  children: [
    CharacterTabs(
      storageId: 'character-$id',
      children: [
        Column(
          children: [
            Text('Visual content $id'),
            const SizedBox(height: 600),
            Text('Visual end $id'),
          ],
        ),
        Column(children: [Text('Voice content $id')]),
        Column(children: [Text('Costume content $id')]),
      ],
    ),
  ],
);

void main() {
  testWidgets(
    'character tabs expand to content height, use page scrolling and survive reopening',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(child: characterCard('azhe')),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        tester.getSize(find.byType(CharacterTabs)).height,
        greaterThan(600),
      );
      final verticalScrolls = find.descendant(
        of: find.byType(CharacterTabs),
        matching: find.byWidgetPredicate(
          (widget) =>
              widget is Scrollable &&
              (widget.axisDirection == AxisDirection.down ||
                  widget.axisDirection == AxisDirection.up),
        ),
      );
      expect(verticalScrolls, findsNothing);
      await tester.drag(
        find.text('Visual content azhe'),
        const Offset(0, -120),
      );
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Character azhe'));
      await tester.pumpAndSettle();
      for (var attempt = 0; attempt < 2; attempt++) {
        await tester.tap(find.text('Character azhe'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Character azhe'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.byType(ErrorWidget), findsNothing);
      }
      await tester.tap(find.text('Voice'));
      await tester.pumpAndSettle();
      expect(find.text('Voice content azhe').hitTestable(), findsOneWidget);
      expect(tester.getSize(find.byType(CharacterTabs)).height, lessThan(200));
      await tester.tap(find.text('Character azhe'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Character azhe'));
      await tester.pumpAndSettle();
      expect(find.text('Voice content azhe').hitTestable(), findsOneWidget);
      expect(tester.widget<TabBar>(find.byType(TabBar)).controller!.index, 1);
      expect(tester.takeException(), isNull);
    },
  );
}
