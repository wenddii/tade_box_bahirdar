"""
Middleware to manage thread-local request context for centralized subscription month resolution.
Ensures that any part of the request lifecycle can query the simulated month seamlessly in development.
"""
from .utils import set_current_request


class SimulationMonthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            set_current_request(None)
        return response
