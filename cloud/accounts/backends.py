from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class EmailOrUsernameBackend(ModelBackend):
    """Log in with either the username or the e-mail address (web and REST API)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or not password:
            return None
        field = "email__iexact" if "@" in username else "username__iexact"
        try:
            user = User.objects.get(**{field: username})
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            User().set_password(password)            # constant-ish time for unknown users
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
