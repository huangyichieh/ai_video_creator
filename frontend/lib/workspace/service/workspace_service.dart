import '../../util/model/job.dart';
import '../../util/service/api_client.dart';
import '../model/workspace_snapshot.dart';

class WorkspaceService {
  final ApiClient api;
  WorkspaceService(this.api);
  Future<WorkspaceSnapshot> load(String pid, String page) async =>
      WorkspaceSnapshot.fromJson(
        await api.request('GET', '/api/projects/$pid/workspace/$page'),
      );
  Future<Job> run(
    String pid,
    String operation,
    Map<String, dynamic> arguments,
  ) async => Job.fromJson(
    await api.request(
      'POST',
      '/api/jobs',
      data: {'project_id': pid, 'operation': operation, 'arguments': arguments},
    ),
  );
  Future<void> save(String pid, Artifact artifact, dynamic value) async =>
      api.request(
        'PUT',
        '/api/projects/$pid/artifacts/${artifact.id}',
        data: {'value': value, 'expected': artifact.version},
      );
  Future<Map<String, dynamic>> prepare(
    String pid,
    String operation,
    Map<String, dynamic> args,
  ) async => Map<String, dynamic>.from(
    await api.request(
      'POST',
      '/api/projects/$pid/prepare',
      data: {'operation': operation, 'arguments': args},
    ),
  );
}
