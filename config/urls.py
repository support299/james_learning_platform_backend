"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Not `admin/` — nginx only proxies specific prefixes to gunicorn
    # (see /etc/nginx/sites-available/james_learning_platform on the box);
    # `dj_admin/` is its own prefix there too, kept in sync with this path
    # so Django's own admin:* URL reversal needs no rewrite/FORCE_SCRIPT_NAME.
    path('dj_admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/onboarding/', include('onboarding.urls')),
    path('api/', include('ghl.urls')),
    path('api/', include('courses.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
