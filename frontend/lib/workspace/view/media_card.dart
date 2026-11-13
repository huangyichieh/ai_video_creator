import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:web/web.dart' as web;
import '../../util/service/api_client.dart';
import '../model/workspace_snapshot.dart';

class MediaCard extends StatelessWidget {
  final String projectId;
  final MediaAsset asset;
  const MediaCard(this.projectId, this.asset, {super.key});
  @override
  Widget build(BuildContext context) {
    final api = Get.find<ApiClient>();
    final originalUrl = api.mediaUrl(projectId, asset.file);
    final url = api.mediaUrl(
      projectId,
      asset.file,
      preview: asset.kind == 'image',
    );
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(asset.title, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            if (asset.kind == 'image')
              InkWell(
                onTap: () => Get.dialog(
                  Dialog(
                    child: SizedBox(
                      width: 1200,
                      height: 800,
                      child: Column(
                        children: [
                          Align(
                            alignment: Alignment.topRight,
                            child: IconButton(
                              onPressed: () => Get.back(),
                              icon: const Icon(Icons.close),
                            ),
                          ),
                          Expanded(
                            child: InteractiveViewer(
                              minScale: .2,
                              maxScale: 8,
                              child: Image.network(
                                originalUrl,
                                fit: BoxFit.contain,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 240),
                  child: Image.network(
                    url,
                    fit: BoxFit.contain,
                    errorBuilder: (_, error, stack) =>
                        Text('Preview unavailable: $error'),
                  ),
                ),
              )
            else
              SizedBox(
                height: asset.kind == 'video' ? 260 : 56,
                child: HtmlElementView.fromTagName(
                  tagName: asset.kind == 'video' ? 'video' : 'audio',
                  onElementCreated: (element) {
                    final media = element as web.HTMLMediaElement;
                    media.src = url;
                    media.controls = true;
                    media.preload = 'metadata';
                    media.style.width = '100%';
                    media.style.height = '100%';
                  },
                ),
              ),
            TextButton.icon(
              onPressed: () => web.window.open(
                api.mediaUrl(projectId, asset.file, download: true),
                '_blank',
              ),
              icon: const Icon(Icons.download),
              label: const Text('Download'),
            ),
          ],
        ),
      ),
    );
  }
}
