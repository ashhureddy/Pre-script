"""
Antenna name resolution pipeline — ported verbatim (logic-for-logic) from
QUICKIX's resolveAntenna()/normalise()/suffixCandidates() in the HTML tool's
embedded RFDS-to-CIQ Auto-Validator.

An "Antenna Info" workbook (columns kget_ant_model / rfds_ant_model, header
row auto-detected in the first 5 rows) maps a raw antenna model string as it
appears in a kget-all log to the exact model name used elsewhere (RFDS/CIQ).
Three resolution tiers, tried in order, exactly as the HTML tool does it:

    1. EXACT       - raw string equals a kget_ant_model key verbatim.
    2. NORMALISED  - both sides stripped of everything but A-Z0-9 and upper-
                     cased, then compared.
    3. SUFFIX-STRIP - progressively strips known installation-code suffixes
                     (mounting/version/orientation codes) from the raw
                     string, normalising and re-trying the map at each step.
                     Model-meaningful tokens are intentionally left alone.

Returns confidence info alongside the resolved value, so callers can show
the same colored confidence dot the HTML tool shows (exact/normalised/
suffix-strip/none).
"""
import re

_INSTALL_SUFFIX_RE = [
    re.compile(r'[-_](YU0[1-9]|YU\d{2})$', re.I),
    re.compile(r'[-_](YB0[1-9]|YB\d{2})$', re.I),
    re.compile(r'[-_](HU0[1-9]|HB0[1-9])$', re.I),
    re.compile(r'[-_](HU\d{2}|HB\d{2})$', re.I),
    re.compile(r'[-_]V\d{1,2}$', re.I),
    re.compile(r'[-_]\d+[-_][A-Z]$'),
    re.compile(r'[-_][R-Z]\d[R-Z]\d$', re.I),
    re.compile(r'[-_](RET|UPM|DL|UL|EXT|INT)$', re.I),
]


def normalise(s):
    """Remove ALL non-alphanumeric characters and upper-case."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def suffix_candidates(raw):
    """Candidate strings from progressively stripping known installation-
    code suffixes off the raw antenna model name. Returns a list, original
    string first, each entry appearing once."""
    raw = str(raw or '').strip()
    candidates = [raw]
    s = raw
    prev = None
    while s != prev:
        prev = s
        for pattern in _INSTALL_SUFFIX_RE:
            trimmed = pattern.sub('', s)
            if trimmed and trimmed != s:
                s = trimmed
                candidates.append(s)
                break
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def build_antenna_map(rows, kget_col_hint='kget', rfds_col_hint='rfds'):
    """rows: list of dicts (any header names) from the uploaded Antenna Info
    sheet, OR raw header-1 rows (list of lists) with the header auto-
    detected in the first 5 rows — mirrors the HTML tool's buildAntennaMap().
    Returns {kget_ant_model: [rfds_ant_model, ...]}."""
    antenna_map = {}
    if rows and isinstance(rows[0], dict):
        keys = list(rows[0].keys())
        kget_col = next((k for k in keys if kget_col_hint in str(k).lower()), None)
        rfds_col = next((k for k in keys if rfds_col_hint in str(k).lower()), None)
        if not kget_col or not rfds_col:
            return {}
        for r in rows:
            kget = str(r.get(kget_col) or '').strip()
            rfds = str(r.get(rfds_col) or '').strip()
            if not kget or not rfds:
                continue
            antenna_map.setdefault(kget, [])
            if rfds not in antenna_map[kget]:
                antenna_map[kget].append(rfds)
        return antenna_map

    # raw header-1 rows (list of lists)
    header_row_idx = -1
    kget_col = rfds_col = -1
    for i, row in enumerate(rows[:5]):
        lower = [str(v or '').strip().lower() for v in row]
        ki = next((idx for idx, v in enumerate(lower) if kget_col_hint in v), -1)
        ri = next((idx for idx, v in enumerate(lower) if rfds_col_hint in v), -1)
        if ki != -1 and ri != -1:
            header_row_idx, kget_col, rfds_col = i, ki, ri
            break
    if header_row_idx == -1:
        return {}
    for row in rows[header_row_idx + 1:]:
        kget = str(row[kget_col] if kget_col < len(row) else '' or '').strip()
        rfds = str(row[rfds_col] if rfds_col < len(row) else '' or '').strip()
        if not kget or not rfds:
            continue
        antenna_map.setdefault(kget, [])
        if rfds not in antenna_map[kget]:
            antenna_map[kget].append(rfds)
    return antenna_map


def _norm_index(antenna_map):
    idx = {}
    for kget in antenna_map:
        idx.setdefault(normalise(kget), kget)
    return idx


def resolve_antenna(raw_kget, antenna_map):
    """Resolve a raw antenna model string to its exact rfds_ant_model using
    the supplied Antenna Info map (exact -> normalised -> suffix-strip).
    Returns {'value': str, 'method': 'exact'|'normalised'|'suffix-strip'|'none',
    'matched_kget': str}. Falls back to the raw string untouched when no map
    is loaded or nothing matches (method='none')."""
    if not raw_kget:
        return {'value': '', 'method': 'none', 'matched_kget': ''}
    if not antenna_map:
        return {'value': raw_kget, 'method': 'none', 'matched_kget': ''}

    raw = str(raw_kget).strip()
    if raw in antenna_map:
        return {'value': ' / '.join(antenna_map[raw]), 'method': 'exact', 'matched_kget': raw}

    norm_idx = _norm_index(antenna_map)
    norm_raw = normalise(raw)
    hit = norm_idx.get(norm_raw)
    if hit:
        return {'value': ' / '.join(antenna_map[hit]), 'method': 'normalised', 'matched_kget': hit}

    for candidate in suffix_candidates(raw)[1:]:
        if candidate in antenna_map:
            return {'value': ' / '.join(antenna_map[candidate]), 'method': 'suffix-strip', 'matched_kget': candidate}
        hit = norm_idx.get(normalise(candidate))
        if hit:
            return {'value': ' / '.join(antenna_map[hit]), 'method': 'suffix-strip', 'matched_kget': hit}

    return {'value': raw_kget, 'method': 'none', 'matched_kget': ''}


CONFIDENCE_LABEL = {
    'exact': 'Exact match', 'normalised': 'Matched after normalisation',
    'suffix-strip': 'Matched after suffix-strip', 'none': 'No match',
}
CONFIDENCE_COLOR = {
    'exact': '#2f855a', 'normalised': '#b7791f', 'suffix-strip': '#0056b3', 'none': '#94a3b8',
}
