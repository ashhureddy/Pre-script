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
    """Node, Cell, Band, Sector, RRU, TX, RX, Power, Sharing Radio."""
    cells = sorted(c for c in pci.extract_pre_cells_for_node(text) if not bl.is_5g_cell(c))
    radio_by_cell = pe.extract_cell_to_radio(text)
    cfg_by_cell = cs._extract_sector_config(text)

    # Sharing radio: same RRU + same band serving DIFFERENT sector letters —
    # same definition as QUICKIX's radioBandMap (cross-sector share only; a
    # single radio carrying multiple carriers on the SAME sector is normal).
    radio_band_map = {}
    for cell in cells:
        band, sector = bl.band_label(cell)
        rru = radio_by_cell.get(cell)
        if not (band and sector and rru):
            continue
        key = (rru, band)
        radio_band_map.setdefault(key, {}).setdefault(sector, set()).add(cell)

    rows = []
    for cell in cells:
        band, sector = bl.band_label(cell)
        rru = radio_by_cell.get(cell, "-")
        cfg = cfg_by_cell.get(cell)
        sharing = "No"
        if band and sector and rru != "-":
            by_sector = radio_band_map.get((rru, band), {})
            shared = {c for sec, cs_ in by_sector.items() if sec != sector for c in cs_}
            if shared:
                sharing = ", ".join(sorted(shared))
        rows.append({
            "node": node_id, "cell": cell, "band": band or "-", "sector": sector or "-",
            "rru": rru, "tx": cfg["tx"] if cfg else "-", "rx": cfg["rx"] if cfg else "-",
            "power": cfg["power"] if cfg else "-", "sharing_radio": sharing,
            "pre_existing_dss": "Not available from Pre kget-all logs",
        })
    return rows


def build_nr_cell_rows(node_id, text):
    """Node, Cell, Band, Sector, RRU, TX, RX, Power."""
    cells = sorted(c for c in pci.extract_pre_cells_for_node(text) if bl.is_5g_cell(c))
    radio_by_cell = pe.extract_cell_to_radio(text)
    cfg_by_cell = cs._extract_sector_config_5g(text)

    rows = []
    for cell in cells:
        band, sector = bl.band_label(cell)
        cfg = cfg_by_cell.get(cell)
        rows.append({
            "node": node_id, "cell": cell, "band": band or "-", "sector": sector or "-",
            "rru": radio_by_cell.get(cell, "-"), "tx": cfg["tx"] if cfg else "-",
            "rx": cfg["rx"] if cfg else "-", "power": cfg["power"] if cfg else "-",
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
