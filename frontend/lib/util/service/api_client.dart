import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;

class ApiClient {
  final String base;
  final http.Client client;
  ApiClient(this.base, {http.Client? client})
    : client = client ?? http.Client();
  Uri uri(String path, [Map<String, String>? query]) =>
      Uri.parse('$base$path').replace(queryParameters: query);
  Future<dynamic> request(
    String method,
    String path, {
    Object? data,
    Map<String, String>? query,
  }) async {
    final request = http.Request(method, uri(path, query));
    request.headers['Content-Type'] = 'application/json';
    if (data != null) request.body = jsonEncode(data);
    final response = await http.Response.fromStream(await client.send(request));
    final body = response.body.isEmpty
        ? null
        : jsonDecode(utf8.decode(response.bodyBytes));
    if (response.statusCode >= 400) {
      throw Exception(body is Map ? body['detail'] ?? body : body);
    }
    return body;
  }

  Future<Map<String, dynamic>> upload(Uint8List bytes, String filename) async {
    final request = http.MultipartRequest('POST', uri('/api/uploads'))
      ..files.add(
        http.MultipartFile.fromBytes('file', bytes, filename: filename),
      );
    final response = await http.Response.fromStream(await client.send(request));
    final result = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode >= 400) throw Exception(result['detail']);
    return result;
  }

  String mediaUrl(
    String pid,
    String file, {
    bool download = false,
    bool preview = false,
  }) => uri(
    '/api/projects/$pid/media/${file.split('/').map(Uri.encodeComponent).join('/')}',
    {if (download) 'download': 'true', if (preview) 'width': '640'},
  ).toString();
}
