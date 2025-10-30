from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from wagtail.admin import urls as wagtailadmin_urls

from wis2watch.api import urls as api_urls

urlpatterns = [
    path("debug/django-admin/", admin.site.urls),
    path("api/", include(api_urls), name="wis2watch_api"),
    path("", include(wagtailadmin_urls)),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    
    # Serve static and media files from development server
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
