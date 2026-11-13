
import csv
import math
import time
from datetime import datetime, timezone
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

URL = "http://localhost:8080/health/ready"
SAMPLES = 200

results = []

for i in range(SAMPLES):
    timestamp = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()

    try:
        with urlopen(URL, timeout=5) as response:
            status = response.status
            response.read()
    except HTTPError as e:
        status = e.code
    except (URLError, TimeoutError) as e:
        status = "ERROR"

    duration_ms = (time.perf_counter() - start) * 1000

    results.append({
        "sample": i + 1,
        "timestamp": timestamp,
        "status": status,
        "duration_ms": round(duration_ms, 3)
    })

    time.sleep(0.05)

with open("docs/http_samples.csv", "w", newline="") as f:
    writer = csv.DictWriter(
        f, fieldnames=["sample", "timestamp", "status", "duration_ms"]
    )
    writer.writeheader()
    writer.writerows(results)

latencies = sorted(
    r["duration_ms"] for r in results
    if r["status"] == 200
)

if latencies:
    p95_index = math.ceil(0.95 * len(latencies)) - 1
    p95 = latencies[p95_index]

    print(f"Total samples: {len(results)}")
    print(f"Successful HTTP 200: {len(latencies)}")
    print(f"Failed requests: {len(results) - len(latencies)}")
    print(f"p95 latency: {p95:.3f} ms")
else:
    print("No successful HTTP 200 responses. Check API availability.")
