"""Stay alive while a consumer handles the first line slowly."""

import time

print("first", flush=True)
time.sleep(0.16)
print("second", flush=True)
