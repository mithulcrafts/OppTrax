import pinggy
import time
import sys

print("Starting Pinggy tunnel...", flush=True)
# Use start_tunnel with all config to disable web debugger and forward to 127.0.0.1:8000
tunnel = pinggy.start_tunnel(
    forwardto="127.0.0.1:8000",
    type="http",
    webdebuggerport=0
)
print("\nPinggy tunnel started! Public URLs:", flush=True)
for url in tunnel.urls:
    print(f"  - {url}", flush=True)
print("\nUse the HTTPS URL ending with .run.pinggy-free.link for WhatsApp webhook!\n", flush=True)

print("Tunnel is running—press Ctrl+C to stop.", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopping tunnel...", flush=True)
    tunnel.stop()
