"""Emit output on alternating pipes, then exit."""

import sys
import time

time.sleep(0.15)
sys.stderr.write("stderr-activity\n")
sys.stderr.flush()
time.sleep(0.5)
sys.stdout.write("stdout-activity\n")
sys.stdout.flush()
