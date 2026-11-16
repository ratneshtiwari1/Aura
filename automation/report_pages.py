"""Ordered page requests and the backward-compatible report envelope."""
import re

MAX_REPORT_PAGES = 12
_TITLE_WORDS = r"(?:called|named|titled)"
_NAME_TAIL = r"(?:with|containing|showing|including|having|that\s+(?:has|shows|contains))"
HEADER = re.compile(
    r"(?:^|[\n;]|(?<=\.))\s*Page\s+(\d+)\s*(?:"
    r"(?:[-–—:]\s*|" + _TITLE_WORDS + r"\s+)?([^:\n;]{0,100}):\s*"
    r"|" + _TITLE_WORDS + r"\s+([^:\n;]{1,100}?)\s+" + _NAME_TAIL + r"\s+"
    r")",
    re.I,
)
MULTI = re.compile(r"\b(?:multi[- ]?page|(?:multiple|several)[- ]pages?|more than one page|(?:[2-9]|\d{2,}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[- ]pages?)\b", re.I)
COUNT_WORDS = "zero one two three four five six seven eight nine ten eleven twelve".split()
COUNT = re.compile(r"\b(\d+|" + "|".join(COUNT_WORDS) + r")[- ]pages?\b", re.I)
GUIDANCE = "Describe each page, for example: Page 1 Overview: KPI card for total revenue; Page 2 Products: Total revenue by Product as a bar chart."


def requested_page_count(text):
    match = COUNT.search(text)
    if not match:
        return None
    word = match[1].casefold()
    count = int(word) if word.isdigit() else COUNT_WORDS.index(word)
    if count < 2 or count > MAX_REPORT_PAGES:
        raise ValueError(f"Use between 2 and {MAX_REPORT_PAGES} report pages.")
    return count


def is_multi_page_request(text):
    return bool(MULTI.search(text))


def page_requests(text):
    headers = list(HEADER.finditer(text))
    if not headers and not MULTI.search(text):
        return []
    if not headers:
        if re.search(r"\bpage\s+\d+\b", text, re.I):
            raise ValueError(GUIDANCE)
        requested_page_count(text)
        return []
    if len(headers) < 2 and MULTI.search(text):
        raise ValueError(GUIDANCE)
    preamble = text[:headers[0].start()]
    count = COUNT.search(preamble)
    if count:
        word = count[1].casefold()
        expected = int(word) if word.isdigit() else COUNT_WORDS.index(word)
        if expected != len(headers):
            raise ValueError(f"Requested {expected} pages but found {len(headers)} page descriptions. " + GUIDANCE)
    if len(headers) > MAX_REPORT_PAGES:
        raise ValueError(f"Use at most {MAX_REPORT_PAGES} report pages.")
    result = []
    names = set()
    for i, header in enumerate(headers):
        name = (header[2] or header[3] or "").strip(" -–—") or f"Page {i + 1}"
        goal = text[header.end():headers[i + 1].start() if i + 1 < len(headers) else None].strip(" ;\n.")
        if int(header[1]) != i + 1 or not goal or name.casefold() in names:
            raise ValueError(GUIDANCE)
        names.add(name.casefold())
        result.append((name, goal))
    return result


def report_pages(spec):
    additional = spec.get("additionalPages", [])
    if not isinstance(additional, list) or len(additional) >= MAX_REPORT_PAGES:
        raise ValueError(f"A report supports 1 to {MAX_REPORT_PAGES} pages.")
    pages = [{k: v for k, v in spec.items() if k != "additionalPages"}, *additional]
    names = set()
    for page in pages:
        if not isinstance(page, dict) or "additionalPages" in page or not isinstance(page.get("page"), str):
            raise ValueError("Each report page needs a name and visual specification.")
        name = page["page"].strip().casefold()
        if not name or name in names:
            raise ValueError("Each report page needs a distinct, non-empty name.")
        names.add(name)
    return pages


def pack_pages(pages):
    return {**pages[0], **({"additionalPages": pages[1:]} if len(pages) > 1 else {})}


def validate_sync_groups(spec, require_multiple_pages=False):
    groups = {}
    for page in report_pages(spec):
        for visual in page.get("visuals", []):
            if not isinstance(visual, dict) or "syncGroup" not in visual:
                continue
            name, field = visual["syncGroup"], visual.get("field")
            if visual.get("type") != "slicer" or not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("A sync group must have a non-empty name and belong to a slicer.")
            if not isinstance(field, dict) or not isinstance(field.get("table"), str) or not isinstance(field.get("field"), str):
                raise ValueError("A synchronized slicer requires a valid field binding.")
            binding = (field["table"], field["field"])
            group = groups.setdefault(name, {"binding": binding, "pages": set()})
            if group["binding"] != binding:
                raise ValueError(f"Synchronized slicers in group '{name}' must use the same table and field.")
            if page["page"] in group["pages"]:
                raise ValueError(f"Page {page['page']} contains duplicate slicers in sync group '{name}'.")
            group["pages"].add(page["page"])
    if require_multiple_pages:
        for name, group in groups.items():
            if len(group["pages"]) < 2:
                raise ValueError(f"Sync group '{name}' must have slicers on at least two pages.")
    return groups
