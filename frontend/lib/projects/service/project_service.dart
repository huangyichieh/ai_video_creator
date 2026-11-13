import '../../util/service/api_client.dart';
import '../model/project.dart';

class ProjectService {
  final ApiClient api;
  ProjectService(this.api);
  Future<List<Project>> list() async =>
      (await api.request('GET', '/api/projects') as List)
          .map((row) => Project.fromJson(Map<String, dynamic>.from(row)))
          .toList();
  Future<Project> create(String name, String premise, String language) async =>
      Project.fromJson(
        await api.request(
          'POST',
          '/api/projects',
          data: {'name': name, 'premise': premise, 'language': language},
        ),
      );
  Future<void> remove(String id, String confirmation) async => api.request(
    'DELETE',
    '/api/projects/$id',
    data: {'confirmation': confirmation},
  );
}
