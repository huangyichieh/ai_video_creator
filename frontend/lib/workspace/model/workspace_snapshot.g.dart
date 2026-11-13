// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'workspace_snapshot.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

WorkspaceSnapshot _$WorkspaceSnapshotFromJson(Map<String, dynamic> json) =>
    WorkspaceSnapshot(
      project: Project.fromJson(json['project'] as Map<String, dynamic>),
      page: json['page'] as String,
      sections:
          (json['sections'] as List<dynamic>?)
              ?.map((e) => Artifact.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      media:
          (json['media'] as List<dynamic>?)
              ?.map((e) => MediaAsset.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      characters:
          (json['characters'] as List<dynamic>?)
              ?.map((e) => e as Map<String, dynamic>)
              .toList() ??
          const [],
      operations:
          (json['operations'] as List<dynamic>?)
              ?.map((e) => Operation.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      modelPreferences:
          json['modelPreferences'] as Map<String, dynamic>? ?? const {},
      computed: json['computed'] as Map<String, dynamic>? ?? const {},
    );

Map<String, dynamic> _$WorkspaceSnapshotToJson(WorkspaceSnapshot instance) =>
    <String, dynamic>{
      'project': instance.project.toJson(),
      'page': instance.page,
      'sections': instance.sections.map((e) => e.toJson()).toList(),
      'media': instance.media.map((e) => e.toJson()).toList(),
      'characters': instance.characters,
      'operations': instance.operations.map((e) => e.toJson()).toList(),
      'modelPreferences': instance.modelPreferences,
      'computed': instance.computed,
    };

Artifact _$ArtifactFromJson(Map<String, dynamic> json) => Artifact(
  id: json['id'] as String,
  title: json['title'] as String,
  data: json['data'],
  version: json['version'] as String,
);

Map<String, dynamic> _$ArtifactToJson(Artifact instance) => <String, dynamic>{
  'id': instance.id,
  'title': instance.title,
  'data': instance.data,
  'version': instance.version,
};

MediaAsset _$MediaAssetFromJson(Map<String, dynamic> json) => MediaAsset(
  file: json['file'] as String,
  title: json['title'] as String,
  kind: json['kind'] as String,
);

Map<String, dynamic> _$MediaAssetToJson(MediaAsset instance) =>
    <String, dynamic>{
      'file': instance.file,
      'title': instance.title,
      'kind': instance.kind,
    };

Operation _$OperationFromJson(Map<String, dynamic> json) => Operation(
  operation: json['operation'] as String,
  title: json['title'] as String,
  description: json['description'] as String? ?? '',
  parameters:
      (json['parameters'] as List<dynamic>?)
          ?.map((e) => OperationParameter.fromJson(e as Map<String, dynamic>))
          .toList() ??
      const [],
);

Map<String, dynamic> _$OperationToJson(Operation instance) => <String, dynamic>{
  'operation': instance.operation,
  'title': instance.title,
  'description': instance.description,
  'parameters': instance.parameters.map((e) => e.toJson()).toList(),
};

OperationParameter _$OperationParameterFromJson(Map<String, dynamic> json) =>
    OperationParameter(
      name: json['name'] as String,
      required: json['required'] as bool? ?? false,
      defaultValue: json['defaultValue'],
      schema: json['schema'] as Map<String, dynamic>? ?? const {},
    );

Map<String, dynamic> _$OperationParameterToJson(OperationParameter instance) =>
    <String, dynamic>{
      'name': instance.name,
      'required': instance.required,
      'defaultValue': instance.defaultValue,
      'schema': instance.schema,
    };
