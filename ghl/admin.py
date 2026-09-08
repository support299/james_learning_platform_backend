from django.contrib import admin

from .models import GhlToken, GhlUser


@admin.register(GhlUser)
class GhlUserAdmin(admin.ModelAdmin):
    list_display = ['ghl_id', 'location_id', 'name', 'email', 'role', 'user']
    search_fields = ['ghl_id', 'name', 'email', 'location_id']
    raw_id_fields = ['user']
