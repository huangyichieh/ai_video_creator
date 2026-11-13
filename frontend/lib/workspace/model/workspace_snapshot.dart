import 'package:json_annotation/json_annotation.dart';
import '../../projects/model/project.dart';
part 'workspace_snapshot.g.dart';

@JsonSerializable(explicitToJson: true)
class WorkspaceSnapshot {
  final Project project;
  final String page;
  final List<Artifact> sections;
  final List<MediaAsset> media;
  final List<Map<String, dynamic>> characters;
  final List<Operation> operations;
  final Map<String, dynamic> modelPreferences;
  final Map<String, dynamic> computed;
  WorkspaceSnapshot({
    required this.project,
    required this.page,
    this.sections = const [],
    this.media = const [],
    this.characters = const [],
    this.operations = const [],
    this.modelPreferences = const {},
    this.computed = const {},
  });
  factory WorkspaceSnapshot.fromJson(Map<String, dynamic> json) =>
      _$WorkspaceSnapshotFromJson(json);
  Map<String, dynamic> toJson() => _$WorkspaceSnapshotToJson(this);
}

@JsonSerializable()
class Artifact {
  final String id;
  final String title;
  final dynamic data;
  final String version;
  Artifact({
    required this.id,
    required this.title,
    required this.data,
    required this.version,
  });
  factory Artifact.fromJson(Map<String, dynamic> json) =>
      _$ArtifactFromJson(json);
  Map<String, dynamic> toJson() => _$ArtifactToJson(this);
}

@JsonSerializable()
class MediaAsset {
  final String file;
  final String title;
  final String kind;
  MediaAsset({required this.file, required this.title, required this.kind});
  factory MediaAsset.fromJson(Map<String, dynamic> json) =>
      _$MediaAssetFromJson(json);
  Map<String, dynamic> toJson() => _$MediaAssetToJson(this);
}

@JsonSerializable(explicitToJson: true)
class Operation {
  final String operation;
  final String title;
  final String description;
  final List<OperationParameter> parameters;
  Operation({
    required this.operation,
    required this.title,
    this.description = '',
    this.parameters = const [],
  });
  factory Operation.fromJson(Map<String, dynamic> json) =>
      _$OperationFromJson(json);
  Map<String, dynamic> toJson() => _$OperationToJson(this);
}

@JsonSerializable()
class OperationParameter {
  final String name;
  final bool required;
  final dynamic defaultValue;
  final Map<String, dynamic> schema;
  OperationParameter({
    required this.name,
    this.required = false,
    this.defaultValue,
    this.schema = const {},
  });
  factory OperationParameter.fromJson(Map<String, dynamic> json) =>
      _$OperationParameterFromJson(json);
  Map<String, dynamic> toJson() => _$OperationParameterToJson(this);
}
