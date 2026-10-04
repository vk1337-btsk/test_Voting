from rest_framework.throttling import AnonRateThrottle

from .utils import get_client_ip


class VoteThrottle(AnonRateThrottle):
    scope = "vote"

    def get_ident(self, request):
        # Используем тот же IP, что и для fingerprint,
        # чтобы throttling и антифрод смотрели на одного клиента.
        return get_client_ip(request)
