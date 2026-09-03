"""
RFDS extraction. RFDS files are zip archives (per-page N.jpeg + N.txt +
manifest.json), NOT real PDFs - identical format to what QUICKIX's
extract_pdf_text() already discovered for Pre/Post-checks PDFs. That
function is ported here as load_rfds_pages(); the OCR itself is already
done (baked into the per-page .txt files), so there is no OCR pipeline to
build - only targeted regex extraction per page, same philosophy as the
rest of this tool.

Two reliability tiers, confirmed against real RFDS samples:
  - 'Cell Details (Final)' pages are clean, row-per-cell text ->
    reliable anchored regex (extract_cell_details).
  - 'Non RF Inventory Details (Final)' pages have genuinely
    garbled OCR ordering (column-major reading order scrambles multi-line
    cell blocks and even splits node names mid-word across a line break,
    e.g. 'SCL05020,SCCN0050' + '20'). Only a coarse whitespace-collapsed
    presence check is reliable there, NOT full row/column parsing - use
    check_nodes_present_together() for the Primary/Secondary leg of rule
    #3/#31, not anything more structured.

Every function here targets the '(Final)' tables only. Validation is always
against the post-change design, so the '(Existing)' variants are never the
comparison target - the option to select them was removed rather than left
as a parameter that could be passed by mistake.
"""
import io
import json
import re
import zipfile


def load_rfds_pages(rfds_bytes):
    """Returns {page_number: page_text}. Ported from QUICKIX's
    extract_pdf_text(), split per-page instead of concatenated, so callers
    can target specific pages/headings without re-searching the whole doc.

    Tries the zip-bundle format first (every RFDS sample seen so far), then
    falls back to pdfplumber for a genuine PDF - confirmed necessary: a real
    user's RFDS came through as an actual PDF, not the zip bundle."""
    if rfds_bytes[:2] == b"PK":
        pages = {}
        with zipfile.ZipFile(io.BytesIO(rfds_bytes)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            for p in manifest["pages"]:
                text = zf.read(p["text"]["path"]).decode("utf-8", errors="replace")
                pages[p["page_number"]] = text
        return pages

    if rfds_bytes[:5] == b"%PDF-":
        import pdfplumber
        pages = {}
        with pdfplumber.open(io.BytesIO(rfds_bytes)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                # x_tolerance=1: pdfplumber's default merges adjacent words with
                # no space between them on this RFDS format (confirmed against a
                # real sample - 'SiteDetails', 'FACode' instead of 'Site Details',
                # 'FA Code'), which breaks every heading-anchored regex below.
                # A lower x_tolerance correctly splits words at real visual gaps.
                pages[i] = page.extract_text(x_tolerance=1) or ""
        return pages

    raise ValueError("RFDS bytes are neither a zip bundle nor a genuine PDF "
                      "(unrecognized file format).")


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

_RRH_BAND_SUFFIX_RE = re.compile(r'^B\d|/')


def _extract_rrh(rest):
    """Isolates just the RRH/radio-model from the 'rest' capture (which also
    contains Sector Position and/or a bare frequency-band label mashed in,
    e.g. '4449 B5/B12 A-1-1,A-1-4 700' or '8843 B2/B66A AWS'). Keeps the
    first token (model number/name) plus any following band-suffix tokens
    (start with 'B<digit>' or contain '/'), stops at the first token that
    doesn't - confirmed against real Sector-Position and frequency-label
    formats. Without this, radio-type comparison substring-matched against
    the whole mashed string, which usually still worked by luck but wasn't
    actually comparing RRH to RRH."""
    if not rest:
        return rest
    toks = rest.split()
    rrh = toks[:1]
    for tok in toks[1:]:
        if _RRH_BAND_SUFFIX_RE.match(tok):
            rrh.append(tok)
        else:
            break
    return ' '.join(rrh)


def extract_cell_details(pages):
    """Rule #6 / #25: Cell ID -> RCN (+ BBU/CRGNB node, RRH radio model),
    from 'Cell Details (Final)'.

    The Sector-Position column wraps across two lines in real PDFs
    (e.g. 'A-1-9,A-1-10,A-1' then '-11,A-1-12' on the next line), so a
    single-line regex misses every row that wraps. Fix: join continuation
    lines (lines starting with '-digit' or ',') onto the preceding data line
    before applying the row regex, making the whole data record one logical
    line regardless of how many physical lines the PDF wraps it over.

    Returns {cell_id: {'rcn':, 'bbu':, 'rrh_and_secpos':, 'rrh':, 'status':}}.
    """
    heading = 'Cell Details (Final)'
    text = find_pages_by_heading(pages, heading)
    if not text:
        return {}
    clean = _HEADER_NOISE_RE.sub('', text)

    # Join continuation lines onto their preceding data line.
    # Confirmed patterns from real PDFs:
    #   '-11,A-1-12'  — starts with hyphen-digit
    #   '11,B-1-12'   — starts with digit-comma
    #   ',B-1-4'      — starts with comma-letter (sector-position list wrap)
    joined_lines = []
    for raw_line in clean.split('\n'):
        line = raw_line.rstrip('\r')
        stripped = line.lstrip()
        is_continuation = bool(joined_lines) and (
            re.match(r'^-\d', stripped) or          # -11,A-1-12
            re.match(r'^\d[\d,]', stripped) or      # 11,B-1-12
            re.match(r'^,[A-Za-z]', stripped)       # ,B-1-4
        )
        if is_continuation:
            joined_lines[-1] = joined_lines[-1].rstrip('\r') + stripped
        else:
            joined_lines.append(line)
    clean = '\n'.join(joined_lines)

    _CELL_DETAILS_ROW_RE = re.compile(
        r'^(?P<cell>\S+)[ \t]+(?P<bbu>\S+)[ \t]+(?P<rest>.+?)[ \t]+(?P<rcn>\d+)[ \t]+'
        r'(?P<useid>[\d.]+\.[A-Za-z0-9.]+)[ \t]+(?P<status>NEW|EXISTING|UPDATE|AF MIGRATED)[ \t\r]*$',
        re.M
    )
    result = {}
    for m in _CELL_DETAILS_ROW_RE.finditer(clean):
        result[m.group('cell')] = {
            'rcn': m.group('rcn'),
            'bbu': m.group('bbu'),
            'rrh_and_secpos': m.group('rest').strip(),
            'rrh': _extract_rrh(m.group('rest').strip()),
            'status': m.group('status'),
        }
    return result


def extract_site_details(pages):
    """Site ID / USID / FA Code / ATOLL Site Name for the report header.

    Two known layouts, confirmed against real samples of each:
      - Genuine PDF (pdfplumber): all fields sit on one clean data line, in
        header order - 'Site ID USID FA Code ATOLL Site Name Location Name
        RF Engineer' followed by 'SIFL013958 64921 10020064 FCL04120 ...'.
        Extra trailing columns (RF Engineer) are tolerated, not required.
      - Zip-bundle (OCR): column-major-scrambled - the FA Code VALUE (a bare
        8-digit number) lands several lines above its own header, so it's
        recovered separately as the lone 8-digit token on the page (site IDs
        are alphanumeric, USID is 6 digits, lat/long carry decimal points,
        so a bare 8-digit integer is unambiguous). Site ID/USID/ATOLL still
        come from the header+dataline pattern, just without FA Code inline.
    """
    text = find_pages_by_heading(pages, 'Site Details')
    if not text:
        return {}
    out = {}

    m = re.search(
        r'Site ID\s+USID\s+FA Code\s+ATOLL Site Name\s+Location Name[^\n]*\r?\n'
        r'(?P<site_id>\S+)\s+(?P<usid>\d+)\s+(?P<fa_code>\d+)\s+(?P<atoll>\S+)',
        text)
    if m:
        out['site_id'] = m.group('site_id')
        out['usid'] = m.group('usid')
        out['fa_code'] = m.group('fa_code')
        out['atoll_site_name'] = m.group('atoll')
        return out

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


_PORT_ROW_START_RE = re.compile(r'\b([A-Z]-\d{1,2})\s+')
_CELL_TOKEN_RE = re.compile(r'\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b')


def extract_port_level_details(pages):
    """Antenna Vendor/Model per cell, from 'Port Level Details (Final)'.

    Fixes vs previous version:
    - Antenna model like 'MA-2L4M-65F8-A12P-HG' wraps across two lines in
      real PDFs ('MA-2L4M-65F8-A12P-' then 'HG'). The old code collapsed all
      whitespace first, turning it into 'MA-2L4M-65F8-A12P- 1 HG' (port
      number absorbed into the model). Fix: join dangling-hyphen lines before
      collapsing whitespace, so the model is 'MA-2L4M-65F8-A12P-HG'.
    - Port number (1-2 digit integer between model and first cell token) must
      be stripped from the pre-cell prefix to isolate the pure vendor+model.

    Also returns a grouped structure so the caller can show all cells sharing
    the same antenna position as a list, not just per-cell lookups.

    Returns {cell_name: {'sec_pos':, 'vendor_model':, 'shared_cells': [...]}}
    where shared_cells is every cell on the same sec_pos+model antenna.
    """
    text = find_pages_by_heading(pages, 'Port Level Details (Final)')
    if not text:
        return {}

    # Fix 1: Join multi-line model/cell fragments. Three patterns confirmed:
    #   a) Dangling-hyphen: 'MA-2L4M-65F8-A12P- 1\nHG' — hyphen ends the token,
    #      next line is the suffix. Join by removing the newline (not the hyphen).
    #   b) Port number on same line as model prefix, suffix on next line:
    #      same as (a) after the pre-collapse join handles it.
    # Strategy: collapse all newlines into spaces first (like before), then
    # fix the 'MODEL-PREFIX PORTNUMBER SUFFIX' pattern in the model string.
    pass  # handled below after whitespace collapse

    clean = text.replace('\r\n', ' ').replace('\n', ' ')
    clean = re.sub(r'Sec-Pos\s+Vendor\s+Model\s+Port\s+Cells\s+Gain\s+Elec\s+Tilt\s+Azimuth\s+Offset\s+Coax\s+Type\s+Coax\s+Length', ' ', clean)
    clean = re.sub(r'Port Level Details \(Final\)', ' ', clean)
    clean = re.sub(r'Page \d+ of \d+', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    starts = [m.start() for m in _PORT_ROW_START_RE.finditer(clean)]
    starts.append(len(clean))

    # First pass: collect all (sec_pos, vendor_model) -> [cells] groups
    groups = {}   # (sec_pos, vendor_model) -> set of cells
    per_cell = {} # cell -> (sec_pos, vendor_model)

    for i in range(len(starts) - 1):
        chunk = clean[starts[i]:starts[i + 1]].strip()
        m = _PORT_ROW_START_RE.match(chunk + ' ')
        if not m:
            continue
        sec_pos = m.group(1)
        rest = chunk[m.end():].strip()
        cells = _CELL_TOKEN_RE.findall(rest)
        if not cells:
            continue
        pre = rest[:rest.find(cells[0])].strip()
        pre_tokens = pre.split()
        # Strip trailing port-number tokens (1-3 bare digits). Also handle
        # the pattern 'MODEL-PREFIX PORTNUMBER SUFFIX' where the model suffix
        # landed on the next source line (e.g. 'MA-2L4M-65F8-A12P- 1 HG'):
        # the model is 'MA-2L4M-65F8-A12P-HG' and '1' is the port number.
        # Detect: bare 1-2 digit token with a hyphen-ending token before it
        # and a non-numeric token after it → remove the digit and merge.
        cleaned = []
        j = 0
        while j < len(pre_tokens):
            tok = pre_tokens[j]
            if (re.fullmatch(r'\d{1,2}', tok)
                    and j > 0 and cleaned and cleaned[-1].endswith('-')
                    and j + 1 < len(pre_tokens) and not pre_tokens[j+1][0].isdigit()):
                # skip the port-number token; next token glues to the hyphen
                j += 1
                cleaned[-1] = cleaned[-1] + pre_tokens[j]
            else:
                cleaned.append(tok)
            j += 1
        pre_tokens = cleaned
        while pre_tokens and re.fullmatch(r'\d{1,3}', pre_tokens[-1]):
            pre_tokens.pop()
        vendor_model = ' '.join(pre_tokens)
        key = (sec_pos, vendor_model)
        if key not in groups:
            groups[key] = set()
        for c in cells:
            groups[key].add(c)
            if c not in per_cell:
                per_cell[c] = (sec_pos, vendor_model)

    # Build result: for each cell, include the full list of cells sharing
    # that antenna position so the UI can display them as a group.
    result = {}
    for (sec_pos, vendor_model), cell_set in groups.items():
        shared = sorted(cell_set)
        for c in cell_set:
            result[c] = {'sec_pos': sec_pos, 'vendor_model': vendor_model,
                          'shared_cells': shared}
    return result


_RF_INV_ETYPES = ('ANTENNA', 'RRH', 'TMA', 'DIPLEXER', 'DUPLEXER', 'COMBINER', 'SWITCH', 'JUMPER')
_RF_INV_ANCHOR_RE = re.compile(
    r'([A-Z]-\d+(?:\(D\))?(?:,[A-Z]?-?\d+)?)\s+(' + '|'.join(_RF_INV_ETYPES) + r')\b'
)
_RF_INV_STATUS_RE = re.compile(r'\b(EXISTING|NEW|UPDATE|REMOVE|AF MIGRATED)\b')
_RF_INV_TECH_RE = re.compile(r'^(5G|LTE|4G)(,(5G|LTE|4G))*$')


def extract_rf_inventory_antennas(pages):
    """Antenna Model + linked cells, from 'RF Inventory Details (Final)',
    filtered to EquipmentType == 'ANTENNA'. This is the authoritative
    per-antenna record — a fix over the previous approach of reading
    'Port Level Details (Final)' and inferring per-cell model text from
    Sec-Pos position, which doesn't carry an explicit equipment-type flag
    and mis-split multi-line models around embedded port numbers.

    Two real-PDF quirks fixed here:
      - The Model field sometimes wraps mid-word across two lines with NO
        hyphen ('MA-2L4M-65F8' / '-A12P-HG') — joined without inserting a
        space when the continuation starts with '-', since a hyphen-started
        continuation is always a mid-word wrap, never a new column value.
      - Some RFDS fonts encode a literal hyphen inside the Model string as
        an unprintable control character (\\x02) instead of '-' (confirmed:
        'CMA-BTLBHH\\x026516-20-20' should read 'CMA-BTLBHH-6516-20-20') —
        replaced before parsing.

    AIR-series radios have no separate ANTENNA row here (the antenna is
    integrated into the radio) — those cells simply won't appear in the
    result, which is correct, not a gap; callers already treat AIR-series
    antenna/loss checks as exempt rather than missing.

    Returns {cell_name: {'model':, 'vendor':, 'sec_pos':, 'shared_cells':}}
    where shared_cells lists every cell fed by that same physical antenna.
    """
    text = find_pages_by_heading(pages, 'RF Inventory Details (Final)')
    if not text:
        return {}
    text = text.replace('\x02', '-').replace('\u0002', '-')
    text = re.sub(r'^RF Inventory Details \(Final\).*$', '', text, flags=re.M)
    text = re.sub(r'^Model Cascaded.*$', '', text, flags=re.M)
    text = re.sub(r'^From/Cpri.*$', '', text, flags=re.M)
    text = re.sub(r'^Page \d+ of \d+\s*$', '', text, flags=re.M)

    # Smart line-join: a continuation line starting with '-' is always a
    # mid-word wrap (join with no space); anything else is a normal
    # column/row wrap (join with a single space).
    flat_parts = []
    for raw_line in text.split('\n'):
        line = raw_line.rstrip('\r').strip()
        if not line:
            continue
        if flat_parts and line.startswith('-'):
            flat_parts[-1] = flat_parts[-1] + line
        else:
            flat_parts.append(line)
    flat = re.sub(r'\s+', ' ', ' '.join(flat_parts)).strip()

    # Split into one record per row, using the Status keyword as the
    # (reliable, closed-vocabulary) row terminator.
    records, pos = [], 0
    for m in _RF_INV_STATUS_RE.finditer(flat):
        records.append(flat[pos:m.end()].strip())
        pos = m.end()

    result = {}
    for rec in records:
        m = _RF_INV_ANCHOR_RE.search(rec)
        if not m or m.group(2) != 'ANTENNA':
            continue
        before, after = rec[:m.start()], rec[m.end():]
        cells = _CELL_TOKEN_RE.findall(before)
        if not cells:
            continue
        model = re.sub(r'\s+', ' ', _CELL_TOKEN_RE.sub('', before)).strip()
        vendor_tokens = []
        for tok in after.split():
            if re.fullmatch(r'-(/-)?', tok) or _RF_INV_STATUS_RE.fullmatch(tok) or _RF_INV_TECH_RE.match(tok):
                break
            vendor_tokens.append(tok)
        vendor = ' '.join(vendor_tokens)
        shared = sorted(cells)
        for c in cells:
            if c not in result:
                result[c] = {'model': model, 'vendor': vendor, 'sec_pos': m.group(1), 'shared_cells': shared}
    return result


def load_rfds_tables(rfds_bytes):
    """Returns {page_number: [[row, ...], ...]} using pdfplumber's table
    extraction - genuine PDFs only (returns {} for the zip-bundle format,
    which has no real table structure to extract, only OCR'd text).

    Kept separate from load_rfds_pages() deliberately: extract_tables()
    gives much cleaner per-row columns than plain text for well-behaved
    rows (confirmed against a real sample), but the rest of this module's
    functions are text-regex-based and already handle both RFDS formats -
    changing their input shape to accommodate tables would touch a lot of
    already-working code for a capability only two new functions need."""
    if rfds_bytes[:5] != b"%PDF-":
        return {}
    import pdfplumber
    tables = {}
    with pdfplumber.open(io.BytesIO(rfds_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            t = page.extract_tables()
            if t:
                tables[i] = t
    return tables


def extract_xmu_by_node(rfds_bytes):
    """Node-specific XMU presence (rule #27) via pdfplumber table extraction,
    scoped to the 'Non RF Inventory Details' page(s) only (found via the
    text-based heading search first - confirmed necessary: scanning every
    page's tables blindly pulls in unrelated tables from Design Summary, RF
    Inventory, etc. and produces garbage node names). Returns {node: bool},
    or None if this RFDS is the zip-bundle/OCR format (no tables to
    extract) - callers should fall back to the page-wide check in that case.

    Verified against every RFDS sample available (5 genuine PDFs, incl. 3
    with real XMU rows):
      - XMU is always recorded in the EquipmentConfiguration column, as
        '1xXMU' within a '1x6601/ 1x5216/ 1xXMU'-style stack. It is never
        in Model or EquipmentType, so only Configuration is checked.
      - Column positions shift between the '(Existing)' and '(Final)' pages
        of the same document (11 vs 10 columns - 'EquipmentType' splits
        across two cells on one), so each table's own header row is located
        and column indices read from it rather than assumed.
      - CommonName legitimately contains newlines when a value wraps
        ('OKTN000082,OKL02\\n082' is ONE node pair, not a merged cell), so
        newlines are stripped and the value comma-split - an earlier version
        skipped these rows as 'merged' and would have missed a real XMU.
    """
    if rfds_bytes[:5] != b"%PDF-":
        return None

    pages_text = load_rfds_pages(rfds_bytes)
    heading = 'Non RF Inventory Details (Final)'
    target_pages = {num for num, text in pages_text.items() if heading.lower() in text.lower()}
    if not target_pages:
        return None

    import pdfplumber
    result = {}
    found_any_row = False
    with pdfplumber.open(io.BytesIO(rfds_bytes)) as pdf:
        for page_num in sorted(target_pages):
            for table in pdf.pages[page_num - 1].extract_tables():
                col_idx = {}
                for row in table:
                    cells = [str(c).replace('\n', '').strip() if c else '' for c in row]
                    if 'CommonName' in ''.join(cells):
                        for i, c in enumerate(cells):
                            if c == 'Vendor':
                                col_idx['vendor'] = i
                            elif c == 'Model':
                                col_idx['model'] = i
                            elif c == 'CommonName':
                                col_idx['common_name'] = i
                            elif 'Configuration' in c:
                                col_idx['config'] = i
                        break
                if 'common_name' not in col_idx or 'config' not in col_idx:
                    continue

                for row in table:
                    if len(row) <= max(col_idx.values()):
                        continue
                    cells = [str(c).replace('\n', '') if c else '' for c in row]
                    common_name = cells[col_idx['common_name']].strip()
                    config = cells[col_idx['config']].strip()
                    vendor = cells[col_idx['vendor']].strip() if 'vendor' in col_idx else ''
                    if not (common_name and vendor) or common_name == 'CommonName':
                        continue
                    found_any_row = True
                    has_xmu = 'XMU' in config.upper()
                    for name in common_name.split(','):
                        name = name.strip()
                        if name:
                            result[name] = result.get(name, False) or has_xmu

    if found_any_row:
        return result

    # Fallback: some documents collapse the whole data row into one merged
    # cell, leaving no column structure at all (confirmed on a real sample:
    # every column but the first comes back None, and the plain-text version
    # splits the row so the node name and its '1xXMU' marker land on
    # different lines). The LinkedCells values are still intact there, and
    # every cell name is prefixed with its own node - so node identity is
    # recoverable from those prefixes even when CommonName isn't readable.
    # XMU presence can only be established page-wide in this case, so it's
    # applied to every node found on the page rather than to one specific
    # node - which is the same precision the page-wide check gives, but at
    # least scoped to the correct page's nodes.
    page_text = '\n'.join(pages_text[n] for n in sorted(target_pages))
    if 'XMU' not in page_text.upper():
        return None
    nodes = set(re.findall(r'^([A-Z]{2,}[A-Z0-9]*?)_(?:N\d{3}|\d)[A-F](?:_\d+)?(?:_[EF])?$',
                            page_text, re.M))
    if not nodes:
        return None
    return {n: True for n in nodes}


def _has_single_equipment_record(pages):
    """True if 'Non RF Inventory Details (Final)' has exactly one equipment
    row (one 'BBU'/'RAN PROCESSOR'/model mention) - used to justify grouping
    every node prefix on the page as one CommonName pair when the table
    structure has fully collapsed (see check_nodes_present_together)."""
    text = find_pages_by_heading(pages, 'Non RF Inventory Details (Final)')
    if not text:
        return None
    records = len(re.findall(r'\bBBU\s+ERICSSON\b', text, re.I))
    return records == 1


def extract_common_name_groups(rfds_bytes):
    """Returns the list of CommonName node-groups from 'Non RF Inventory
    Details (Final)', e.g. [['HXL00147'], ['HXL04147','HXIN010147']].
    Genuine-PDF RFDS only (returns None for the zip-bundle/OCR format).

    Uses pdfplumber table extraction rather than page text, because a
    CommonName value that wraps mid-word is NOT recoverable from the text
    version: the two fragments end up separated by a whole interleaved line
    (confirmed on a real sample - 'HXL04147,HXIN0101' and its trailing '47'
    are split by the row's own 'BBU ERICSSON ...' line), so collapsing
    whitespace glues the fragment to the wrong neighbour. The table cell
    keeps them together as 'HXL04147,HXIN0101\\n47', which rejoins cleanly."""
    if rfds_bytes[:5] != b"%PDF-":
        return None

    pages_text = load_rfds_pages(rfds_bytes)
    heading = 'Non RF Inventory Details (Final)'
    target_pages = {num for num, text in pages_text.items() if heading.lower() in text.lower()}
    if not target_pages:
        return None

    import pdfplumber
    groups = []
    with pdfplumber.open(io.BytesIO(rfds_bytes)) as pdf:
        for page_num in sorted(target_pages):
            for table in pdf.pages[page_num - 1].extract_tables():
                cn_idx = None
                for row in table:
                    cells = [str(c).replace('\n', '').strip() if c else '' for c in row]
                    if 'CommonName' in cells:
                        cn_idx = cells.index('CommonName')
                        break
                if cn_idx is None:
                    continue
                for row in table:
                    if len(row) <= cn_idx or not row[cn_idx]:
                        continue
                    value = str(row[cn_idx]).replace('\n', '').strip()
                    if not value or value == 'CommonName':
                        continue
                    names = [n.strip() for n in value.split(',') if n.strip()]
                    if names:
                        groups.append(names)
    return groups or None


def check_nodes_present_together(pages, node_a, node_b, rfds_bytes=None):
    """Rule #3/#31 (RFDS leg): are node_a and node_b listed together as one
    CommonName group?

    Prefers the table-based lookup (extract_common_name_groups) when the
    RFDS bytes are available, since a wrapped CommonName can't be reassembled
    from page text - see that function's docstring. Falls back to the
    whitespace-collapsed text check for the zip-bundle/OCR format, where
    the fragments do sit adjacently ('SCL05020,SCCN0050' + '20')."""
    if rfds_bytes is not None:
        groups = extract_common_name_groups(rfds_bytes)
        if groups is not None:
            a, b = str(node_a).strip().upper(), str(node_b).strip().upper()
            for g in groups:
                upper = [n.upper() for n in g]
                if a in upper and b in upper:
                    return True
            return False
        # Table extraction found no usable rows at all - confirmed real case:
        # a 33-cell LinkedCells list collapsed the entire equipment row into
        # one merged cell, and the CommonName value itself wraps mid-digit
        # across an interleaved 'BBU ERICSSON ...' line ('FSNN0904' + '52'),
        # which defeats whitespace-collapse too. Fall back to a narrower but
        # defensible signal: if the page has exactly ONE equipment record
        # (one 'BBU'/model mention), every distinct node prefix on it
        # necessarily belongs to that one record.
        single_record = _has_single_equipment_record(pages)
        if single_record is True:
            prefixes = extract_non_rf_inventory_cells(pages)
            node_prefixes = {re.match(r'^([A-Za-z0-9]+?)_', c).group(1) for c in prefixes
                              if re.match(r'^([A-Za-z0-9]+?)_', c)}
            a, b = str(node_a).strip().upper(), str(node_b).strip().upper()
            if a in {p.upper() for p in node_prefixes} and b in {p.upper() for p in node_prefixes}:
                return True

    heading = 'Non RF Inventory Details (Final)'
    text = find_pages_by_heading(pages, heading)
    if not text:
        return None  # RFDS page not found - caller should treat as "not checked", not "failed"
    collapsed = re.sub(r'\s+', '', text)
    pair_a = f"{node_a},{node_b}"
    pair_b = f"{node_b},{node_a}"
    return (pair_a in collapsed) or (pair_b in collapsed)


def extract_non_rf_inventory_cells(pages):
    """Rule #18: which cells are mentioned at all on the Non RF Inventory
    page, for a coarse presence check (CIQ cell exists somewhere in RFDS).
    Same reliability caveat as check_nodes_present_together() - this finds
    cell-name-shaped tokens anywhere on the page, it does not attempt to
    attribute them to a specific equipment row."""
    heading = 'Non RF Inventory Details (Final)'
    text = find_pages_by_heading(pages, heading)
    if not text:
        return set()
    # cell names: SITEID[_USID]_<digit or Nxxx><sector letter>_<carrier>[_suffix]
    # The optional middle segment matters: some gNodeB cell names carry the
    # site USID between node and band (confirmed on a real sample -
    # 'HXIN090147F_056338_N077A_1'), and a pattern without it silently
    # matched none of that node's cells, flagging all six as missing from an
    # RFDS that plainly contained them.
    return set(re.findall(r'\b[A-Z0-9]+(?:_\d+)?_(?:N\d{3}|\d)[A-F](?:_\d+)?(?:_[EF])?\b', text))
