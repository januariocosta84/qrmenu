from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

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
        return [f"{f}_{code}" for f in model.TRANSLATABLE_FIELDS for code, _ in TRANSLATED_LANGUAGES]

    def apply_translations(self, instance):
        for field in instance.TRANSLATABLE_FIELDS:
            for code, _ in TRANSLATED_LANGUAGES:
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

    class Meta:
        model = Restaurant
        fields = [
            "name", "description", "logo", "cover_image", "address", "phone", "email", "opening_hours",
            "currency", "currency_symbol", "service_charge_percent", "default_language",
            "default_prep_minutes", "is_accepting_orders",
            "cash_drawer_enabled", "printer_host", "printer_port", "drawer_pin",
            "receipt_prompt", "receipt_printer", "receipt_width", "receipt_footer",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "opening_hours": forms.Textarea(attrs={"rows": 3}),
            "logo": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
            "cover_image": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not settings.CASH_DRAWER_NETWORK_ENABLED:  # not available on this deployment
            for name in ("cash_drawer_enabled", "printer_host", "printer_port", "drawer_pin",
                         "receipt_printer", "receipt_width"):
                self.fields.pop(name, None)

    def clean_printer_host(self):
        return validate_printer_host(self.cleaned_data.get("printer_host", ""))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("receipt_printer") == Restaurant.RECEIPT_NETWORK and not cleaned.get("printer_host"):
            self.add_error("printer_host", "Enter the receipt printer's IP address to print receipts on it.")
        if cleaned.get("cash_drawer_enabled") and not cleaned.get("printer_host"):
            self.add_error("printer_host", "Enter the receipt printer's IP address to use the cash drawer.")
        return cleaned


class CategoryForm(TranslationFormMixin, forms.ModelForm):
    class Meta:
        model = MenuCategory
        fields = ["name", "position", "is_active"]

    def __init__(self, *args, restaurant, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = MenuCategory.objects.filter(restaurant=self.restaurant, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("A category with this name already exists.")
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
        labels = {"is_available": "Available (untick = Sold Out)"}

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


OptionFormSet = forms.inlineformset_factory(
    MenuItem, MenuItemOption, form=OptionForm, extra=2, can_delete=True, max_num=30
)


class TableForm(forms.ModelForm):
    class Meta:
        model = Table
        fields = ["number", "label", "seats", "is_active"]

    def __init__(self, *args, restaurant, **kwargs):
        self.restaurant = restaurant
        super().__init__(*args, **kwargs)

    def clean_number(self):
        number = self.cleaned_data["number"].strip()
        qs = Table.objects.filter(restaurant=self.restaurant, number__iexact=number)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("This table number already exists.")
        return number

    def save(self, commit=True):
        self.instance.restaurant = self.restaurant
        return super().save(commit)


class BulkTableForm(forms.Form):
    start = forms.IntegerField(min_value=1, max_value=999, initial=1)
    end = forms.IntegerField(min_value=1, max_value=999, initial=10)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and (end < start or end - start >= 200):
            raise ValidationError("Choose a range of at most 200 tables, with end ≥ start.")
        return cleaned


class StaffCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False, label="Name")
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(choices=Role.CHOICES)
    password = forms.CharField(widget=forms.PasswordInput, help_text="Share it with the staff member; they can change it later.")

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User.username_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("This username is already taken.")
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


class PaymentForm(forms.Form):
    method = forms.ChoiceField(choices=STAFF_RECORDABLE_METHODS)
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    reference = forms.CharField(max_length=120, required=False, help_text="Receipt / transfer reference")


class OrderFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Order # / name")
    table = forms.CharField(required=False, validators=[table_number_validator])
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
