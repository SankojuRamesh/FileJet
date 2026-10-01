from rest_framework import serializers

from .models import Plan, Subscription


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["code", "name", "price_month", "currency", "max_file_size", "monthly_quota", "max_users",
                  "max_folders", "description"]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = ["plan", "status", "current_period_start", "current_period_end", "cancel_at_period_end",
                  "provider"]
