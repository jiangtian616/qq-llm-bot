import 'package:get/get.dart';

import 'app_shell.dart';
import 'feature_registry.dart';

class AppRoutes {
  static String get initial => FeatureRegistry.initial.route;

  static List<GetPage<dynamic>> get pages => [
        for (final f in FeatureRegistry.features)
          GetPage<dynamic>(
            name: f.route,
            page: () => AppShell(current: f),
            binding: BindingsBuilder(f.bind),
            transition: Transition.noTransition,
          ),
      ];
}
