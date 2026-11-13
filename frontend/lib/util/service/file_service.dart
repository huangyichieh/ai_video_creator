import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';
import 'package:web/web.dart' as web;
import 'api_client.dart';

class FileService {
  final ApiClient api;
  FileService(this.api);
  Future<Map<String, dynamic>?> chooseUpload() async {
    final input = web.HTMLInputElement()..type = 'file';
    final done = Completer<Map<String, dynamic>?>();
    input.onChange.listen((_) async {
      try {
        final file = input.files?.item(0);
        if (file == null) {
          done.complete(null);
          return;
        }
        final buffer = await file.arrayBuffer().toDart;
        done.complete(
          await api.upload(Uint8List.view(buffer.toDart), file.name),
        );
      } catch (error, stack) {
        done.completeError(error, stack);
      }
    });
    input.addEventListener(
      'cancel',
      ((web.Event event) {
        if (!done.isCompleted) done.complete(null);
      }).toJS,
    );
    input.click();
    return done.future;
  }
}
