import 'package:get/get.dart';

import 'sessions_repository.dart';

class SessionsController extends GetxController {
  SessionsController(this.repository);

  final SessionsRepository repository;
}
