"""
Band/sector label system — ported verbatim from QUICKIX's app.py (the
Integration Template Generator), since this new tool reuses the exact same
CIQ cell-naming conventions and needs identical labels for consistency with
the rest of the MasTec/QuadGen tooling. Do not "improve" these mappings
without confirming against real sites first — they were confirmed the same
way originally.
"""
import re

SECTOR_NAME = {'A': 'Alpha', 'B': 'Beta', 'C': 'Gamma', 'D': 'Delta', 'E': 'Epsilon', 'F': 'Foxtrot'}
SECTOR_ORDER = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Foxtrot']


def lte_band_label(cell_name):
    """e.g. ECL00043_2A_1 -> ('AWS_1', 'Alpha') ; DXL04049_7A_2_F -> ('FNET', 'Alpha')"""
    if not cell_name:
        return None, None
    m = re.search(r'_(\d)([A-F])_(\d+)(_[EF])?$', str(cell_name))
    if not m:
        return None, None
    digit, letter, carrier, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
    sector = SECTOR_NAME.get(letter, letter)
    if digit == '9':
        return f"PCS_{carrier}", sector
    if digit == '2':
        return f"AWS_{carrier}", sector
    if digit == '8':
        return f"850_{carrier}", sector
    if digit == '3':
        return "WCS", sector
    if digit == '7':
        if suffix == '_F':
            return "FNET", sector
        if suffix == '_E':
            return "LTE_700_E", sector
        return "LTE_700", sector
    return f"BAND{digit}_{carrier}", sector


def nr_band_label(cell_name):
    """e.g. NCRN002376_N066A_1 -> ('5G_AWS_1', 'Alpha') ; ..._N077A_2 -> ('DOD', 'Alpha')"""
    if not cell_name:
        return None, None
    m = re.search(r'_N(\d{3})([A-F])_(\d+)$', str(cell_name))
    if not m:
        return None, None
    band, letter, carrier = m.group(1), m.group(2), m.group(3)
    sector = SECTOR_NAME.get(letter, letter)
    if band == '005':
        return "5G_850", sector
    if band == '002':
        return f"5G_PCS_{carrier}", sector
    if band == '066':
        return f"5G_AWS_{carrier}", sector
    if band == '077':
        return {'1': 'CBAND', '2': 'DOD', '3': 'DOD_BWE'}.get(carrier, f"N077_{carrier}"), sector
    if band == '260':
        return "MMWAVE", sector
    return f"N{band}_{carrier}", sector


def band_label(cell_name):
    """Dispatch to LTE or 5G labeler based on whether the cell name contains an 'N0xx' 5G marker."""
    if re.search(r'_N\d{3}[A-F]_\d+$', str(cell_name or '')):
        return nr_band_label(cell_name)
    return lte_band_label(cell_name)


def is_5g_cell(cell_name):
    return bool(re.search(r'_N\d{3}[A-F]_\d+$', str(cell_name or '')))


def is_mmwave_cell(cell_name):
    """Rule #10: mmWave identification signal, per confirmed decision - N260 band marker.
    Real cell names carry a trailing suffix after the carrier number
    (e.g. 'ILRN004372_N260A_1_M') - not anchored to end right after the
    digit, or every mmWave cell on a real CIQ silently matches nothing."""
    return bool(re.search(r'_N260[A-F]_\d+(?:_\S+)?$', str(cell_name or '')))


def is_cband_cell(cell_name):
    label, _ = band_label(cell_name)
    return label == 'CBAND'


def is_dod_cell(cell_name):
    label, _ = band_label(cell_name)
    return label in ('DOD', 'DOD_BWE')


def is_wll_node_name(name):
    """Confirmed rule (QUICKIX): any node name ending in 'L' is a WLL node - a
    co-located logical entity, not a real radio node."""
    return bool(name) and str(name).strip().upper().endswith("L")


_BAND_NUMBER_RE = re.compile(r'(?:E-?UTRA|NR)?\s*Band\s*(\d+)', re.IGNORECASE)


def underlying_band_number(band_str):
    """Extract the real numeric band (e.g. '30' from 'WCS MHz B (5 MHz)
    E-UTRA Band 30') out of a raw CIQ 'eUTRA operating band'/'Operating
    Band' cell value.

    Confirmed real: the CIQ's dropdown for WCS (Band 30) spells out the
    sub-block and channel width in the SAME string as the band number -
    'WCS MHz A (5 MHz) E-UTRA Band 30' vs 'WCS MHz A+B (10 MHz) E-UTRA
    Band 30' vs 'WCS MHz B (5 MHz) E-UTRA Band 30'. Those are the SAME
    physical band (30) at different bandwidths, not different bands, so
    any check comparing raw band strings must key on this number instead
    or it false-flags a legitimate same-band/different-bandwidth carrier
    as 'Carrier Reused Across Bands'. Returns None if no band number is
    found in the string (caller should then fail safe and not suppress)."""
    if not band_str:
        return None
    m = _BAND_NUMBER_RE.search(str(band_str))
    return m.group(1) if m else None


def same_underlying_band(band_values):
    """True only when every value in band_values resolves to the SAME
    real band number via underlying_band_number() - i.e. the apparent
    'multiple bands' are really one band split by bandwidth/sub-block
    text. False (fail safe, still flag) if any value's band number can't
    be determined or if the numbers genuinely differ."""
    numbers = [underlying_band_number(b) for b in band_values]
    if any(n is None for n in numbers):
        return False
    return len(set(numbers)) == 1


def dedupe_labels(cell_names, lte_first=True):
    """Classify a list of cell names into unique band labels, LTE group first
    then 5G group, preserving first-seen order within each group."""
    lte_labels, fiveg_labels = [], []
    for c in cell_names:
        label, _ = band_label(c)
        if not label:
            continue
        target = fiveg_labels if is_5g_cell(c) else lte_labels
        if label not in target:
            target.append(label)
    return (lte_labels + fiveg_labels) if lte_first else (fiveg_labels + lte_labels)
