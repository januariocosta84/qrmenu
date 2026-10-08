from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from apps.core.i18n import TRANSLATED_LANGUAGES
from apps.core.images import process_image
from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem, MenuItemOption
from apps.payments.drawer import validate_printer_host
from apps.payments.providers import STAFF_RECORDABLE_METHODS
from apps.restaurants.models import Restaurant, RestaurantStaff, Table, table_number_validator

User = get_user_model()


class TranslationFormMixin:
    """
    Adds `<field>_<lang>` inputs for each translatable field (e.g. name_tet,
    name_id) and stores them in the model's `translations` JSON on save.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model = self._meta.model
        for field in model.TRANSLATABLE_FIELDS:
            base = self.fields.get(field) or model._meta.get_field(field).formfield()
            is_text = isinstance(base.widget, forms.Textarea)
            for code, lang_name in TRANSLATED_LANGUAGES:
                name = f"{field}_{code}"
                self.fields[name] = forms.CharField(
                    label=f"{base.label} ({lang_name})",
                    required=False,
                    max_length=600 if is_text else 120,
                    widget=forms.Textarea(attrs={"rows": 2}) if is_text else forms.TextInput(),
                )
                if self.instance and self.instance.pk:
                    self.initial[name] = (self.instance.translations or {}).get(code, {}).get(field, "")

    def translation_field_names(self):
        model = self._meta.model
        return [f"{f}_{code}" for f in model.TRANSLATABLE_FIELDS for code, _name in TRANSLATED_LANGUAGES]

    def apply_translations(self, instance):
        for field in instance.TRANSLATABLE_FIELDS:
            for code, _name in TRANSLATED_LANGUAGES:
                instance.set_translation(code, field, self.cleaned_data.get(f"{field}_{code}", ""))

    def save(self, commit=True):
        instance = super().save(commit=False)
        self.apply_translations(instance)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ImageFormMixin:
    """Re-encodes newly uploaded images (see apps.core.images)."""

    IMAGE_FIELDS: dict[str, str] = {}

    def clean(self):
        cleaned = super().clean()
        for field, kind in self.IMAGE_FIELDS.items():
            data = cleaned.get(field)
            if isinstance(data, UploadedFile):
                try:
                    cleaned[field] = process_image(data, kind)
                except ValidationError as exc:
                    self.add_error(field, exc)
        return cleaned


class RestaurantForm(ImageFormMixin, TranslationFormMixin, forms.ModelForm):
    IMAGE_FIELDS = {"logo": "logo", "cover_image": "cover"}
    vat_inclusive = forms.TypedChoiceField(
        label=_("How VAT is charged"),
        choices=[(False, _("Add VAT on top of menu prices")), (True, _("Menu prices already include VAT"))],
        coerce=lambda v: v in (True, "True"), empty_value=False, required=False,
        help_text=Restaurant._meta.get_field("vat_inclusive").help_text,
    )

    class Meta:
        model = Restaurant
        fields = [
            "name", "description", "logo", "cover_image", "address", "phone", "email", "opening_hours",
            "currency", "currency_symbol", "service_charge_percent", "default_language",
            "default_prep_minutes", "is_accepting_orders",
            "cash_drawer_enabled", "printer_host", "printer_port", "drawer_pin",
            "receipt_prompt", "receipt_printer", "receipt_width", "receipt_footer",
            "vat_enabled", "vat_percent", "vat_inclusive", "vat_label", "vat_number",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "opening_hours": forms.Textarea(attrs={"rows": 3}),
            "logo": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
            "cover_image": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }
        labels = {
            "name": _("Name"), "description": _("Description"), "logo": _("Logo"), "cover_image": _("Cover image"),
            "address": _("Address"), "phone": _("Phone"), "email": _("Email"), "opening_hours": _("Opening hours"),
            "currency": _("Currency"), "currency_symbol": _("Currency symbol"),
            "service_charge_percent": _("Service charge (%)"), "default_language": _("Default menu language"),
            "default_prep_minutes": _("Default preparation time (minutes)"),
            "is_accepting_orders": _("Accepting orders"), "cash_drawer_enabled": _("Cash drawer enabled"),
            "printer_host": _("Printer IP address"), "printer_port": _("Printer port"), "drawer_pin": _("Drawer pin"),
            "receipt_prompt": _("After a payment"), "receipt_printer": _("Receipt printer"),
            "receipt_width": _("Paper width"), "receipt_footer": _("Receipt footer"),
            "vat_enabled": _("Charge VAT"), "vat_percent": _("VAT rate (%)"),
            "vat_label": _("Tax name"),
            "vat_number": _("Tax ID"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("vat_label", "vat_percent"):  # optional: blank means "VAT" / 0
            self.fields[name].required = False
        if not settings.CASH_DRAWER_NETWORK_ENABLED:  # not available on this deployment
            for name in ("cash_drawer_enabled", "printer_host", "printer_port", "drawer_pin",
                         "receipt_printer", "receipt_width"):
                self.fields.pop(name, None)

    def clean_vat_label(self):
        return (self.cleaned_data.get("vat_label") or "").strip() or "VAT"

    def clean_vat_percent(self):
        return self.cleaned_data.get("vat_percent") or 0

    def clean_printer_host(self):
        return validate_printer_host(self.cleaned_data.get("printer_host", ""))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("receipt_printer") == Restaurant.RECEIPT_NETWORK and not cleaned.get("printer_host"):
            self.add_error("printer_host", _("Enter the receipt printer's IP address to print receipts on it."))
        if cleaned.get("vat_enabled") and not (cleaned.get("vat_percent") or 0) > 0:
            self.add_error("vat_percent", _("Enter the VAT rate, e.g. 10."))
        if cleaned.get("cash_drawer_enabled") and not cleaned.get("printer_host"):
            self.add_error("printer_host", _("Enter the receipt printer's IP address to use the cash drawer."))
        return cleaned


class CategoryForm(TranslationFormMixin, forms.ModelForm):
    class Meta:
        model = MenuCategory
        fields = ["name", "position", "is_active"]
        labels = {"name": _("Name"), "position": _("Position"), "is_active": _("Visible to customers")}

    def __init__(self, *args, restaurant, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = MenuCategory.objects.filter(restaurant=self.restaurant, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("A category with this name already exists."))
        return name


class MenuItemForm(ImageFormMixin, TranslationFormMixin, forms.ModelForm):
    IMAGE_FIELDS = {"image": "item"}

    class Meta:
        model = MenuItem
        fields = ["category", "name", "description", "price", "image", "is_available", "prep_minutes", "position"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "image": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
            "price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
        }
        labels = {
            "category": _("Category"), "name": _("Name"), "description": _("Description"), "price": _("Price"),
            "image": _("Photo"), "is_available": _("Available (untick = Sold Out)"),
            "prep_minutes": _("Preparation time (minutes)"), "position": _("Position"),
        }

    def __init__(self, *args, restaurant, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)
        # Only this restaurant's categories can be chosen.
        self.fields["category"].queryset = MenuCategory.objects.filter(restaurant=restaurant)

    def save(self, commit=True):
        self.instance.restaurant = self.restaurant
        return super().save(commit)


class OptionForm(TranslationFormMixin, forms.ModelForm):
    class Meta:
        model = MenuItemOption
        fields = ["name", "price", "is_available", "position"]
        widgets = {"price": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}
        labels = {"name": _("Name"), "price": _("Price"), "is_available": _("Available"), "position": _("Position")}


OptionFormSet = forms.inlineformset_factory(
    MenuItem, MenuItemOption, form=OptionForm, extra=2, can_delete=True, max_num=30
)


class TableForm(forms.ModelForm):
    class Meta:
        model = Table
        fields = ["number", "label", "seats", "is_active"]
        labels = {"number": _("Table number"), "label": _("Label"), "seats": _("Seats"), "is_active": _("Active")}

    def __init__(self, *args, restaurant, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def clean_number(self):
        number = self.cleaned_data["number"].strip()
        qs = Table.objects.filter(restaurant=self.restaurant, number__iexact=number)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("This table number already exists."))
        return number

    def save(self, commit=True):
        self.instance.restaurant = self.restaurant
        return super().save(commit)


class BulkTableForm(forms.Form):
    start = forms.IntegerField(min_value=1, max_value=999, initial=1, label=_("From table"))
    end = forms.IntegerField(min_value=1, max_value=999, initial=10, label=_("To table"))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and (end < start or end - start >= 200):
            raise ValidationError(_("Choose a range of at most 200 tables, with end ≥ start."))
        return cleaned


class StaffCreateForm(forms.Form):
    username = forms.CharField(max_length=150, label=_("Username"))
    first_name = forms.CharField(max_length=150, required=False, label=_("Name"))
    email = forms.EmailField(required=False, label=_("Email"))
    role = forms.ChoiceField(choices=Role.CHOICES, label=_("Role"))
    password = forms.CharField(widget=forms.PasswordInput, label=_("Password"),
                               help_text=_("Share it with the staff member; they can change it later."))

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("An account with this email already exists."))
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User.username_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(_("This username is already taken."))
        return username

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password"):
            user = User(username=cleaned.get("username", ""), email=cleaned.get("email", ""))
            try:
                validate_password(cleaned["password"], user)
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class StaffEditForm(forms.ModelForm):
    class Meta:
        model = RestaurantStaff
        fields = ["role", "is_active"]
        labels = {"role": _("Role"), "is_active": _("Active")}


class PaymentForm(forms.Form):
    method = forms.ChoiceField(choices=STAFF_RECORDABLE_METHODS, label=_("Method"))
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01, label=_("Amount"))
    reference = forms.CharField(max_length=120, required=False, label=_("Reference"),
                                help_text=_("Receipt / transfer reference"))


class OrderFilterForm(forms.Form):
    q = forms.CharField(required=False, label=_("Order # / name"))
    table = forms.CharField(required=False, validators=[table_number_validator])
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
