import 'package:flutter/material.dart';

/// 网络图片的缩略图：加载失败（比如 QQ 的图片地址过了几个小时失效）时显示占位，点开看大图。
class ImageThumb extends StatelessWidget {
  const ImageThumb({super.key, required this.url, this.size = 48, this.tooltip});

  final String url;
  final double size;
  final String? tooltip;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final px = (size * MediaQuery.devicePixelRatioOf(context)).round();
    Widget placeholder(IconData icon, {String? message}) => Tooltip(
          message: message ?? '',
          child: Container(
            width: size,
            height: size,
            color: theme.colorScheme.surfaceContainerHighest,
            child: Icon(icon, size: size * 0.4, color: theme.colorScheme.outline),
          ),
        );
    return Tooltip(
      message: tooltip ?? '',
      child: InkWell(
        onTap: () => showImageViewer(context, url),
        borderRadius: BorderRadius.circular(4),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: Image.network(
            url,
            width: size,
            height: size,
            fit: BoxFit.cover,
            cacheWidth: px,
            loadingBuilder: (context, child, progress) => progress == null ? child : placeholder(Icons.image_outlined),
            errorBuilder: (context, error, stack) =>
                placeholder(Icons.broken_image_outlined, message: '图片加载失败（QQ 的图片地址几个小时后会失效）'),
          ),
        ),
      ),
    );
  }
}

/// 一排缩略图。
class ImageThumbs extends StatelessWidget {
  const ImageThumbs({super.key, required this.urls, this.size = 48});

  final List<String> urls;
  final double size;

  @override
  Widget build(BuildContext context) => Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [for (final u in urls) ImageThumb(url: u, size: size)],
      );
}

/// 看大图：可以缩放、拖动；加载失败时说明原因。
Future<void> showImageViewer(BuildContext context, String url) => showDialog<void>(
      context: context,
      builder: (context) {
        final theme = Theme.of(context);
        return Dialog(
          clipBehavior: Clip.antiAlias,
          child: Stack(
            children: [
              InteractiveViewer(
                maxScale: 8,
                child: Image.network(
                  url,
                  fit: BoxFit.contain,
                  loadingBuilder: (context, child, progress) => progress == null
                      ? child
                      : const SizedBox.square(
                          dimension: 240,
                          child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
                        ),
                  errorBuilder: (context, error, stack) => Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text('图片加载失败，地址可能已经失效（QQ 的图片地址几个小时后会失效）。\n$url',
                        style: TextStyle(color: theme.colorScheme.error)),
                  ),
                ),
              ),
              Positioned(
                right: 4,
                top: 4,
                child: IconButton.filledTonal(
                  tooltip: '关闭',
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.close),
                ),
              ),
            ],
          ),
        );
      },
    );
