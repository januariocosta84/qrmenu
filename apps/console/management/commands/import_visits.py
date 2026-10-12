"""
Fill the visitor counter with past days from the web server's access logs.

    python manage.py import_visits /var/log/apache2/qrmenu-access.log* --exclude-ip 203.0.113.10   # e.g. this server's own address

Only days before today are imported (today is counted live); existing rows for those
days are replaced, so it is safe to run again.
"""
import collections
import datetime
import gzip
import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.console.models import PageViewDay
from apps.console.visits import BOT, classify
from apps.restaurants.models import Restaurant

LINE = re.compile(r'^(\S+) \S+ \S+ \[(\d+/\w+/\d+):[^\]]+\] "(\w+) (\S+) [^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)"')


class Command(BaseCommand):
    help = "Import past daily visitors from Apache access logs (combined format)."

    def add_arguments(self, parser):
        parser.add_argument("logs", nargs="+")
        parser.add_argument("--exclude-ip", action="append", default=[], help="e.g. this server's own address")

    def handle(self, logs, exclude_ip, **options):
        today = timezone.localdate()
        slugs = dict(Restaurant.objects.values_list("slug", "id"))
        views = collections.Counter()
        people = collections.defaultdict(set)
        first_seen = {}
        for path in logs:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", errors="replace") as fh:
                for line in fh:
                    m = LINE.match(line)
                    if not m:
                        continue
                    ip, day, method, url, status, ua = m.groups()
                    when = datetime.datetime.strptime(line.split("[", 1)[1].split("]", 1)[0], "%d/%b/%Y:%H:%M:%S %z")
                    if method != "GET" or status != "200" or ip in exclude_ip or not ua or BOT.search(ua):
                        continue
                    day = datetime.datetime.strptime(day, "%d/%b/%Y").date()
                    hit = classify(url.split("?")[0])
                    if day >= today or hit is None:
                        continue
                    page, slug = hit
                    rid = slugs.get(slug) if slug else None
                    if slug and rid is None:
                        continue
                    key = (day, page, rid)
                    views[key] += 1
                    people[key].add((ip, ua))
                    first_seen.setdefault((day, page, rid, ip, ua), when)
        days = sorted({k[0] for k in views})
        from apps.console import geo
        from apps.console.models import VisitorLog

        with transaction.atomic():
            VisitorLog.objects.filter(created_at__date__in=days).delete()
            VisitorLog.objects.bulk_create([
                VisitorLog(created_at=when, page=p, restaurant_id=r, ip_masked=geo.mask(ip), country=geo.country(ip))
                for (d, p, r, ip, ua), when in first_seen.items()
            ])
            PageViewDay.objects.filter(date__in=days).delete()
            PageViewDay.objects.bulk_create([
                PageViewDay(date=d, page=p, restaurant_id=r, views=n, visitors=len(people[(d, p, r)]))
                for (d, p, r), n in views.items()
            ])
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(views)} rows for {len(days)} day(s)" + (f": {days[0]} to {days[-1]}" if days else "")))
