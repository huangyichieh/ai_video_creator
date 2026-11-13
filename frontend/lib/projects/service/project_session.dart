import '../../util/service/browser_preferences.dart' as storage;

/// Browser navigation preferences, separate from project/API data.
class ProjectSession {
  static const pages = {
    'world',
    'assets',
    'story',
    'animations',
    'final',
    'analytics',
  };
  final String prefix;
  ProjectSession({this.prefix = 'storyStudio.workspace.v1'});
  String? get projectId => storage.readPreference('$prefix.project');
  String? get episodeId => storage.readPreference('$prefix.episode');
  String get page {
    final value = storage.readPreference('$prefix.page');
    return pages.contains(value) ? value! : 'world';
  }

  void remember(String id, String page, {String? episodeId}) {
    if (projectId != id) {
      storage.removePreference('$prefix.episode');
    }
    storage.writePreference('$prefix.project', id);
    storage.writePreference(
      '$prefix.page',
      pages.contains(page) ? page : 'world',
    );
    if (episodeId != null) {
      storage.writePreference('$prefix.episode', episodeId);
    }
  }

  void clear() {
    for (final field in ['project', 'episode', 'page']) {
      storage.removePreference('$prefix.$field');
    }
  }
}
