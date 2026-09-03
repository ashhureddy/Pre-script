"""
Warning text generation - one function per blueprint section that specifies
a warning in its column C or D. Each returns a list of strings to render
directly beneath that section's table.

Wording is taken verbatim from the Blueprint's 'Pre  checks validation
format' sheet wherever it gives exact text, with {placeholders} filled in.
"""
import re
from collections import defaultdict


def xmu_warnings(rows):
    """Section 7, column C: 'XMU mismatch found on the {node}, check the
    revision history.'"""
    out = []
    for r in rows:
        if r.get('status') == 'MISMATCH':
            out.append(f"XMU mismatch found on the {r['node']}, check the revision history.")
    return out


def parse_primary_secondary_roles(label):
    """'{node}(P)/{node}(S)(mode)' -> {'P': node, 'S': node}. Shared by the
    warning text below and the Streamlit RFDS Validation page's Comments
    column, so both name the same mismatched role the same way."""
    out = {}
    for m in re.finditer(r'([^/()]+)\((P|S)\)', str(label or "")):
        out[m.group(2)] = m.group(1).strip()
    return out


def primary_secondary_mismatched_role(row):
    """Which role (Primary/Secondary/both) actually differs for this row,
    by comparing the CIQ label's (P)/(S) node ids against EDP's and RFDS's."""
    ciq_roles = parse_primary_secondary_roles(row.get('ciq'))
    bad = set()
    for key in ('edp', 'rfds'):
        other = parse_primary_secondary_roles(row.get(key))
        for role in ('P', 'S'):
            if role in ciq_roles and role in other and ciq_roles[role] != other[role]:
                bad.add(role)
    names = {'P': 'Primary', 'S': 'Secondary'}
    return "/".join(names[r] for r in sorted(bad)) if bad else "Primary/Secondary"


def primary_secondary_warnings(rows):
    """RFDS Validation page, Primary & Secondary Node: 'Mismatch found on
    Primary/Secondary id on {node}, raise a Pre integration issue mail.'"""
    out = []
    for r in rows:
        if r.get('status') != 'MISMATCH':
            continue
        role_txt = primary_secondary_mismatched_role(r)
        out.append(f"Mismatch found on {role_txt} id on {r['node']}, raise a Pre integration issue mail.")
    return out


def board_type_warnings(rows):
    """RFDS Validation page, Board Type: 'Board type mismatch found on the
    {node}, raise a Pre integration issue mail.' A planned/agreed board
    swap (status EXPECTED) is not a fault and does not warn."""
    out = []
    for r in rows:
        if r.get('status') == 'MISMATCH':
            out.append(f"Board type mismatch found on the {r['node']}, raise a Pre integration issue mail.")
    return out


def param_warnings(rows):
    """Sections 10 & 11, column C: 'Warning: [Parameter- which parameter
    mismatch] Mismatch found, Please check revison history'. The blueprint
    names the specific parameter, so mismatched field names are extracted
    from each row's note rather than reporting a generic 'mismatch'."""
    by_param = defaultdict(list)
    for r in rows:
        if r.get('status') != 'MISMATCH':
            continue
        note = r.get('note', '')
        m = re.search(r'(?:Planned retune|Mismatch on)\s*\(?([^)\-]+?)\)?\s*[-\u2013]', note)
        fields = m.group(1).strip() if m else 'parameter'
        by_param[fields].append(r.get('cell'))
    out = []
    for fields, cells in by_param.items():
        shown = ', '.join(str(c) for c in cells[:6])
        more = f' (+{len(cells) - 6} more)' if len(cells) > 6 else ''
        out.append(f"[{fields}] Mismatch found on {shown}{more}, Please check revision history")
    return out


def pci_warnings(rows):
    """Section 12, column D: 'Warning: similar PCI found, send the Pre
    integration issue mail'."""
    dupes = [r for r in rows if r.get('status') == 'MISMATCH']
    if not dupes:
        return []
    cells = ', '.join(str(r.get('cell')) for r in dupes[:6])
    more = f' (+{len(dupes) - 6} more)' if len(dupes) > 6 else ''
    return [f"similar PCI found on {cells}{more}, send the Pre integration issue mail"]


def radio_type_warnings(rows):
    """Section 13, column C: 'Warning: Radio swap pending on:'."""
    pending = [r for r in rows if r.get('status') == 'MISMATCH']
    if not pending:
        return []
    cells = ', '.join(str(r.get('cell')) for r in pending[:8])
    more = f' (+{len(pending) - 8} more)' if len(pending) > 8 else ''
    return [f"Radio swap pending on: {cells}{more}"]


def sector_swap_warnings(radio_rows):
    """Section 13 (#21,22,32), column C: 'Radio swap is pending, please set
    the Power, TX/RX, Sector carrier as per pre'. Triggered by the same
    radio-swap condition as section 13, since it's the follow-up action."""
    if any(r.get('status') == 'MISMATCH' for r in radio_rows):
        return ["Radio swap is pending, please set the Power, TX/RX, Sector carrier as per pre"]
    return []


def nr_tac_warnings(rows, sa_nodes):
    """Section 17, column C: 'Warning: SA Configuration on : Node id, in
    pre'. sa_nodes: node ids whose Pre nRTAC is a 7-digit (SA) value."""
    return [f"SA Configuration on : {n}, in pre" for n in sorted(set(sa_nodes))]


def air_radio_warnings(rows, ciq_wb=None):
    """Section 19, column C - four conditions, each with its own warning:
      1) single band radios 6419/6449 - SEF/RRU FieldReplaceableUnit for
         CBAND|DOD should be unique
      2) radio type 8863/4461/4467 - RRU FieldReplaceableUnit should start
         with RRU
      3) 6472 is a sharing radio - SEF and RRU FieldReplaceableUnit may be
         similar for CBAND/DOD cells (informational, not a fault)
      4) Set TX/RX as zero for AIR 3283 radios
    """
    out = []
    single_band, prefix_bad = [], []
    for r in rows:
        rru = str(r.get('rru_type', ''))
        if r.get('status') != 'MISMATCH':
            continue
        if any(m in rru for m in ('6419', '6449')):
            single_band.append(str(r.get('cell')))
        elif any(m in rru for m in ('8863', '4461', '4467')):
            prefix_bad.append(str(r.get('cell')))
    if single_band:
        out.append("Single band radio (6419/6449): SEF/RRU FieldReplaceableUnit for "
                    f"CBAND|DOD should be unique - {', '.join(single_band[:6])}")
    if prefix_bad:
        out.append("RRU FieldReplaceableUnit should start with 'RRU' for 8863/4461/4467 radios - "
                    f"{', '.join(prefix_bad[:6])}")
    return out


def air3283_warnings(ciq_wb):
    """Section 19 condition 4: 'Set TX/RX as zero, for the AIR 3283 Radios'.
    Flags AIR 3283 cells whose CIQ TX/RX antenna counts aren't zero."""
    from ciq_edp_reader import sheet_rows_as_dicts
    offenders = []
    for sheet, cell_col, rru_col in (('eUtran Parameters', 'EutranCellFDDId', 'RRU type'),
                                      ('5G Info', 'NRCellDU', 'RRU Type')):
        if sheet not in ciq_wb.sheetnames:
            continue
        for r in sheet_rows_as_dicts(ciq_wb[sheet]):
            rru = str(r.get(rru_col, '')).upper()
            if '3283' not in rru:
                continue
            tx, rx = r.get('noOfTxAntennas'), r.get('noOfRxAntennas')
            def _nonzero(v):
                try:
                    return int(str(v).strip()) != 0
                except (TypeError, ValueError):
                    return v not in (None, '', '0')
            if _nonzero(tx) or _nonzero(rx):
                offenders.append(f"{r.get(cell_col)} (TX/RX={tx}/{rx})")
    if not offenders:
        return []
    return [f"Set TX/RX as zero for the AIR 3283 radios - {', '.join(offenders[:6])}"]
