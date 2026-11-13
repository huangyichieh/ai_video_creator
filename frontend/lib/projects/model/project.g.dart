// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'project.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

Project _$ProjectFromJson(Map<String, dynamic> json) => Project(
  projectId: json['projectId'] as String,
  title: json['title'] as String,
  worldProjectId: json['worldProjectId'] as String?,
  language: json['language'] as String? ?? 'zh-TW',
  targetDurationSec: (json['targetDurationSec'] as num?)?.toInt() ?? 0,
  updatedAt: json['updatedAt'] as String? ?? '',
  createdAt: json['createdAt'] as String?,
  steps: json['steps'] as Map<String, dynamic>? ?? const {},
  approvals: json['approvals'] as Map<String, dynamic>? ?? const {},
  storyStatus: json['storyStatus'] as String? ?? 'Draft',
  animationStatus: json['animationStatus'] as String? ?? 'Not started',
);

Map<String, dynamic> _$ProjectToJson(Project instance) => <String, dynamic>{
  'projectId': instance.projectId,
  'title': instance.title,
  'worldProjectId': instance.worldProjectId,
  'language': instance.language,
  'targetDurationSec': instance.targetDurationSec,
  'updatedAt': instance.updatedAt,
  'createdAt': instance.createdAt,
  'steps': instance.steps,
  'approvals': instance.approvals,
  'storyStatus': instance.storyStatus,
  'animationStatus': instance.animationStatus,
};
