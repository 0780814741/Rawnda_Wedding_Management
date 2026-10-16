from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.ratelimit import rate_limit

from .forms import EmailAuthenticationForm, SignUpForm
from .models import CustomerProfile


@rate_limit("signup", limit=10, window=3600)
def signup(request):
    if request.user.is_authenticated:
        return redirect("planning:dashboard")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome! Your account is ready. Let's set up your wedding.")
        return redirect("planning:new")
    return render(request, "registration/signup.html", {"form": form})


class RateLimitedLoginView(LoginView):
    authentication_form = EmailAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    @classmethod
    def as_view(cls, **initkwargs):
        return rate_limit("login", limit=20, window=900)(super().as_view(**initkwargs))


@login_required
def account(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    return render(request, "accounts/account.html", {"profile": profile})


@login_required
@require_POST
def request_deletion(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    profile.deletion_requested_at = timezone.now()
    profile.save(update_fields=["deletion_requested_at", "updated_at"])
    log_action(request.user, "deletion_requested", profile, request=request)
    messages.success(
        request,
        "We received your deletion request. Our team will confirm and remove or anonymise your data. "
        "Records we must keep for accounting (invoices, payments) are retained without personal details where possible.",
    )
    return redirect("accounts:account")
