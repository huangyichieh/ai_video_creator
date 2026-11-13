import 'package:web/web.dart' as web;

String? readPreference(String key) {
  try {
    return web.window.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

void writePreference(String key, String value) {
  try {
    web.window.localStorage.setItem(key, value);
  } catch (_) {
    // Navigation still works when browser storage is unavailable.
  }
}

void removePreference(String key) {
  try {
    web.window.localStorage.removeItem(key);
  } catch (_) {
    // Private browsing may disable persistent storage.
  }
}
