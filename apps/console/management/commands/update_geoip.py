"""
Download the free DB-IP "IP to Country Lite" database (CC BY 4.0, https://db-ip.com) and
build the small lookup file used for visitor countries (settings.GEOIP_DB).

    python manage.py update_geoip            # this month's file (falls back to last month)
    python manage.py update_geoip --csv file.csv.gz
"""
import csv
import gzip
import io
import os
import sqlite3
import tempfile
import urllib.request
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.console.geo import key

URL = "https://download.db-ip.com/free/dbip-country-lite-{ym}.csv.gz"


def _months():
    today = date.today()
    yield f"{today:%Y-%m}"
    prev = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    yield f"{prev:%Y-%m}"


class Command(BaseCommand):
    help = "Download and build the IP → country lookup file for the Visitors page."

    def add_arguments(self, parser):
        parser.add_argument("--csv", dest="csv_file", help="Use a downloaded dbip-country-lite CSV(.gz) instead.")

    def handle(self, csv_file=None, **options):
        raw = open(csv_file, "rb").read() if csv_file else self._download()
        text = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        target = settings.GEOIP_DB
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".sqlite3")
        os.close(fd)
        conn = sqlite3.connect(tmp)
        conn.execute("CREATE TABLE ranges (start_ip BLOB PRIMARY KEY, end_ip BLOB NOT NULL, cc TEXT NOT NULL) WITHOUT ROWID")
        rows = ((key(a), key(b), cc) for a, b, cc in csv.reader(io.StringIO(text.decode())))
        conn.executemany("INSERT OR REPLACE INTO ranges VALUES (?, ?, ?)", rows)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM ranges").fetchone()[0]
        conn.close()
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        self.stdout.write(self.style.SUCCESS(f"{n} address ranges → {target}"))

    def _download(self):
        for ym in _months():
            try:
                req = urllib.request.Request(URL.format(ym=ym), headers={"User-Agent": "Mozilla/5.0 (QR Menu update_geoip)"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.stdout.write(f"Downloaded DB-IP country lite {ym}")
                    return resp.read()
            except Exception as exc:  # not published yet this month
                last = exc
        raise CommandError(f"Could not download the DB-IP database: {last}")
