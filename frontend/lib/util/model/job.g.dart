// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'job.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

Job _$JobFromJson(Map<String, dynamic> json) => Job(
  id: json['id'] as String,
  projectId: json['project_id'] as String,
  operation: json['operation'] as String,
  status: json['status'] as String,
  arguments: json['arguments'] as Map<String, dynamic>?,
  result: json['result'],
  error: json['error'] as String?,
  createdAt: json['created_at'] as String? ?? '',
  updatedAt: json['updated_at'] as String? ?? '',
  progress: json['progress'] as Map<String, dynamic>?,
);

Map<String, dynamic> _$JobToJson(Job instance) => <String, dynamic>{
  'id': instance.id,
  'project_id': instance.projectId,
  'operation': instance.operation,
  'status': instance.status,
  'arguments': instance.arguments,
  'result': instance.result,
  'error': instance.error,
  'created_at': instance.createdAt,
  'updated_at': instance.updatedAt,
  'progress': instance.progress,
};
