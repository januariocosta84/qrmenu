"""
Amount in words for quotations ("One hundred twenty US dollars and fifty cents").

English, Portuguese and Indonesian. Tetun documents use the Portuguese numbers,
as is usual in formal Tetun writing.
"""
from decimal import Decimal

# ---------------------------------------------------------------- English
_EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
            "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _en_below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_EN_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        parts.append(_EN_TENS[n // 10] + (f"-{_EN_ONES[n % 10]}" if n % 10 else ""))
    elif n or not parts:
        parts.append(_EN_ONES[n])
    return " ".join(parts)


def en_number(n: int) -> str:
    if n == 0:
        return "zero"
    out = []
    for value, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand"), (1, "")):
        if n >= value:
            out.append((_en_below_1000(n // value) + (f" {name}" if name else "")).strip())
            n %= value
    return " ".join(out)


# ---------------------------------------------------------------- Portuguese
_PT_ONES = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze",
            "treze", "catorze", "quinze", "dezasseis", "dezassete", "dezoito", "dezanove"]
_PT_TENS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
_PT_HUNDREDS = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos",
                "oitocentos", "novecentos"]


def _pt_below_1000(n: int) -> str:
    if n == 100:
        return "cem"
    parts = []
    if n >= 100:
        parts.append(_PT_HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        parts.append(_PT_TENS[n // 10] + (f" e {_PT_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_PT_ONES[n])
    return " e ".join(parts) if parts else "zero"


def pt_number(n: int) -> str:
    if n == 0:
        return "zero"
    groups = []
    for value, one, many in ((10**9, "mil milhões", "mil milhões"), (10**6, "um milhão", "milhões"), (1000, "mil", "mil"), (1, "", "")):
        if n >= value:
            q = n // value
            n %= value
            if value == 1:
                groups.append(_pt_below_1000(q))
            elif value == 1000:
                groups.append("mil" if q == 1 else f"{_pt_below_1000(q)} mil")
            else:
                groups.append(one if q == 1 else f"{_pt_below_1000(q)} {many}")
    # "e" before the last group when it is < 100 or a round hundred (mil e cem, mil e vinte).
    text = groups[0]
    for g in groups[1:]:
        last_small = g in _PT_ONES or g == "cem" or g in _PT_HUNDREDS or " e " not in g and g.split()[0] in _PT_TENS
        text += (" e " if last_small else " ") + g
    return text


# ---------------------------------------------------------------- Indonesian
_ID_ONES = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


def _id_below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append("seratus" if n // 100 == 1 else f"{_ID_ONES[n // 100]} ratus")
        n %= 100
    if n >= 20:
        parts.append(f"{_ID_ONES[n // 10]} puluh" + (f" {_ID_ONES[n % 10]}" if n % 10 else ""))
    elif n >= 12:
        parts.append(f"{_ID_ONES[n % 10]} belas")
    elif n:
        parts.append(_ID_ONES[n])
    return " ".join(parts) if parts else "nol"


def id_number(n: int) -> str:
    if n == 0:
        return "nol"
    out = []
    for value, name in ((10**9, "miliar"), (10**6, "juta"), (1000, "ribu"), (1, "")):
        if n >= value:
            q = n // value
            n %= value
            if value == 1000 and q == 1:
                out.append("seribu")
            else:
                out.append((_id_below_1000(q) + (f" {name}" if name else "")).strip())
    return " ".join(out)


# ---------------------------------------------------------------- public
CURRENCY_WORDS = {
    "USD": {"en": ("US dollar", "US dollars", "cent", "cents"), "pt": ("dólar americano", "dólares americanos", "cêntimo", "cêntimos"),
            "id": ("dolar AS", "dolar AS", "sen", "sen")},
}


def amount_in_words(amount: Decimal, lang: str, currency: str = "USD") -> str:
    lang = {"tet": "pt"}.get(lang, lang)
    if lang not in ("en", "pt", "id"):
        lang = "en"
    amount = Decimal(amount).quantize(Decimal("0.01"))
    whole, cents = int(amount), int((amount - int(amount)) * 100)
    number = {"en": en_number, "pt": pt_number, "id": id_number}[lang]
    names = CURRENCY_WORDS.get(currency, {}).get(lang)
    if names is None:  # unknown currency: number words + code
        names = (currency, currency, "/100", "/100")
    one, many, cent_one, cent_many = names
    joiner = {"en": " and ", "pt": " e ", "id": " dan "}[lang]
    text = f"{number(whole)} {one if whole == 1 else many}"
    if cents:
        text += f"{joiner}{number(cents)} {cent_one if cents == 1 else cent_many}"
    return text[0].upper() + text[1:]
