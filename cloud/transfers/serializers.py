from rest_framework import serializers

from accounts.serializers import UserBriefSerializer

from .models import TransferRecord


class TransferSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    receiver = UserBriefSerializer(read_only=True)
    progress = serializers.FloatField(read_only=True)
    duration = serializers.FloatField(read_only=True)
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = TransferRecord
        fields = ["transfer_id", "job_id", "file_name", "relative_path", "file_size", "direction", "share_name",
                  "sender", "receiver", "my_role", "status", "sender_status", "receiver_status",
                  "bytes_transferred", "progress", "speed", "avg_speed", "peak_speed", "connection_type",
                  "file_hash", "error", "started_at", "updated_at", "completed_at", "duration", "folder_id"]

    def get_my_role(self, rec):
        user = self.context.get("user")
        if user is None:
            return None
        return "sender" if rec.sender_id == user.id else "receiver" if rec.receiver_id == user.id else None
