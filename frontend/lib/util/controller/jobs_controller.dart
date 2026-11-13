import 'dart:async';
import 'package:get/get.dart';
import '../model/job.dart';
import '../service/api_client.dart';
import '../../projects/controller/project_controller.dart';

class JobsController extends GetxController {
  final ApiClient api;
  final jobs = <Job>[].obs;
  final errorMessage = Rx<String?>(null);
  Timer? timer;
  bool fetching = false;
  JobsController(this.api);
  @override
  void onInit() {
    super.onInit();
    refreshJobs();
    timer = Timer.periodic(const Duration(seconds: 2), (_) => refreshJobs());
  }

  Future<String?> refreshJobs() async {
    if (fetching) return null;
    fetching = true;
    try {
      final activeIds = jobs
          .where((job) => job.active)
          .map((job) => job.id)
          .toSet();
      final updated = (await api.request('GET', '/api/jobs') as List)
          .map((row) => Job.fromJson(Map<String, dynamic>.from(row)))
          .toList();
      if (updated.any((job) => !job.active && activeIds.contains(job.id)) &&
          Get.isRegistered<ProjectController>()) {
        await Get.find<ProjectController>().loadProjects();
      }
      jobs.assignAll(updated);
      errorMessage.value = null;
      return null;
    } catch (error) {
      errorMessage.value = error.toString();
      return error.toString();
    } finally {
      fetching = false;
    }
  }

  Future<String?> stop(String id) async {
    try {
      await api.request('POST', '/api/jobs/$id/stop');
      return await refreshJobs();
    } catch (error) {
      return error.toString();
    }
  }

  @override
  void onClose() {
    timer?.cancel();
    super.onClose();
  }
}
