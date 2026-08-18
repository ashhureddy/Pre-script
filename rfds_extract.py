"""
RFDS extraction. RFDS files are zip archives (per-page N.jpeg + N.txt +
manifest.json), NOT real PDFs - identical format to what QUICKIX's
extract_pdf_text() already discovered for Pre/Post-checks PDFs. That
function is ported here as load_rfds_pages(); the OCR itself is already
done (baked into the per-page .txt files), so there is no OCR pipeline to
build - only targeted regex extraction per page, same philosophy as the
rest of this tool.

Two reliability tiers, confirmed against real RFDS samples:
  - 'Cell Details (Existing/Final)' pages are clean, row-per-cell text ->
    reliable anchored regex (extract_cell_details).
  - 'Non RF Inventory Details (Existing/Final)' pages have genuinely
    garbled OCR ordering (column-major reading order scrambles multi-line
    cell blocks and even splits node names mid-word across a line break,
    e.g. 'SCL05020,SCCN0050' + '20'). Only a coarse whitespace-collapsed
    presence check is reliable there, NOT full row/column parsing - use
    check_nodes_present_together() for the Primary/Secondary leg of rule
    #3/#31, not anything more structured.
"""
import io
import json
import re
import zipfile


def load_rfds_pages(rfds_bytes):
    """Returns {page_number: page_text}. Ported from QUICKIX's
    extract_pdf_text(), split per-page instead of concatenated, so callers
    can target specific pages/headings without re-searching the whole doc."""
    if rfds_bytes[:2] != b"PK":
        raise ValueError("RFDS bytes are not a zip bundle (unexpected format - "
                          "if this is ever a genuine PDF, add a pdfplumber fallback here).")
    pages = {}
    with zipfile.ZipFile(io.BytesIO(rfds_bytes)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        for p in manifest["pages"]:
            text = zf.read(p["text"]["path"]).decode("utf-8", errors="replace")
            pages[p["page_number"]] = text
    return pages


def find_pages_by_heading(pages, heading_substr):
    """Returns concatenated text of every page whose text contains
    heading_substr (case-insensitive) - handles headings that span 2 pages
    (e.g. 'Cell Details (Final) Page 1 of 2' / 'Page 2 of 2')."""
    sub = heading_substr.lower()
    matched = [text for _, text in sorted(pages.items()) if sub in text.lower()]
    return "\n".join(matched)


_HEADER_NOISE_RE = re.compile(
    r'^(Cell Details \((Existing|Final)\)|Cell ID BBU/CRGNB.*|Page \d+ of \d+|Status)\s*$',
    re.M
)

_CELL_DETAILS_ROW_RE = re.compile(
    r'^(?P<cell>\S+)\s+(?P<bbu>\S+)\s+(?P<rest>.+?)\s+(?P<rcn>\d+)\s+'
    r'(?P<useid>[\d.]+\.[A-Za-z0-9.]+)\s+(?P<status>NEW|EXISTING|UPDATE)\s*$',
    re.M
)


def extract_cell_details(pages, final=True):
    """Rule #6 / #25: Cell ID -> RCN (+ BBU/CRGNB node, RRH radio model),
    from 'Cell Details (Final)' (final=True) or 'Cell Details (Existing)'
    (final=False). Returns {cell_id: {'rcn':, 'bbu':, 'rrh_and_secpos':, 'status':}}.

    'rest' (RRH model + Sector-Position, e.g. '4449 B5/B12 A-1-1,A-1-4') isn't
    split further - the RRH model can itself contain spaces (e.g. 'AIR6472
    B77G B77M'), so splitting it reliably needs the CIQ's own RRU Type value
    to anchor against, which the caller already has; returned as one string
    for the caller to substring-match rather than guessing a split point here.
    """
    heading = 'Cell Details (Final)' if final else 'Cell Details (Existing)'
    text = find_pages_by_heading(pages, heading)
    if not text:
        return {}
    clean = _HEADER_NOISE_RE.sub('', text)
    result = {}
    for m in _CELL_DETAILS_ROW_RE.finditer(clean):
        result[m.group('cell')] = {
            'rcn': m.group('rcn'),
            'bbu': m.group('bbu'),
            'rrh_and_secpos': m.group('rest').strip(),
            'status': m.group('status'),
        }
    return result


def extract_site_details(pages):
    """Site ID / USID / FA Code / ATOLL Site Name for the report header.

    The RFDS Details page's OCR is column-major-scrambled: the FA Code VALUE
    (a bare 8-digit number) appears several lines ABOVE its own header row,
    so label-then-value adjacency parsing does not work here. What IS stable
    is the 'Site ID USID FA Code ATOLL Site Name Location Name' header line
    followed by a data line holding Site ID + USID + ATOLL name - confirmed
    against real samples. FA Code is recovered separately as the lone 8-digit
    token on the page (site IDs are alphanumeric, USID is 6 digits, lat/long
    carry decimal points, so an 8-digit bare integer is unambiguous here).
    """
    text = find_pages_by_heading(pages, 'Site Details')
    if not text:
        return {}
    out = {}
    m = re.search(
        r'Site ID USID FA Code ATOLL Site Name Location Name\s*\r?\n'
        r'(?P<site_id>\S+)\s+(?P<usid>\d+)\s+(?P<atoll>\S+)',
        text)
    if m:
        out['site_id'] = m.group('site_id')
        out['usid'] = m.group('usid')
        out['atoll_site_name'] = m.group('atoll')
    fa = re.findall(r'(?<![\d.])\d{8}(?![\d.])', text)
    if fa:
        out['fa_code'] = fa[0]
    return out


def check_nodes_present_together(pages, node_a, node_b, final=True):
    """Rule #3/#31 (RFDS leg): coarse presence check on the 'Non RF Inventory
    Details' page - are node_a and node_b listed together (as a Common Name
    pair, e.g. 'SCL05020,SCCN005020')? Whitespace-collapsed comparison, since
    the real page text has cell-name blocks and even node names themselves
    split across OCR line-wraps (confirmed: 'SCL05020,SCCN0050' + '20' on
    separate lines for a real sample) - collapsing whitespace reunites them
    without attempting full row/column table parsing, which is not reliable
    on this specific page (see module docstring)."""
    heading = 'Non RF Inventory Details (Final)' if final else 'Non RF Inventory Details (Existing)'
    text = find_pages_by_heading(pages, heading)
    if not text:
        return None  # RFDS page not found - caller should treat as "not checked", not "failed"
    collapsed = re.sub(r'\s+', '', text)
    pair_a = f"{node_a},{node_b}"
    pair_b = f"{node_b},{node_a}"
    return (pair_a in collapsed) or (pair_b in collapsed)


def extract_non_rf_inventory_cells(pages, final=True):
    """Rule #18: which cells are mentioned at all on the Non RF Inventory
    page, for a coarse presence check (CIQ cell exists somewhere in RFDS).
    Same reliability caveat as check_nodes_present_together() - this finds
    cell-name-shaped tokens anywhere on the page, it does not attempt to
    attribute them to a specific equipment row."""
    heading = 'Non RF Inventory Details (Final)' if final else 'Non RF Inventory Details (Existing)'
    text = find_pages_by_heading(pages, heading)
    if not text:
        return set()
    # cell names: SITEID_<digit or Nxxx><sector letter>_<carrier>[_suffix]
    return set(re.findall(r'\b[A-Z0-9]+_(?:N\d{3}|\d)[A-F](?:_\d+)?(?:_[EF])?\b', text))
