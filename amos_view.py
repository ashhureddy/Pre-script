"""
Pre kget-all log -> flat per-node/per-cell tables, mirroring QUICKIX's
embedded AMOS module (Node Summary / LTE Cells / NR Cells tables).

Reuses the confirmed extraction primitives in pre_extract.py and the two
private sector-config helpers in checks_sector.py (same functions the
rule-based checks already use) rather than re-parsing the log text with new
regexes — the goal here is a different VIEW of already-confirmed data, not
new extraction logic.

Two fields the HTML tool shows are NOT available from this project's Pre
kget-all commands, and are labelled as such rather than guessed:
    - PTP status       (no PTP signal in these hget commands — see
                         run_validation.py's own documented limitation)
    - Pre-existing DSS (no essScPairId/essScLocalId in these hget commands)
"""
import re

import band_labels as bl
import checks_sector as cs
import pre_cell_inventory as pci
import pre_extract as pe


def _has_amf_signal(text):
    """Best-effort proxy for the HTML tool's TermPointToAmf presence check —
    a 5G core AMF termination point reference anywhere in the log."""
    return bool(text) and bool(re.search(r'TermPointToAmf', text, re.I))


def sa_nsa_status(text, nr_tac_by_cell):
    """Same rule as QUICKIX's findSaNsaStatus(): AMF present AND at least one
    7-digit nRTAC -> SA, else NSA."""
    has_7digit = any(str(v or '').isdigit() and len(str(v)) == 7 for v in nr_tac_by_cell.values())
    return "SA" if (_has_amf_signal(text) and has_7digit) else "NSA"


def build_node_summary(node_id, text):
    sw = pe.extract_sw_version(text) or {}
    lte_cells = pci.extract_pre_cells_for_node(text)
    nr_tac = pe.extract_nr_tac(text)
    has_lte = any(not bl.is_5g_cell(c) for c in lte_cells)
    has_nr = any(bl.is_5g_cell(c) for c in lte_cells)
    node_type = "LTE + 5G" if (has_lte and has_nr) else "LTE Only" if has_lte else "5G Only" if has_nr else "Unknown"
    return {
        "node": node_id,
        "sw_version": sw.get("sw_version", "NOT FOUND"),
        "sw_package": sw.get("sw_package", "NOT FOUND"),
        "type": node_type,
        "ptp_status": "Not available from Pre kget-all logs",
        "sa_nsa_status": sa_nsa_status(text, nr_tac) if has_nr else "-",
    }


def build_lte_cell_rows(node_id, text):
    """Node, Cell, Sector Carrier, RRUs, Radio type, Sharing Radio, TX, RX,
    RFBRANCHTXREF, RFBRANCHRXREF, SEF RFBRANCHES, Pre Existing DSS — matches
    QUICKIX HTML's LTE Cells table column-for-column."""
    cells = sorted(c for c in pci.extract_pre_cells_for_node(text) if not bl.is_5g_cell(c))
    radio_by_cell = pe.extract_cell_to_radio(text)
    fru_by_cell = pe.extract_cell_to_fru(text)
    cfg_by_cell = cs._extract_sector_config(text)
    branch_refs = pe.extract_rf_branch_refs(text)
    sector_carrier_by_cell = _extract_sector_carrier_numbers(text)

    # Sharing radio: same RRU + same band serving DIFFERENT sector letters —
    # same definition as QUICKIX's radioBandMap (cross-sector share only; a
    # single radio carrying multiple carriers on the SAME sector is normal).
    radio_band_map = {}
    for cell in cells:
        band, sector = bl.band_label(cell)
        rru = fru_by_cell.get(cell)
        if not (band and sector and rru):
            continue
        key = (rru, band)
        radio_band_map.setdefault(key, {}).setdefault(sector, set()).add(cell)

    rows = []
    for cell in cells:
        band, sector = bl.band_label(cell)
        fru = fru_by_cell.get(cell, "-")
        radio_model = pe._short_radio_name(radio_by_cell.get(cell)) or "-"
        cfg = cfg_by_cell.get(cell)
        sharing = "No"
        if band and sector and fru != "-":
            by_sector = radio_band_map.get((fru, band), {})
            shared = {c for sec, cs_ in by_sector.items() if sec != sector for c in cs_}
            if shared:
                sharing = ", ".join(sorted(shared))
        refs = branch_refs.get(cell, {})
        rows.append({
            "node": node_id, "cell": cell,
            "sector_carrier": sector_carrier_by_cell.get(cell, "-"),
            "rru": fru, "radio_type": radio_model, "sharing_radio": sharing,
            "tx": cfg["tx"] if cfg else "-", "rx": cfg["rx"] if cfg else "-",
            "rfbranch_tx_ref": refs.get("tx_ref") or "-", "rfbranch_rx_ref": refs.get("rx_ref") or "-",
            "sef_rfbranches": refs.get("sef_branches") or "-",
            "pre_existing_dss": "Not available from Pre kget-all logs",
        })
    return rows


def _extract_sector_carrier_numbers(text):
    """Cell -> the raw SectorCarrier/NRSectorCarrier index shown in QUICKIX's
    'Sector Carries' column (e.g. 'SectorCarrier=7_1' -> '7_1',
    'SectorCarrier=10' -> '10'). Reuses the same reservedBy cross-reference
    block extract_rf_branch_refs() already parses, just keeping the numeric
    suffix instead of the branch refs."""
    import re
    from log_parser import get_command_block
    id_block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction') or ''
    cell_to_num = {}
    for m in re.finditer(r'^(?:SectorCarrier|NRSectorCarrier)=(\S+)\s+.*$', id_block, re.M):
        num, rest = m.group(1), m.group(0)
        for cell in re.findall(r'(?:EUtranCellFDD|NRCellDU)=(\S+)', rest):
            cell_to_num[cell] = num
    return cell_to_num


def build_nr_cell_rows(node_id, text):
    """Node, Cell, RRUs, TX, RX, SEF RFBRANCHES — matches QUICKIX HTML's
    5G NR Cells table."""
    cells = sorted(c for c in pci.extract_pre_cells_for_node(text) if bl.is_5g_cell(c))
    fru_by_cell = pe.extract_cell_to_fru(text)
    cfg_by_cell = cs._extract_sector_config_5g(text)
    branch_refs = pe.extract_rf_branch_refs(text)

    rows = []
    for cell in cells:
        cfg = cfg_by_cell.get(cell)
        refs = branch_refs.get(cell, {})
        rows.append({
            "node": node_id, "cell": cell,
            "rru": fru_by_cell.get(cell, "-"),
            "tx": cfg["tx"] if cfg else "-", "rx": cfg["rx"] if cfg else "-",
            "sef_rfbranches": refs.get("sef_branches") or "-",
        })
    return rows


def build_amos_tables(node_logs):
    """node_logs: {node_id: log_text}. Returns (summary_rows, lte_rows, nr_rows)."""
    summary_rows, lte_rows, nr_rows = [], [], []
    for node_id, text in node_logs.items():
        if not text:
            continue
        summary_rows.append(build_node_summary(node_id, text))
        lte_rows += build_lte_cell_rows(node_id, text)
        nr_rows += build_nr_cell_rows(node_id, text)
    return summary_rows, lte_rows, nr_rows
