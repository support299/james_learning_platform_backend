from django.contrib import admin

from .models import GhlToken, GhlUser


@admin.register(GhlUser)
class GhlUserAdmin(admin.ModelAdmin):
    list_display = ['ghl_id', 'name', 'email', 'role', 'student']
    search_fields = ['ghl_id', 'name', 'email']
    raw_id_fields = ['student']
