from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from billing.services import get_subscription, usage_summary

from .models import Device, User


class RegisterSerializer(serializers.Serializer):
    username = serializers.RegexField(r"^[A-Za-z0-9_.-]{3,30}$", error_messages={
        "invalid": "3-30 characters: letters, digits, . _ -"})
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    display_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    # asked by the sign-up forms; optional here so apps older than 2.10 can still register
    organization = serializers.CharField(max_length=120, required=False, allow_blank=True)
    location = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate_username(self, v):
        if User.objects.filter(username__iexact=v).exists():
            raise serializers.ValidationError("username already taken")
        return v

    def validate_email(self, v):
        if User.objects.filter(email__iexact=v).exists():
            raise serializers.ValidationError("an account with this e-mail already exists")
        return v.lower()

    def validate(self, data):
        validate_password(data["password"], User(username=data["username"], email=data["email"]))
        return data

    def create(self, data):
        user = User.objects.create_user(username=data["username"], email=data["email"], password=data["password"],
                                        display_name=data.get("display_name", ""),
                                        organization=data.get("organization", "").strip(),
                                        location=data.get("location", "").strip())
        get_subscription(user)
        return user


class LoginSerializer(TokenObtainPairSerializer):
    """The username field accepts a username or an e-mail (EmailOrUsernameBackend)."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["uid"] = user.public_id
        return token


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "name", "platform", "app", "fingerprint", "created_at", "last_seen_at", "revoked"]


class PublicDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["name", "fingerprint", "cert_pem", "app"]


class MeSerializer(serializers.ModelSerializer):
    subscription = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    platform_admin = serializers.BooleanField(source="is_superuser", read_only=True)   # web admin panel only

    class Meta:
        model = User
        fields = ["id", "username", "email", "display_name", "organization", "location", "phone", "public_id",
                  "date_joined", "subscription", "usage", "platform_admin"]
        read_only_fields = ["id", "username", "public_id", "date_joined"]

    def validate_phone(self, v):
        from .models import normalize_phone
        try:
            return normalize_phone(v)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None

    def validate_email(self, v):
        if User.objects.filter(email__iexact=v).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("e-mail already in use")
        return v.lower()

    def get_subscription(self, user):
        from billing.serializers import SubscriptionSerializer
        return SubscriptionSerializer(get_subscription(user)).data

    def get_usage(self, user):
        return usage_summary(user)


class PeerSerializer(serializers.ModelSerializer):
    """A linked user (admin <-> user) with their device certificates, for pinning and encryption."""
    devices = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["public_id", "username", "display_name", "email", "organization", "devices"]

    def get_devices(self, user):
        return PublicDeviceSerializer(user.devices.filter(revoked=False), many=True).data


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["public_id", "username", "display_name"]
