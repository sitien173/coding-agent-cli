"""Start a SIGTERM-resistant child that inherits the output pipes, then exit."""

import subprocess
import sys

child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(5)",
    ]
)
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
print("parent-done", flush=True)
