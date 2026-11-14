"""Write an oversized line followed by a valid line."""

import sys

sys.stdout.write("x" * 100_001 + "\nvalid-after-oversized\n")
sys.stdout.flush()
