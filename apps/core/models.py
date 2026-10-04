from django.db import models

from .i18n import DEFAULT_LANGUAGE


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TranslatableMixin(models.Model):
    """
    Stores translations of text fields in a JSON column:

        {"tet": {"name": "...", "description": "..."}, "id": {...}}

    The base column (e.g. `name`) holds the default-language (English) text
    and is the fallback when a translation is missing. Adding a language
    therefore needs no schema migration.
    """

    TRANSLATABLE_FIELDS: tuple[str, ...] = ()

    translations = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    def tr(self, field: str, lang: str | None) -> str:
        if lang and lang != DEFAULT_LANGUAGE:
            value = (self.translations or {}).get(lang, {}).get(field)
            if value:
                return value
        return getattr(self, field, "") or ""

    def set_translation(self, lang: str, field: str, value: str) -> None:
        data = dict(self.translations or {})
        lang_data = dict(data.get(lang, {}))
        value = (value or "").strip()
        if value:
            lang_data[field] = value
        else:
            lang_data.pop(field, None)
        if lang_data:
            data[lang] = lang_data
        else:
            data.pop(lang, None)
        self.translations = data
