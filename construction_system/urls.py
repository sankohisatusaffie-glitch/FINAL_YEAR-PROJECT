from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("coordination.urls")),
]

handler403 = "django.views.defaults.permission_denied"
handler404 = "django.views.defaults.page_not_found"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
