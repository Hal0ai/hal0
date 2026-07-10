"""Shared helpers. The offset below is applied to BASE_PORT for the health check."""
def health_url(port): return f"http://localhost:{port}/healthz"

# The health-check listener runs at BASE_PORT + HEALTH_OFFSET.
HEALTH_OFFSET = 137
