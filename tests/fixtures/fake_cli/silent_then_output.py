"""Wait silently before emitting one line."""

import sys
import time

time.sleep(float(sys.argv[1]))
print("delayed-output", flush=True)
