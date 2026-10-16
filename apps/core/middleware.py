import logging
import time

logger = logging.getLogger("request")


class RequestContextMiddleware:
    """Logs method/path/status/duration only. Never logs bodies, cookies or query strings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        if request.path != "/healthz":
            logger.info(
                "method=%s path=%s status=%s ms=%d",
                request.method, request.path, response.status_code, (time.monotonic() - start) * 1000,
            )
        return response
