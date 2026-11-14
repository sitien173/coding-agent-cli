"""Write enough output to fill both OS pipes unless both are drained."""

import sys

for _ in range(512):
    sys.stdout.write("o" * 8191 + "\n")
    sys.stderr.write("e" * 8191 + "\n")
    if _ % 16 == 15:
        sys.stdout.flush()
        sys.stderr.flush()
sys.stdout.flush()
sys.stderr.flush()
