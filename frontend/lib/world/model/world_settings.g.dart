// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'world_settings.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

WorldSettingsData _$WorldSettingsDataFromJson(Map<String, dynamic> json) =>
    WorldSettingsData(
      name: json['name'] as String? ?? '',
      premise: json['premise'] as String? ?? '',
      eraSetting: json['eraSetting'] as String? ?? '',
      coreRules: json['coreRules'] as String? ?? '',
      technologyMagicLimits: json['technologyMagicLimits'] as String? ?? '',
      societyKeyPlaces: json['societyKeyPlaces'] as String? ?? '',
      tone: json['tone'] as String? ?? '',
      forbiddenContradictions: json['forbiddenContradictions'] as String? ?? '',
    );

Map<String, dynamic> _$WorldSettingsDataToJson(WorldSettingsData instance) =>
    <String, dynamic>{
      'name': instance.name,
      'premise': instance.premise,
      'eraSetting': instance.eraSetting,
      'coreRules': instance.coreRules,
      'technologyMagicLimits': instance.technologyMagicLimits,
      'societyKeyPlaces': instance.societyKeyPlaces,
      'tone': instance.tone,
      'forbiddenContradictions': instance.forbiddenContradictions,
    };

WorldFormReview _$WorldFormReviewFromJson(
  Map<String, dynamic> json,
) => WorldFormReview(
  original: WorldSettingsData.fromJson(
    json['original'] as Map<String, dynamic>,
  ),
  suggested: WorldSettingsData.fromJson(
    json['suggested'] as Map<String, dynamic>,
  ),
  reviewId: json['reviewId'] as String? ?? '',
  summary: json['summary'] as String? ?? '',
  decision: json['decision'] as String? ?? '',
  current: json['current'] as bool? ?? false,
  reasons:
      (json['reasons'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const [],
  conflicts:
      (json['conflicts'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const [],
  accepted: json['accepted'] == null
      ? null
      : WorldSettingsData.fromJson(json['accepted'] as Map<String, dynamic>),
);

Map<String, dynamic> _$WorldFormReviewToJson(WorldFormReview instance) =>
    <String, dynamic>{
      'reviewId': instance.reviewId,
      'summary': instance.summary,
      'decision': instance.decision,
      'current': instance.current,
      'reasons': instance.reasons,
      'conflicts': instance.conflicts,
      'original': instance.original.toJson(),
      'suggested': instance.suggested.toJson(),
      'accepted': instance.accepted?.toJson(),
    };

WorldSettingsForm _$WorldSettingsFormFromJson(Map<String, dynamic> json) =>
    WorldSettingsForm(
      settings: WorldSettingsData.fromJson(
        json['settings'] as Map<String, dynamic>,
      ),
      version: json['version'] as String,
      defaultLanguage: json['defaultLanguage'] as String? ?? 'zh-TW',
      approved: json['approved'] as bool? ?? false,
      review: json['review'] == null
          ? null
          : WorldFormReview.fromJson(json['review'] as Map<String, dynamic>),
      additions:
          (json['additions'] as List<dynamic>?)
              ?.map((e) => WorldAddition.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
    );

Map<String, dynamic> _$WorldSettingsFormToJson(WorldSettingsForm instance) =>
    <String, dynamic>{
      'settings': instance.settings.toJson(),
      'defaultLanguage': instance.defaultLanguage,
      'version': instance.version,
      'approved': instance.approved,
      'review': instance.review?.toJson(),
      'additions': instance.additions.map((e) => e.toJson()).toList(),
    };

WorldAddition _$WorldAdditionFromJson(Map<String, dynamic> json) =>
    WorldAddition(
      settingId: json['settingId'] as String,
      text: json['text'] as String,
      version: json['version'] as String,
      approved: json['approved'] as bool? ?? false,
      approvedAt: json['approvedAt'] as String? ?? '',
      review: json['review'] == null
          ? null
          : AdditionReview.fromJson(json['review'] as Map<String, dynamic>),
    );

Map<String, dynamic> _$WorldAdditionToJson(WorldAddition instance) =>
    <String, dynamic>{
      'settingId': instance.settingId,
      'text': instance.text,
      'version': instance.version,
      'approvedAt': instance.approvedAt,
      'approved': instance.approved,
      'review': instance.review?.toJson(),
    };

AdditionReview _$AdditionReviewFromJson(
  Map<String, dynamic> json,
) => AdditionReview(
  original: json['original'] as String,
  suggested: json['suggested'] as String,
  summary: json['summary'] as String? ?? '',
  reasons:
      (json['reasons'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const [],
  conflicts:
      (json['conflicts'] as List<dynamic>?)?.map((e) => e as String).toList() ??
      const [],
  current: json['current'] as bool? ?? false,
  decision: json['decision'] as String? ?? '',
);

Map<String, dynamic> _$AdditionReviewToJson(AdditionReview instance) =>
    <String, dynamic>{
      'original': instance.original,
      'suggested': instance.suggested,
      'summary': instance.summary,
      'decision': instance.decision,
      'reasons': instance.reasons,
      'conflicts': instance.conflicts,
      'current': instance.current,
    };
