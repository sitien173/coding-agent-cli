"""Print N numbered lines, where N is the first argument."""

import sys

for index in range(int(sys.argv[1])):
    print(f"line-{index:06d}-" + "x" * 40, flush=True)
