"""Print one line longer than 1 MiB."""

import sys

sys.stdout.write("y" * (2 * 1024 * 1024) + "\n")
sys.stdout.flush()
