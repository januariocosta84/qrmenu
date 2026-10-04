from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Owners sign in with their email; staff created by owners use a username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or password is None:
            return None
        User = get_user_model()
        login = username.strip()
        user = None
        if "@" in login:
            user = User.objects.filter(email__iexact=login).order_by("id").first()
        if user is None:
            user = User.objects.filter(username__iexact=login).first()
        if user is None:
            User().set_password(password)  # same timing as a real check
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
