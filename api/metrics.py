from prometheus_client import Counter, Histogram

orders_created = Counter("fulfillops_orders_created_total", "Orders accepted by the API")
orders_failed = Counter("fulfillops_orders_failed_total", "Orders that failed processing")
worker_retries = Counter("fulfillops_worker_retries_total", "Worker retry attempts")
http_requests = Counter(
    "fulfillops_http_requests_total", "HTTP requests received", ["method", "path", "status"]
)
http_latency = Histogram(
    "fulfillops_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)
worker_completed = Counter(
    "fulfillops_worker_completed_total", "Orders completed by the worker"
)
worker_dead_letter = Counter(
    "fulfillops_worker_dead_letter_total", "Orders moved to dead-letter state"
)
