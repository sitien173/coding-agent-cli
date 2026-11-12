"""Write bytes that are not valid UTF-8."""

import sys

sys.stdout.buffer.write(b"\xffok\n")
sys.stdout.buffer.flush()
