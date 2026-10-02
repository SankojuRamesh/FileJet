from rest_framework import serializers

from accounts.serializers import UserBriefSerializer

from .models import TransferRecord


class TransferSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    receiver = UserBriefSerializer(read_only=True)
    progress = serializers.FloatField(read_only=True)
    duration = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()
    attempts = serializers.SerializerMethodField()
    failures = serializers.SerializerMethodField()
    first_started_at = serializers.SerializerMethodField()

    class Meta:
        model = TransferRecord
        fields = ["transfer_id", "job_id", "file_name", "relative_path", "file_size", "direction", "share_name",
                  "sender", "receiver", "my_role", "status", "sender_status", "receiver_status",
                  "bytes_transferred", "progress", "speed", "avg_speed", "peak_speed", "connection_type",
                  "file_hash", "error", "started_at", "updated_at", "completed_at", "duration", "folder_id",
                  "attempts", "failures", "first_started_at"]

    def get_duration(self, rec):
        total = getattr(rec, "total_active", None)          # every try of this file, data moving only
        return round(total, 1) if total else rec.duration

    def get_attempts(self, rec):
        return getattr(rec, "attempts", 1)

    def get_failures(self, rec):
        return getattr(rec, "failures", int(rec.status in ("failed", "cancelled")))

    def get_first_started_at(self, rec):
        v = getattr(rec, "first_started_at", None) or rec.started_at
        return serializers.DateTimeField().to_representation(v)

    def get_my_role(self, rec):
        user = self.context.get("user")
        if user is None:
            return None
        return "sender" if rec.sender_id == user.id else "receiver" if rec.receiver_id == user.id else None
