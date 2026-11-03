"""Platform flags: the single import point for gating Linux-only code."""

import sys

IS_LINUX = sys.platform == "linux"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
