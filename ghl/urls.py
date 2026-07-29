from django.urls import path

from . import views

urlpatterns = [
    path('oauth/connect/', views.connect, name='ghl-connect'),
    path('oauth/callback/', views.callback, name='ghl-callback'),
    path(
        'oauth/location-token/',
        views.LocationTokenView.as_view(),
        name='ghl-location-token',
    ),
    path('tokens/', views.GhlTokenListView.as_view(), name='ghl-tokens'),
    # Namespaced under ghl/ because "users" at the /api/ root would read as
    # this platform's own users, which are a different thing entirely.
    path(
        'ghl/users/search/',
        views.UserSearchView.as_view(),
        name='ghl-user-search',
    ),
]
