import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import '../../util/controller/jobs_controller.dart';
import '../controller/workspace_controller.dart';

class TaskButton extends StatelessWidget {
  final WorkspaceController controller;
  final String operation, label;
  final Map<String, dynamic> arguments;
  final VoidCallback onPressed;
  const TaskButton(
    this.controller, {
    required this.operation,
    required this.label,
    required this.onPressed,
    this.arguments = const {},
    super.key,
  });
  @override
  Widget build(BuildContext context) => Obx(() {
    final pending = controller.pendingCommands.contains(
      controller.commandKey(operation, arguments),
    );
    final job = Get.find<JobsController>().jobs.firstWhereOrNull(
      (job) =>
          job.active &&
          job.projectId == controller.projectId.value &&
          job.operation == operation &&
          arguments.entries.every(
            (arg) =>
                jsonEncode(job.arguments?[arg.key]) == jsonEncode(arg.value),
          ),
    );
    final busy = pending || job != null;
    final text = !busy
        ? label
        : pending || job?.status == 'queued'
        ? 'Queuing…'
        : operation.contains('review')
        ? 'Reviewing…'
        : 'Generating…';
    return OutlinedButton(
      onPressed: busy ? null : onPressed,
      child: Text(text),
    );
  });
}
