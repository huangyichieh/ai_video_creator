final _values = <String, String>{};
String? readPreference(String key) => _values[key];
void writePreference(String key, String value) => _values[key] = value;
void removePreference(String key) => _values.remove(key);
