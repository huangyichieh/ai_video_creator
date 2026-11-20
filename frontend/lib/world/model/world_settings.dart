import 'package:json_annotation/json_annotation.dart';
part 'world_settings.g.dart';

const worldFields = {
  'name': 'World name',
  'premise': 'Premise',
  'eraSetting': 'Era & setting',
  'coreRules': 'Core rules',
  'technologyMagicLimits': 'Technology/magic & limits',
  'societyKeyPlaces': 'Society & key places',
  'tone': 'Tone',
  'forbiddenContradictions': 'Forbidden contradictions',
};

const worldFieldHelp = {
  'name': 'World title. Example: Five Elements.',
  'defaultLanguage': 'Content language. Example: en or zh-TW.',
  'premise': 'Core idea. Example: Most people control one of five elements.',
  'eraSetting': 'When and where. Example: An ancient island kingdom.',
  'coreRules': 'Always-true rules. Example: Each person controls one element.',
  'technologyMagicLimits':
      'Powers and limits. Example: Magic consumes stamina.',
  'societyKeyPlaces':
      'Society and locations. Example: Five guilds share a capital.',
  'tone': 'Mood. Example: Hopeful adventure.',
  'forbiddenContradictions': 'Never break these. Example: No resurrection.',
};

@JsonSerializable()
class WorldSettingsData {
  final String name,
      premise,
      eraSetting,
      coreRules,
      technologyMagicLimits,
      societyKeyPlaces,
      tone,
      forbiddenContradictions;
  const WorldSettingsData({
    this.name = '',
    this.premise = '',
    this.eraSetting = '',
    this.coreRules = '',
    this.technologyMagicLimits = '',
    this.societyKeyPlaces = '',
    this.tone = '',
    this.forbiddenContradictions = '',
  });
  factory WorldSettingsData.fromJson(Map<String, dynamic> json) =>
      _$WorldSettingsDataFromJson(json);
  Map<String, dynamic> toJson() => _$WorldSettingsDataToJson(this);
  Map<String, String> get fields => toJson().cast<String, String>();
}

@JsonSerializable(explicitToJson: true)
class WorldFormReview {
  final String reviewId, summary, decision;
  final bool current;
  final List<String> reasons, conflicts;
  final WorldSettingsData original, suggested;
  final WorldSettingsData? accepted;
  const WorldFormReview({
    required this.original,
    required this.suggested,
    this.reviewId = '',
    this.summary = '',
    this.decision = '',
    this.current = false,
    this.reasons = const [],
    this.conflicts = const [],
    this.accepted,
  });
  factory WorldFormReview.fromJson(Map<String, dynamic> json) =>
      _$WorldFormReviewFromJson(json);
  Map<String, dynamic> toJson() => _$WorldFormReviewToJson(this);
}

@JsonSerializable(explicitToJson: true)
class WorldSettingsForm {
  final WorldSettingsData settings;
  final String defaultLanguage, version;
  final bool approved;
  final WorldFormReview? review;
  final List<WorldAddition> additions;
  const WorldSettingsForm({
    required this.settings,
    required this.version,
    this.defaultLanguage = 'zh-TW',
    this.approved = false,
    this.review,
    this.additions = const [],
  });
  factory WorldSettingsForm.fromJson(Map<String, dynamic> json) =>
      _$WorldSettingsFormFromJson(json);
  Map<String, dynamic> toJson() => _$WorldSettingsFormToJson(this);
}

@JsonSerializable(explicitToJson: true)
class WorldAddition {
  final String settingId, text, version, approvedAt;
  final bool approved;
  final AdditionReview? review;
  const WorldAddition({
    required this.settingId,
    required this.text,
    required this.version,
    this.approved = false,
    this.approvedAt = '',
    this.review,
  });
  factory WorldAddition.fromJson(Map<String, dynamic> json) =>
      _$WorldAdditionFromJson(json);
  Map<String, dynamic> toJson() => _$WorldAdditionToJson(this);
}

@JsonSerializable()
class AdditionReview {
  final String original, suggested, summary, decision;
  final List<String> reasons, conflicts;
  final bool current;
  const AdditionReview({
    required this.original,
    required this.suggested,
    this.summary = '',
    this.reasons = const [],
    this.conflicts = const [],
    this.current = false,
    this.decision = '',
  });
  factory AdditionReview.fromJson(Map<String, dynamic> json) =>
      _$AdditionReviewFromJson(json);
  Map<String, dynamic> toJson() => _$AdditionReviewToJson(this);
}
