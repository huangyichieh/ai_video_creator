import 'package:flutter/material.dart';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:story_studio/util/view/content_text_field.dart';
import 'package:story_studio/workspace/view/value_editor.dart';

Widget host(Widget child) => MaterialApp(
  home: Scaffold(body: SingleChildScrollView(child: child)),
);

void main() {
  testWidgets(
    'JSON-encoded content roundtrips as a string with typed fields preserved',
    (tester) async {
      dynamic draft = jsonEncode({
        'description': 'Before',
        'duration': 3,
        'enabled': true,
      });
      await tester.pumpWidget(
        host(
          StatefulBuilder(
            builder: (context, setState) => ValueEditor(
              value: draft,
              label: 'payload',
              onChanged: (next) => setState(() => draft = next),
            ),
          ),
        ),
      );
      final description = find.byWidgetPredicate(
        (widget) => widget is ContentTextField && widget.label == 'description',
      );
      await tester.enterText(
        find.descendant(of: description, matching: find.byType(TextField)),
        'After\nWith detail',
      );
      await tester.pump();
      expect(draft, isA<String>());
      expect(jsonDecode(draft), {
        'description': 'After\nWith detail',
        'duration': 3,
        'enabled': true,
      });
    },
  );
  testWidgets(
    'nested textual content uses fields while IDs and typed values are preserved',
    (tester) async {
      dynamic draft = {
        'characterId': 'immutable-id',
        'name': 'Keeper',
        'description': 'Lives by the sea.',
        'prompt': 'A quiet lighthouse.',
        'duration': 3,
        'enabled': true,
        'traits': ['Patient'],
      };
      await tester.pumpWidget(
        host(
          StatefulBuilder(
            builder: (context, setState) => ValueEditor(
              value: draft,
              onChanged: (next) => setState(() => draft = next),
            ),
          ),
        ),
      );
      final fields = tester
          .widgetList<ContentTextField>(find.byType(ContentTextField))
          .toList();
      expect(
        fields.singleWhere((field) => field.value == 'immutable-id').readOnly,
        isTrue,
      );
      final description = find.byWidgetPredicate(
        (widget) => widget is ContentTextField && widget.label == 'description',
      );
      final title = find.byWidgetPredicate(
        (widget) => widget is ContentTextField && widget.label == 'name',
      );
      final descriptionInput = find.descendant(
        of: description,
        matching: find.byType(TextField),
      );
      expect(tester.widget<TextField>(descriptionInput).minLines, 3);
      expect(
        tester
            .widget<TextField>(
              find.descendant(of: title, matching: find.byType(TextField)),
            )
            .maxLines,
        1,
      );
      await tester.enterText(
        descriptionInput,
        'New visual description\nWith a second line.',
      );
      await tester.pump();
      expect(
        draft['description'],
        'New visual description\nWith a second line.',
      );
      expect(draft['characterId'], 'immutable-id');
      expect(draft['duration'], 3);
      expect(draft['enabled'], isTrue);
    },
  );

  testWidgets(
    'external resets update text without replacing the focused field on each keystroke',
    (tester) async {
      var value = 'Original outline';
      late StateSetter refresh;
      await tester.pumpWidget(
        host(
          StatefulBuilder(
            builder: (context, setState) {
              refresh = setState;
              return ContentTextField(
                value: value,
                label: 'Story outline',
                onChanged: (next) => setState(() => value = next),
              );
            },
          ),
        ),
      );
      final input = find.byType(TextField);
      await tester.enterText(input, 'Edited outline');
      await tester.pump();
      final controller = tester.widget<TextField>(input).controller!;
      controller.selection = const TextSelection.collapsed(offset: 4);
      refresh(() {});
      await tester.pump();
      expect(tester.widget<TextField>(input).controller, same(controller));
      expect(controller.selection.baseOffset, 4);
      refresh(() => value = 'Original outline');
      await tester.pump();
      expect(controller.text, 'Original outline');
    },
  );

  testWidgets(
    'read-only preview uses a bold label beside a locked text field',
    (tester) async {
      await tester.pumpWidget(
        host(
          ValueEditor(
            value: {'description': 'A readable description'},
            readOnly: true,
            onChanged: (_) => fail('Preview must not edit data'),
          ),
        ),
      );
      final field = find.byType(TextField);
      final label = find.text('description:');
      expect(tester.widget<TextField>(field).readOnly, isTrue);
      expect(tester.widget<Text>(label).style!.fontWeight, FontWeight.bold);
      expect(
        tester.getTopLeft(field).dx,
        greaterThan(tester.getRect(label).right),
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('label and text field stay beside each other on narrow screens', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(320, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      host(
        ValueEditor(
          value: {
            'forbiddenContradictions': 'Do not contradict approved canon.',
          },
          onChanged: (_) {},
        ),
      ),
    );
    expect(
      tester.getTopLeft(find.byType(TextField)).dx,
      greaterThan(tester.getTopLeft(find.text('forbiddenContradictions:')).dx),
    );
    expect(tester.takeException(), isNull);
  });
}
