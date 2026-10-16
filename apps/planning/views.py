from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.audit import log_action
from apps.core.ratelimit import rate_limit
from apps.operations import services as ops
from apps.operations.models import InvalidTransition, Quotation
from apps.payments.models import Invoice
from apps.payments.providers import get_provider

from . import services
from .forms import BasicsForm, CeremoniesForm, PreferencesForm
from .models import WeddingProject


def get_project(request, pk):
    """Every customer view goes through this: another customer's project is simply a 404."""
    return get_object_or_404(WeddingProject, pk=pk, customer=request.user)


def _editable(request, project):
    if project.is_locked:
        messages.info(request, "Your request is with our team, so the setup is locked. Contact us if something changed.")
        return False
    return True


@login_required
def dashboard(request):
    projects = request.user.wedding_projects.prefetch_related("ceremonies")
    return render(request, "planning/dashboard.html", {"projects": projects})


@login_required
def new_project(request):
    form = BasicsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False)
        project.customer, project.setup_step = request.user, 2
        project.save()
        log_action(request.user, "project_created", project, request=request)
        return redirect("planning:setup_ceremonies", pk=project.pk)
    return render(request, "planning/setup_basics.html", {"form": form, "step": 1, "project": None})


@login_required
def setup_basics(request, pk):
    project = get_project(request, pk)
    if not _editable(request, project):
        return redirect("planning:project_dashboard", pk=pk)
    form = BasicsForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("planning:setup_ceremonies", pk=pk)
    return render(request, "planning/setup_basics.html", {"form": form, "step": 1, "project": project})


@login_required
def setup_ceremonies(request, pk):
    project = get_project(request, pk)
    if not _editable(request, project):
        return redirect("planning:project_dashboard", pk=pk)
    initial = {"ceremonies": list(project.ceremonies.values_list("ceremony_type", flat=True))}
    form = CeremoniesForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        form.sync(project)
        project.setup_step = max(project.setup_step, 3)
        project.save(update_fields=["setup_step", "updated_at"])
        return redirect("planning:setup_preferences", pk=pk)
    return render(request, "planning/setup_ceremonies.html", {"form": form, "step": 2, "project": project})


@login_required
def setup_preferences(request, pk):
    project = get_project(request, pk)
    if not _editable(request, project):
        return redirect("planning:project_dashboard", pk=pk)
    if not project.ceremonies.exists():
        messages.info(request, "Please choose your ceremonies first.")
        return redirect("planning:setup_ceremonies", pk=pk)
    form = PreferencesForm(request.POST or None, initial={"priorities": project.priorities, "preferences": project.preferences})
    if request.method == "POST" and form.is_valid():
        project.priorities = form.cleaned_data["priorities"]
        project.preferences = form.cleaned_data["preferences"]
        project.setup_step = 4
        project.save(update_fields=["priorities", "preferences", "setup_step", "updated_at"])
        services.sync_required_services(project)
        return redirect("planning:plan", pk=pk)
    return render(request, "planning/setup_preferences.html", {"form": form, "step": 3, "project": project})


@login_required
def plan(request, pk):
    project = get_project(request, pk)
    if not project.basics_complete or not project.ceremonies.exists():
        messages.info(request, "Let's finish the setup first.")
        return redirect("planning:setup_basics", pk=pk)
    services.sync_required_services(project)
    options = services.generate_options(project) if not project.is_locked else []
    totals = services.project_totals(project)
    items = project.plan_items.select_related("ceremony", "category")
    grouped = {}
    for it in items:
        grouped.setdefault(it.ceremony, []).append(it)
    required = {c: list(c.required_services.select_related("category")) for c in project.ceremonies.all()}
    return render(request, "planning/plan.html", {
        "project": project, "options": options, "totals": totals, "grouped": grouped, "required": required,
        "has_items": bool(items), "contingency_percent": project.contingency_bps / 100, "fee_percent": project.fee_bps / 100,
        "overall_over": totals["remaining"] is not None and totals["remaining"] < 0 and bool(items),
    })


@login_required
@require_POST
def apply_option(request, pk):
    project = get_project(request, pk)
    if project.is_locked:
        messages.error(request, "This plan is already with our team and can't be changed here.")
        return redirect("planning:project_dashboard", pk=pk)
    strategy = request.POST.get("strategy", "")
    option = next((o for o in services.generate_options(project) if o.strategy == strategy), None)
    if option is None:
        messages.error(request, "That option is no longer available. Please pick another.")
        return redirect("planning:plan", pk=pk)
    n = services.apply_option(project, option)
    messages.success(request, f"Plan updated with {n} services. All prices shown are indicative until our team confirms them with each vendor.")
    return redirect("planning:plan", pk=pk)


class RequestForm(forms.Form):
    kind = forms.ChoiceField(choices=[("full_plan", "Prepare my complete plan and a quotation"),
                                      ("consultation", "I'd like a planning consultation first")], widget=forms.RadioSelect, initial="full_plan",
                             label="What would you like from us?")
    contact_phone = forms.CharField(max_length=30, label="Phone / WhatsApp number")
    preferred_contact = forms.ChoiceField(choices=[("whatsapp", "WhatsApp"), ("phone", "Phone call"), ("email", "Email")], label="Best way to reach you")
    message = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}), label="Anything we should know? (optional)")
    consent = forms.BooleanField(
        label="I agree that the planning team may contact vendors on my behalf, sharing only my wedding date, place, guest count and service needs (not my personal contact details).",
        error_messages={"required": "We need your agreement to contact vendors for you."})


@login_required
@rate_limit("request", limit=10, window=3600)
def request_plan(request, pk):
    project = get_project(request, pk)
    if project.is_locked:
        messages.info(request, "You already sent a request for this wedding.")
        return redirect("planning:project_dashboard", pk=pk)
    phone = getattr(getattr(request.user, "profile", None), "phone", "")
    form = RequestForm(request.POST or None, initial={"contact_phone": phone})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            req = ops.create_request(project, request.user, kind=cd["kind"], message=cd["message"], phone=cd["contact_phone"],
                                     preferred_contact=cd["preferred_contact"], consent=cd["consent"])
        except ValidationError as e:
            form.add_error(None, e.messages[0])
        else:
            if settings.OPERATOR_EMAIL:
                send_mail(f"New wedding plan request #{req.pk}", f"A customer submitted request #{req.pk}. Open the operations dashboard to review it.",
                          settings.DEFAULT_FROM_EMAIL, [settings.OPERATOR_EMAIL], fail_silently=True)
            messages.success(request, "Thank you! Your request was sent to our planning team. We will contact you and keep this page updated.")
            return redirect("planning:project_dashboard", pk=pk)
    return render(request, "planning/request_plan.html", {"form": form, "project": project, "totals": services.project_totals(project)})


@login_required
def project_dashboard(request, pk):
    project = get_project(request, pk)
    visible = [Quotation.Status.SENT, Quotation.Status.ACCEPTED, Quotation.Status.DECLINED, Quotation.Status.EXPIRED]
    quotation = project.quotations.filter(status__in=visible).prefetch_related("lines__ceremony", "lines__category").first()
    invoices = project.invoices.exclude(status=Invoice.Status.DRAFT).prefetch_related("payments")
    for inv in invoices:
        inv.instructions = get_provider().get_instructions(inv).text
    bookings = project.bookings.all()
    next_step = {
        "draft": "Finish your plan and send it to our team when you're ready.",
        "requested": "Our planning team is reviewing your request. We will contact vendors for you; you do not need to negotiate with them.",
        "in_progress": "We are checking availability and prices with vendors.",
        "offer_sent": "Your offer is ready. Please review it and accept or decline before it expires.",
        "accepted": "Offer accepted. We are asking each vendor to confirm. A booking is only confirmed when the vendor confirms it.",
        "cancelled": "This plan was cancelled.",
    }[project.status]
    return render(request, "planning/project_dashboard.html", {
        "project": project, "quotation": quotation, "invoices": invoices, "bookings": bookings, "next_step": next_step,
        "totals": services.project_totals(project),
    })


@login_required
@require_POST
def quotation_respond(request, pk, qpk):
    project = get_project(request, pk)
    q = get_object_or_404(Quotation, pk=qpk, project=project)
    try:
        if request.POST.get("action") == "accept":
            ops.accept_quotation(q, request.user)
            messages.success(request, "Offer accepted. Our team will now ask each vendor to confirm. Nothing is confirmed until a vendor confirms it.")
        elif request.POST.get("action") == "decline":
            ops.decline_quotation(q, request.user)
            messages.info(request, "Offer declined. Our team will be in touch about changes.")
    except InvalidTransition as e:
        messages.error(request, str(e))
    return redirect("planning:project_dashboard", pk=pk)


@login_required
def notifications(request):
    items = request.user.notifications.all()[:50]
    if request.method == "POST":
        request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
        return redirect("planning:notifications")
    return render(request, "planning/notifications.html", {"items": items})
