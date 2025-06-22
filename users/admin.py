from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from rest_framework.authtoken.models import Token

from users.forms import CustomUserChangeForm, CustomUserCreationForm
from users.models import Code
from users.models import CustomUser


class TokenInline(admin.TabularInline):
    model = Token
    classes = ('grp-collapse grp-closed',)


class CodeInline(admin.TabularInline):
    model = Code
    classes = ('grp-collapse grp-closed',)


class CustomUserAdmin(UserAdmin):
    inlines = [TokenInline, CodeInline]
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser

    def full_name(self, obj):
        return obj.get_full_name()

    full_name.short_description = 'Name'
    list_display = ('full_name', 'email', 'is_active')
    list_filter = ['is_active']
    fieldsets = (
        (None, {'fields': (
        'email', 'first_name', 'last_name', 'password', 'status', 'muted', 'blocked', 'following')}),
        ('Date information', {'fields': ['last_login', 'date_joined'], 'classes': ('grp-collapse grp-closed',), }),
        ('Permissions',
         {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
          'classes': ('grp-collapse grp-closed',), }),
    )
    add_fieldsets = (
        (None, {'fields': ('email', 'first_name', 'last_name', 'password1', 'password2',)}),
    )
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('first_name', 'email',)
    readonly_fields = ('last_login', 'date_joined',)
    filter_horizontal = ['following']


admin.site.register(CustomUser, CustomUserAdmin)
