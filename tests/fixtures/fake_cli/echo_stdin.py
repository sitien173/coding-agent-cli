"""Read all of stdin and report its byte length and SHA-256."""

import hashlib
import sys

data = sys.stdin.buffer.read()
print(len(data), flush=True)
print(hashlib.sha256(data).hexdigest(), flush=True)
