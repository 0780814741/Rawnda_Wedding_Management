from datetime import date

from django import forms

from apps.vendors.constants import CeremonyType
from apps.vendors.models import VendorCategory

from .models import Ceremony, WeddingProject


class BasicsForm(forms.ModelForm):
    class Meta:
        model = WeddingProject
        fields = ["title", "wedding_date", "district", "guest_count", "budget_rwf"]
        labels = {
            "title": "Name your wedding plan", "wedding_date": "Wedding date (or the main ceremony date)",
            "district": "District where most of the wedding will happen", "guest_count": "Expected number of guests",
            "budget_rwf": "Total budget (in RWF)",
        }
        widgets = {"wedding_date": forms.DateInput(attrs={"type": "date"}),
                   "guest_count": forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
                   "budget_rwf": forms.NumberInput(attrs={"min": 1, "step": 1000, "inputmode": "numeric"})}
        help_texts = {"budget_rwf": "Enter the full amount you plan to spend, as a number, for example 8000000.",
                      "guest_count": "Just a number. We never ask for guest names."}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in ("wedding_date", "district", "guest_count", "budget_rwf"):
            self.fields[f].required = True

    def clean_wedding_date(self):
        d = self.cleaned_data["wedding_date"]
        if d < date.today():
            raise forms.ValidationError("Please choose a date in the future.")
        return d

    def clean_guest_count(self):
        n = self.cleaned_data["guest_count"]
        if not 1 <= n <= 5000:
            raise forms.ValidationError("Please enter a guest count between 1 and 5,000.")
        return n

    def clean_budget_rwf(self):
        n = self.cleaned_data["budget_rwf"]
        if n < 100_000:
            raise forms.ValidationError("The budget looks very small. Please enter the amount in RWF (at least 100,000).")
        return n


class CeremoniesForm(forms.Form):
    ceremonies = forms.MultipleChoiceField(
        choices=CeremonyType.choices, widget=forms.CheckboxSelectMultiple,
        label="Which ceremonies are you planning?",
        error_messages={"required": "Choose at least one ceremony."},
    )

    def sync(self, project):
        chosen = set(self.cleaned_data["ceremonies"])
        project.ceremonies.exclude(ceremony_type__in=chosen).delete()
        for code in chosen:
            Ceremony.objects.get_or_create(project=project, ceremony_type=code)


class PreferencesForm(forms.Form):
    priorities = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                           label="What matters most to you? (choose up to 4)")
    preferences = forms.CharField(required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 4}),
                                  label="Anything else we should know? (optional)",
                                  help_text="For example: preferred style, religion or tradition requirements, accessibility needs. Please do not include other people's personal details.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["priorities"].choices = [(c.slug, c.name) for c in VendorCategory.objects.filter(is_active=True)]

    def clean_priorities(self):
        p = self.cleaned_data["priorities"]
        if len(p) > 4:
            raise forms.ValidationError("Please choose at most 4 priorities so the planner can focus.")
        return p
