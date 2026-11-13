import 'package:flutter_test/flutter_test.dart';
import 'package:story_studio/projects/model/project.dart';
import 'package:story_studio/workspace/model/workspace_snapshot.dart';
import 'package:story_studio/util/model/job.dart';
import 'package:story_studio/util/model/app_route_parameters.dart';
import 'package:story_studio/util/service/api_client.dart';

void main() {
  test(
    'API URLs keep query parameters and separate thumbnails from originals',
    () {
      final api = ApiClient('http://localhost:8000');
      expect(
        api
            .uri('/api/projects/p/revisions?subject=scene_01')
            .queryParameters['subject'],
        'scene_01',
      );
      expect(
        Uri.parse(
          api.mediaUrl('p', 'images/example.png', preview: true),
        ).queryParameters['width'],
        '640',
      );
      expect(
        Uri.parse(
          api.mediaUrl('p', 'images/example.png'),
        ).queryParameters.containsKey('width'),
        isFalse,
      );
    },
  );
  test('Generated models preserve API project IDs and job state', () {
    final project = Project.fromJson({
      'projectId': 'abc',
      'title': 'World',
      'worldProjectId': null,
    });
    expect(project.toJson()['projectId'], 'abc');
    final job = Job.fromJson({
      'id': 'job',
      'project_id': 'abc',
      'operation': 'assets.generate',
      'status': 'running',
    });
    expect(job.active, isTrue);
    expect(job.toJson()['project_id'], 'abc');
    final snapshot = WorkspaceSnapshot.fromJson({
      'project': project.toJson(),
      'page': 'world',
    });
    expect(snapshot.sections, isEmpty);
  });
  test('Route parameters parse once and secret is one use', () {
    final parameters = AppRouteParameters.fromUri(
      Uri.parse('http://localhost:7357/?secret=one#/assets?project=abc'),
    );
    expect(parameters.projectId, 'abc');
    expect(parameters.page, 'assets');
    expect(parameters.takeSecret(), 'one');
    expect(parameters.takeSecret(), isNull);
  });
}
