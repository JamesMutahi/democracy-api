def get_current_user(context):
    scope = context.get('scope') or {}
    user = scope.get('user')

    if user and user.is_authenticated:
        return user

    request = context.get('request')
    if request and getattr(request, 'user', None):
        user = request.user
        if user.is_authenticated:
            return user

    return None