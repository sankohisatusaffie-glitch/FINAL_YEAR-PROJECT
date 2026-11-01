import time

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponseRedirect
from django.urls import reverse


class SessionSecurityMiddleware:
    """Expire signed-in sessions after inactivity or a fixed maximum lifetime."""

    started_key = "_buildlink_session_started_at"
    activity_key = "_buildlink_last_activity_at"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        was_authenticated = bool(user and user.is_authenticated)

        if was_authenticated:
            now = time.time()
            session = request.session
            started_at = session.get(self.started_key, now)
            last_activity_at = session.get(self.activity_key, started_at)

            if (
                now - last_activity_at >= settings.SESSION_IDLE_TIMEOUT_SECONDS
                or now - started_at >= settings.SESSION_MAX_AGE_SECONDS
            ):
                logout(request)
                response = HttpResponseRedirect(f"{reverse('login')}?expired=1")
            else:
                session[self.started_key] = started_at
                session[self.activity_key] = now
                response = self.get_response(request)
        else:
            response = self.get_response(request)

        if was_authenticated:
            response["Cache-Control"] = "private, no-store, max-age=0"
            response["Pragma"] = "no-cache"
        return response
