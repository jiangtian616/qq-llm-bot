import '../features/config/config_feature.dart';
import '../features/dashboard/dashboard_feature.dart';
import '../features/logs/logs_feature.dart';
import '../features/queue/queue_feature.dart';
import '../features/sessions/sessions_feature.dart';
import '../features/settings/settings_feature.dart';
import 'feature_module.dart';

/// 所有功能页面。顺序就是左侧菜单的顺序。
class FeatureRegistry {
  static final List<FeatureModule> features = [
    dashboardFeature,
    sessionsFeature,
    queueFeature,
    configFeature,
    logsFeature,
    settingsFeature,
  ];

  static FeatureModule get initial => features.first;
}
