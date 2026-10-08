"""
Django's own strings that staff see on the dashboard (form errors, dates,
"5 minutes ago"). Django ships Portuguese and Indonesian translations for
these but no Tetun, so they are listed here for `makemessages` to pick up and
are translated only in locale/tet. Leave them untranslated in pt/id so
Django's own catalog is used there.

This module is never imported; it only feeds the message extractor.
"""
from django.utils.translation import gettext_noop, ngettext_lazy, pgettext_lazy

STRINGS = [
    # Form validation
    gettext_noop("This field is required."),
    gettext_noop("Enter a valid email address."),
    gettext_noop("Enter a whole number."),
    gettext_noop("Enter a number."),
    gettext_noop("Enter a valid value."),
    gettext_noop("Enter a valid date."),
    gettext_noop("Ensure this value is greater than or equal to %(limit_value)s."),
    gettext_noop("Ensure this value is less than or equal to %(limit_value)s."),
    gettext_noop("Select a valid choice. %(value)s is not one of the available choices."),
    gettext_noop("Select a valid choice. That choice is not one of the available choices."),
    gettext_noop("Upload a valid image. The file you uploaded was either not an image or a corrupted image."),
    gettext_noop("Currently"),
    gettext_noop("Change"),
    gettext_noop("Clear"),
    gettext_noop("Yes"),
    gettext_noop("No"),
    gettext_noop("Unknown"),
    # Passwords
    gettext_noop("Your old password was entered incorrectly. Please enter it again."),
    gettext_noop("Old password"),
    gettext_noop("New password"),
    gettext_noop("New password confirmation"),
    gettext_noop("The two password fields didn’t match."),
    gettext_noop("This password is too common."),
    gettext_noop("This password is entirely numeric."),
    gettext_noop("The password is too similar to the %(verbose_name)s."),
    ngettext_lazy(
        "This password is too short. It must contain at least %(min_length)d character.",
        "This password is too short. It must contain at least %(min_length)d characters.",
        "min_length",
    ),
    # timesince ("5 minutes ago")
    ngettext_lazy("%(num)d year", "%(num)d years", "num"),
    ngettext_lazy("%(num)d month", "%(num)d months", "num"),
    ngettext_lazy("%(num)d week", "%(num)d weeks", "num"),
    ngettext_lazy("%(num)d day", "%(num)d days", "num"),
    ngettext_lazy("%(num)d hour", "%(num)d hours", "num"),
    ngettext_lazy("%(num)d minute", "%(num)d minutes", "num"),
    # Dates
    gettext_noop("Monday"), gettext_noop("Tuesday"), gettext_noop("Wednesday"), gettext_noop("Thursday"),
    gettext_noop("Friday"), gettext_noop("Saturday"), gettext_noop("Sunday"),
    gettext_noop("Mon"), gettext_noop("Tue"), gettext_noop("Wed"), gettext_noop("Thu"),
    gettext_noop("Fri"), gettext_noop("Sat"), gettext_noop("Sun"),
    gettext_noop("January"), gettext_noop("February"), gettext_noop("March"), gettext_noop("April"),
    gettext_noop("May"), gettext_noop("June"), gettext_noop("July"), gettext_noop("August"),
    gettext_noop("September"), gettext_noop("October"), gettext_noop("November"), gettext_noop("December"),
    gettext_noop("jan"), gettext_noop("feb"), gettext_noop("mar"), gettext_noop("apr"),
    gettext_noop("may"), gettext_noop("jun"), gettext_noop("jul"), gettext_noop("aug"),
    gettext_noop("sep"), gettext_noop("oct"), gettext_noop("nov"), gettext_noop("dec"),
    pgettext_lazy("alt. month", "January"), pgettext_lazy("alt. month", "February"),
    pgettext_lazy("alt. month", "March"), pgettext_lazy("alt. month", "April"),
    pgettext_lazy("alt. month", "May"), pgettext_lazy("alt. month", "June"),
    pgettext_lazy("alt. month", "July"), pgettext_lazy("alt. month", "August"),
    pgettext_lazy("alt. month", "September"), pgettext_lazy("alt. month", "October"),
    pgettext_lazy("alt. month", "November"), pgettext_lazy("alt. month", "December"),
]
