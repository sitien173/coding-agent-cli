"""Record a grandchild PID in the given file, announce readiness, and sleep."""

import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
print("ready", flush=True)
time.sleep(60)
