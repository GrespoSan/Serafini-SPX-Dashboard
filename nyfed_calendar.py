"""Public New York Fed economic-indicator calendar, without a paid API key.

Reads the Federal Reserve Bank of New York monthly calendar in Eastern Time.
Used only as a fallback for Serafini SPX macro news; never modifies Net Gamma.
This calendar is selective, not a comprehensive catalogue of all US releases.
"""
from datetime import date
from html.parser import HTMLParser
import re
from urllib.request import Request, urlopen

NYFED_URL = "https://www.newyorkfed.org/research/calendars/i-{month}{year}.html"
TIME_RE = re.compile(r"\(\s*(\d{1,2}):(\d{2})\s*\)")
DAY_RE = re.compile(r"^\s*(0?[1-9]|[12]\d|3[01])(?:\s|$)")


class MonthTable(HTMLParser):
    """Capture only table cells; do not evaluate or inject remote markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.texts = []
        self.cells = []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            if self.depth == 0:
                self.texts = []
            self.depth += 1
        if self.depth and tag in ("br", "p", "div"):
            self.texts.append(" ")

    def handle_data(self, data):
        if self.depth:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self.texts.append(cleaned)

    def handle_endtag(self, tag):
        if tag == "td" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.cells.append(" ".join(self.texts))


def parse_month(html, year: int, month: int):
    """Strictly recover dated events with explicit ET hours, no inferred times."""
    parser = MonthTable()
    parser.feed(html)
    items = []
    for cell in parser.cells:
        match = DAY_RE.match(cell)
        if match is None:
            continue
        day = int(match.group(1))
        try:
            stamp = date(year, month, day)
        except ValueError:
            continue
        text = cell[match.end():].strip()
        offset = 0
        for time_match in TIME_RE.finditer(text):
            label = text[offset:time_match.start()].strip(" ,-:;.")
            offset = time_match.end()
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            if not label or hour > 23 or minute > 59:
                continue
            # Only US/Eastern times actually published in the calendar.
            items.append({
                "date": stamp.isoformat(),
                "time_et": f"{hour:02d}:{minute:02d}",
                "series_name": label,
                "country": "US",
                "status": "scheduled",
            })
    if not items:
        raise ValueError("Nessun evento datato riconosciuto nel calendario New York Fed")
    return items


def download_month(year: int, month: int):
    url = NYFED_URL.format(month=date(year, month, 1).strftime("%b").lower(),
                           year=f"{year % 100:02d}")
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; SerafiniSPXCalendar/1.0)",
        "Accept": "text/html",
    })
    with urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("Calendario New York Fed non raggiungibile")
        html = response.read(1_200_001)
    if len(html) > 1_200_000:
        raise ValueError("Pagina calendario oltre il limite previsto")
    return parse_month(html.decode("utf-8", errors="replace"), year, month)


def fetch_window(first: date, last: date, downloader=download_month):
    """Fetch only calendar months that intersect the requested window."""
    if last < first or (last - first).days > 35:
        raise ValueError("Finestra calendario non valida")
    result = []
    month = date(first.year, first.month, 1)
    while month <= last:
        result.extend(downloader(month.year, month.month))
        if month.month == 12:
            month = date(month.year + 1, 1, 1)
        else:
            month = date(month.year, month.month + 1, 1)
    return [event for event in result
            if first <= date.fromisoformat(event["date"]) <= last]
