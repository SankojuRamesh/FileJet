"""Web pages: login, register, account, devices."""
from django import forms
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .models import Device, User


class RegisterForm(forms.Form):
    username = forms.RegexField(r"^[A-Za-z0-9_.-]{3,30}$", max_length=30,
                                error_messages={"invalid": "3-30 characters: letters, digits, . _ -"})
    email = forms.EmailField()
    display_name = forms.CharField(max_length=80, required=False)
    password = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Repeat password")

    def clean_username(self):
        v = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=v).exists():
            raise forms.ValidationError("username already taken")
        return v

    def clean_email(self):
        v = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=v).exists():
            raise forms.ValidationError("an account with this e-mail already exists")
        return v

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password2"):
            raise forms.ValidationError("passwords do not match")
        if data.get("password"):
            validate_password(data["password"], User(username=data.get("username"), email=data.get("email")))
        return data


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["display_name", "organization", "email"]


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from billing.services import get_subscription
        d = form.cleaned_data
        user = User.objects.create_user(username=d["username"], email=d["email"], password=d["password"],
                                        display_name=d.get("display_name", ""))
        get_subscription(user)
        login(request, user, backend="accounts.backends.EmailOrUsernameBackend")
        messages.success(request, f"Welcome, {user.label}! Your ID is {user.formatted_id}.")
        return redirect("dashboard")
    return render(request, "cloud/register.html", {"form": form})


@login_required
def account(request):
    profile = ProfileForm(request.POST if request.POST.get("form") == "profile" else None, instance=request.user)
    pw = PasswordChangeForm(request.user, request.POST if request.POST.get("form") == "password" else None)
    if request.method == "POST":
        if request.POST.get("form") == "profile" and profile.is_valid():
            profile.save()
            messages.success(request, "Profile saved.")
            return redirect("account")
        if request.POST.get("form") == "password" and pw.is_valid():
            user = pw.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Password changed.")
            return redirect("account")
    return render(request, "cloud/account.html", {"profile": profile, "pw": pw, "active": "account"})


@login_required
def devices(request):
    return render(request, "cloud/devices.html", {"devices": request.user.devices.all(), "active": "devices"})


@login_required
@require_POST
def device_revoke(request, pk):
    d = get_object_or_404(Device, pk=pk, user=request.user)
    d.revoked = True
    d.save(update_fields=["revoked"])
    messages.success(request, f"{d.name} revoked. It must log in again to be used.")
    return redirect("devices")
