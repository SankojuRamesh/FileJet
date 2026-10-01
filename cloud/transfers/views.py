from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    return render(request, "cloud/dashboard.html", {"active": "dashboard"})


@login_required
def transfers(request):
    return render(request, "cloud/transfers.html", {"active": "transfers"})
