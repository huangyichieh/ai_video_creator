/// URL transport is parsed once; these are navigation values, not API data.
class AppRouteParameters {
  final String apiBase;
  final String? projectId;
  final String page;
  String? _secret;
  AppRouteParameters({
    required this.apiBase,
    this.projectId,
    this.page = 'world',
    String? secret,
  }) : _secret = secret;
  factory AppRouteParameters.fromUri(Uri uri) {
    final fragment = Uri.tryParse(uri.fragment);
    final values = {...uri.queryParameters, ...?fragment?.queryParameters};
    const configured = String.fromEnvironment('API_BASE_URL');
    final api = configured.isNotEmpty
        ? configured
        : (uri.port == 7357 ? 'http://127.0.0.1:8000' : uri.origin);
    return AppRouteParameters(
      apiBase: api,
      projectId: values['project'],
      page: fragment?.path.replaceAll('/', '') ?? 'world',
      secret: values['secret'],
    );
  }
  String? takeSecret() {
    final value = _secret;
    _secret = null;
    return value;
  }
}
