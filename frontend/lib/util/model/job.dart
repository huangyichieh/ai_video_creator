import 'package:json_annotation/json_annotation.dart';
part 'job.g.dart';

@JsonSerializable(fieldRename: FieldRename.snake)
class Job {
  final String id;
  final String projectId;
  final String operation;
  final String status;
  final Map<String, dynamic>? arguments;
  final dynamic result;
  final String? error;
  final String createdAt;
  final String updatedAt;
  final Map<String, dynamic>? progress;
  Job({
    required this.id,
    required this.projectId,
    required this.operation,
    required this.status,
    this.arguments,
    this.result,
    this.error,
    this.createdAt = '',
    this.updatedAt = '',
    this.progress,
  });
  bool get active => ['queued', 'running', 'stopping'].contains(status);
  factory Job.fromJson(Map<String, dynamic> json) => _$JobFromJson(json);
  Map<String, dynamic> toJson() => _$JobToJson(this);
}
