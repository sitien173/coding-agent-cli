"""Print a line and exit with status 3."""

import sys

print("failing", flush=True)
sys.exit(3)
