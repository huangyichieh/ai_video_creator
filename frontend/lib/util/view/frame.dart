import 'package:flutter/material.dart';

class AppFrame extends StatelessWidget {
  final Widget child;
  const AppFrame({required this.child, super.key});
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(8),
    child: SizedBox.expand(child: child),
  );
}
