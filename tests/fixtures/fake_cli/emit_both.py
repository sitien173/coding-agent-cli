"""Print one stdout line and one stderr line, then exit 0."""

import sys

print("out-line", flush=True)
print("err-line", file=sys.stderr, flush=True)
