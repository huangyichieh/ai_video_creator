import '../../util/service/api_client.dart';
import '../model/world_settings.dart';

class WorldSettingsService {
  final ApiClient api;
  WorldSettingsService(this.api);
  Future<WorldSettingsForm> resolveAddition(
    String id,
    String settingId,
    String decision,
    String expected,
  ) async => WorldSettingsForm.fromJson(
    Map<String, dynamic>.from(
      await api.request(
        'PUT',
        '/api/projects/$id/world-settings/additions/$settingId/suggestion',
        data: {'decision': decision, 'expected': expected},
      ),
    ),
  );
  Future<WorldSettingsForm> add(String id, String text) async =>
      WorldSettingsForm.fromJson(
        Map<String, dynamic>.from(
          await api.request(
            'POST',
            '/api/projects/$id/world-settings/additions',
            data: {'text': text},
          ),
        ),
      );
  Future<WorldSettingsForm> updateAddition(
    String id,
    String settingId,
    String text,
    String expected,
  ) async => WorldSettingsForm.fromJson(
    Map<String, dynamic>.from(
      await api.request(
        'PUT',
        '/api/projects/$id/world-settings/additions/$settingId',
        data: {'text': text, 'expected': expected},
      ),
    ),
  );
  Future<WorldSettingsForm> removeAddition(
    String id,
    String settingId,
    String expected,
  ) async => WorldSettingsForm.fromJson(
    Map<String, dynamic>.from(
      await api.request(
        'DELETE',
        '/api/projects/$id/world-settings/additions/$settingId',
        query: {'expected': expected},
      ),
    ),
  );
  Future<WorldSettingsForm> load(String id) async => WorldSettingsForm.fromJson(
    Map<String, dynamic>.from(
      await api.request('GET', '/api/projects/$id/world-settings'),
    ),
  );
  Future<WorldSettingsForm> save(
    String id,
    WorldSettingsData settings,
    String language,
    String expected,
  ) async => WorldSettingsForm.fromJson(
    Map<String, dynamic>.from(
      await api.request(
        'PUT',
        '/api/projects/$id/world-settings',
        data: {
          'settings': settings.toJson(),
          'default_language': language,
          'expected': expected,
        },
      ),
    ),
  );
  Future<WorldSettingsForm> resolve(
    String id,
    WorldSettingsData settings,
    String decision,
    String expected,
  ) async => WorldSettingsForm.fromJson(
    Map<String, dynamic>.from(
      await api.request(
        'POST',
        '/api/projects/$id/world-settings/resolve',
        data: {
          'settings': settings.toJson(),
          'decision': decision,
          'expected': expected,
        },
      ),
    ),
  );
}
