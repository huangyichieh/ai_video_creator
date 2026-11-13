import 'package:flutter/material.dart';
import 'package:get/get.dart';

class AppLoadingIcon extends StatelessWidget {
  const AppLoadingIcon({super.key});
  @override
  Widget build(BuildContext context) => const Center(
    child: Padding(
      padding: EdgeInsets.all(16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(),
          SizedBox(height: 8),
          Text('Loading…'),
        ],
      ),
    ),
  );
}

class LoadingPage extends StatefulWidget {
  const LoadingPage({super.key});
  @override
  State<LoadingPage> createState() => _LoadingPageState();
}

class _LoadingPageState extends State<LoadingPage> {
  @override
  void initState() {
    super.initState();
    final task = (Get.arguments as Map)['task'] as Future;
    task.whenComplete(() {
      if (mounted) Get.back();
    });
  }

  @override
  Widget build(BuildContext context) => const Scaffold(body: AppLoadingIcon());
}
