import 'package:json_annotation/json_annotation.dart';
part 'project.g.dart';

@JsonSerializable()
class Project {
  final String projectId;
  final String title;
  final String? worldProjectId;
  final String language;
  final int targetDurationSec;
  final String updatedAt;
  final String? createdAt;
  final Map<String, dynamic> steps;
  final Map<String, dynamic> approvals;
  final String storyStatus;
  final String animationStatus;
  Project({
    required this.projectId,
    required this.title,
    this.worldProjectId,
    this.language = 'zh-TW',
    this.targetDurationSec = 0,
    this.updatedAt = '',
    this.createdAt,
    this.steps = const {},
    this.approvals = const {},
    this.storyStatus = 'Draft',
    this.animationStatus = 'Not started',
  });
  factory Project.fromJson(Map<String, dynamic> json) =>
      _$ProjectFromJson(json);
  Map<String, dynamic> toJson() => _$ProjectToJson(this);
}
