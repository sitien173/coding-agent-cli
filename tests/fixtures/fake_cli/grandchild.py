"""Start a grandchild that inherits stdout, record its PID if asked, print, and exit."""

import subprocess
import sys

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))
print("parent-done", flush=True)
