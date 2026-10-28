import 'package:flutter/material.dart';
import 'package:get/get.dart';

import 'feature_module.dart';
import 'feature_registry.dart';

/// 左侧菜单 + 右侧当前功能页面。
class AppShell extends StatelessWidget {
  const AppShell({super.key, required this.current});

  final FeatureModule current;

  @override
  Widget build(BuildContext context) {
    final features = FeatureRegistry.features;
    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: features.indexOf(current),
            labelType: NavigationRailLabelType.all,
            leading: Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Icon(Icons.smart_toy_outlined, size: 32, color: Theme.of(context).colorScheme.primary),
            ),
            destinations: [
              for (final f in features) NavigationRailDestination(icon: Icon(f.icon), label: Text(f.title)),
            ],
            onDestinationSelected: (i) {
              if (features[i] != current) Get.offNamed<void>(features[i].route);
            },
          ),
          const VerticalDivider(width: 1),
          Expanded(child: current.page()),
        ],
      ),
    );
  }
}
