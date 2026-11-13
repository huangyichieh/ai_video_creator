import 'package:flutter/material.dart';
import 'package:get/get.dart';

class TextDialog extends StatelessWidget {
  final String title;
  final String text;
  const TextDialog(this.title, this.text, {super.key});
  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(title),
    content: SizedBox(
      width: 500,
      child: SingleChildScrollView(child: SelectableText(text)),
    ),
    actions: [
      TextButton(onPressed: () => Get.back(), child: const Text('Close')),
    ],
  );
}
