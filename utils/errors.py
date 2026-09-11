"""
Centralized, safe error normalization for API responses.

Full exception details (message + traceback) always go to the
server logs via the caller's own print()/traceback.print_exc().
Only the sanitized shape below is ever sent to the client -- never
str(exception), a file path, a subprocess error, or a provider's
raw error body.
"""


def to_client_error(exc: Exception) -> dict:
    """
    Maps an exception to a safe, user-facing error envelope:

        {"code": str, "message": str, "retryable": bool}

    The mapping is heuristic (based on exception type / message
    substrings) since exceptions from third-party libraries
    (psycopg, httpx, MCP transports, requests) aren't a closed set
    we can exhaustively catch by type.
    """
    text = str(exc).lower()
    type_name = type(exc).__name__

    if "429" in text or "rate limit" in text or "too many requests" in text:
        return {
            "code": "RATE_LIMITED",
            "message": "This information is temporarily unavailable due to high demand. Please try again shortly.",
            "retryable": True,
        }

    if isinstance(exc, FileNotFoundError) or "no such file or directory" in text:
        return {
            "code": "SERVICE_UNAVAILABLE",
            "message": "A backend service is temporarily unavailable. Please try again shortly.",
            "retryable": True,
        }

    if "timeout" in text or type_name in ("TimeoutError", "ConnectTimeout", "ReadTimeout"):
        return {
            "code": "TIMEOUT",
            "message": "This is taking longer than expected. Please try again.",
            "retryable": True,
        }

    if "connection" in text or type_name in ("ConnectionError", "ConnectionRefusedError"):
        return {
            "code": "SERVICE_UNAVAILABLE",
            "message": "We couldn't reach a required service. Please try again shortly.",
            "retryable": True,
        }

    return {
        "code": "UNEXPECTED_ERROR",
        "message": "Something went wrong on our end. Please try again.",
        "retryable": True,
    }
