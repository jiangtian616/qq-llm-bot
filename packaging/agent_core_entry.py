"""Nuitka 的入口：打包后的 agent-core 可执行文件从这里启动。"""

import sys

from agent_core.app.main import main

sys.exit(main())
