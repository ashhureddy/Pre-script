"""
Pre vs Post — Audit tab's node-level and cell-level diff. Python port of
QUICKIX_Pre-Script_Validation.html's runAudit() / renderNodeAudit() /
compareCellLevel() / compareNRCellLevel(), cross-checked against the
extracted HTML source function-by-function (see comments below each rule
citing the exact HTML behavior it mirrors).

Cell matching for the LTE/5G tables uses cell-name SUFFIX matching
(everything after the first '_'), same as the HTML's getSuffix()/getPrefix()
— deliberately NOT this project's own sow_analysis.classify_carriers()
signal, because a sector move changes the node PREFIX by definition, so
suffix matching is the only way to still pair a moved cell with its old Pre
values for the field-level diff. sow_analysis's CIQ-sheet-based signal
remains the right source for engineer_comments.py's narrative, which is a
different question ("what moved, per the CIQ's own bookkeeping") than this
module's ("show me every Pre value beside its Post value").

One HTML feature is deliberately NOT ported: the "Dual Link in AMOS, Single
Link in CIQ" comment addendum. It depends on a Pre-side RadioPort (DATA1/
DATA2) signal that does not exist anywhere in this project's confirmed
kget-all extraction set — porting it would mean guessing at a command this
project has never verified against a real log. Flagged here rather than
silently applied to avoid manufacturing a false Link comparison. Everything
else (row classification, field-level color coding, RRU swap detection) is
implemented as extracted below.
"""
import band_labels as bl
import checks_sector as cs
import ciq_edp_reader as cer
import pre_cell_inventory as pci
import pre_extract as pe


def _nz(v):
    """Same as the HTML's nz(): None/undefined -> '', otherwise unchanged —
    used before string comparison so None and "" compare equal."""
    return "" if v is None else v


def _get_suffix(cell_name):
    """getSuffix(): everything after the first '_', upper-cased — this is
    the sector+carrier part of a cell name, stable across a node rename or
    sector move (e.g. 'HXL00147_7A_1' -> '7A_1')."""
    if not cell_name:
        return ""
    parts = str(cell_name).split("_")
    return "_".join(parts[1:]).strip().upper()


def _get_prefix(cell_name):
    """getPrefix(): the node-name part before the first '_', upper-cased."""
    return str(cell_name or "").split("_")[0].upper()


def _norm(v):
    return str(v or "").strip().upper()


def _norm_bb(v):
    """normBB(): pulls the first 3-4 digit run out of a board string, so
    'CXP9024418/16_R17C21' and '5216' compare on the board NUMBER only."""
    import re
    s = str(v or "").strip().upper()
    m = re.search(r'\d{3,4}', s)
    return m.group(0) if m else s


def build_node_pre_post(pre_summary_rows, ciq_node_rows, amos_sa_nsa_by_node):
    """pre_summary_rows: amos_view.build_amos_tables()'s summary_rows (one
    dict per Pre node, with 'node' and 'sw_package').
    ciq_node_rows: ciq_view.build_node_integration()'s output (one dict per
    CIQ node, with 'node' and 'bb_type').
    amos_sa_nsa_by_node: {node: sa_nsa_status string} from the same Pre
    summary rows, passed separately so a node showing 'LTE Only' (this
    project's own value; the HTML's own SA/NSA is always SA/NSA/'-') still
    displays sensibly.

    Returns a list of {node, status, type, ptp, sa_nsa} rows: type is one of
    'change'/'nochange'/'delete'/'new', matching the HTML's row classes for
    color coding (see PRE_POST_ROW_COLORS in the caller)."""
    pre_by_node = {_norm(r["node"].split(" / ")[0]): r for r in pre_summary_rows}
    ciq_by_node = {_norm(r["node"]): r for r in ciq_node_rows}

    result = []
    for key, p in pre_by_node.items():
        pre_bb = p.get("sw_package")
        if key in ciq_by_node:
            fin_bb = ciq_by_node[key].get("bb_type")
            changed = _norm_bb(pre_bb) != _norm_bb(fin_bb)
            status = f"Board Changed: {pre_bb} \u2192 {fin_bb}" if changed else "No Board Change"
            row_type = "change" if changed else "nochange"
        else:
            status, row_type = "Node Deleted", "delete"
        sa_nsa = amos_sa_nsa_by_node.get(p["node"], "-")
        result.append({
            "node": p["node"], "status": status, "type": row_type,
            "ptp": "-",  # HTML itself shows '-' here too (title="Not yet derived from logs")
            "sa_nsa": "\u2014" if row_type == "delete" else sa_nsa,
        })

    for key, c in ciq_by_node.items():
        if key not in pre_by_node:
            result.append({"node": c["node"], "status": "Newly Adding Node", "type": "new",
                            "ptp": "-", "sa_nsa": "-"})
    return result


def _cmp(pre, post, is_rru=False):
    """cmp(): returns (display_text, is_match). Callers color the cell
    green/red from is_match, same as the HTML's cmp()->tdFromCmp() pipeline,
    and append '(Swap)' to RRU-model comparisons that mismatch."""
    p, q = str(_nz(pre)).strip(), str(_nz(post)).strip()
    is_match = (p == q) or (not p and not q)
    text = f"{p or '-'} | {q or '-'}"
    if is_rru and p and q and p != q:
        text += " (Swap)"
    return text, is_match


def _cmp_sector_id(pre, post):
    """cmpSectorId(): AMOS gives a raw SectorCarrier index ('1'); CIQ gives a
    compound sectorId ('1_1'). They match when the AMOS value equals the
    part of the CIQ value before the underscore — same rule as the HTML,
    otherwise a real match would show red."""
    p, q = str(pre or "").strip(), str(post or "").strip()
    q_prefix = q.split("_")[0]
    is_match = (p == q) or (p == q_prefix) or (not p and not q)
    return f"{p or '-'} | {q or '-'}", is_match


def _amos_lte_index(node_logs_text):
    """Node logs -> flat list of Pre LTE cell dicts with the exact field
    names compareCellLevel() expects (Cell/SC/CellID/TAC/BW/EARFCN_DL/
    EARFCN_UL/Pwr/TX/RX/Model), built from this project's own confirmed
    extraction functions rather than re-deriving them."""
    flat = []
    for node_id, text in (node_logs_text or {}).items():
        cells = [c for c in pci.extract_pre_cells_for_node(text) if not bl.is_5g_cell(c)]
        params = pe.extract_lte_sector_params(text)
        cfg = cs._extract_sector_config(text)
        radio_by_cell = pe.extract_cell_to_radio(text)
        sc_by_cell = _extract_sector_carrier_index(text)
        for cell in cells:
            p = params.get(cell, {})
            c = cfg.get(cell, {})
            flat.append({
                "Cell": cell, "Node": node_id,
                "SC": sc_by_cell.get(cell, ""),
                "CellID": p.get("cellId", ""), "TAC": p.get("tac", ""),
                "BW": p.get("dlChannelBandwidth", ""),
                "EARFCN_DL": p.get("earfcndl", ""), "EARFCN_UL": p.get("earfcnul", ""),
                "Pwr": c.get("power", ""), "TX": c.get("tx", ""), "RX": c.get("rx", ""),
                "Model": pe._short_radio_name(radio_by_cell.get(cell)) or "",
            })
    return flat


def _amos_nr_index(node_logs_text):
    """Same as _amos_lte_index but for 5G — CellID/DL/UL/BW_DL/BW_UL/Pwr/
    SSB/Model, matching compareNRCellLevel()'s expected fields."""
    flat = []
    for node_id, text in (node_logs_text or {}).items():
        cells = [c for c in pci.extract_pre_cells_for_node(text) if bl.is_5g_cell(c)]
        params = pe.extract_5g_sector_params_from_text(text)
        cfg = cs._extract_sector_config_5g(text)
        used = pe.extract_nr_used_antennas(text)
        radio_by_cell = pe.extract_cell_to_radio(text)
        for cell in cells:
            p = params.get(cell, {})
            c = cfg.get(cell, {})
            u = used.get(cell, {})
            flat.append({
                "Cell": cell, "Node": node_id,
                "CellID": p.get("cellLocalId", ""),
                "DL": p.get("arfcnDL", ""), "UL": p.get("arfcnUL", ""),
                "BW_DL": p.get("bSChannelBwDL", ""), "BW_UL": p.get("bSChannelBwUL", ""),
                "Pwr": c.get("power", ""), "SSB": p.get("ssbFrequency", ""),
                "Model": pe._short_radio_name(radio_by_cell.get(cell)) or "",
            })
    return flat


def _extract_sector_carrier_index(text):
    """The raw SectorCarrier index (e.g. '1', '7_1') per cell — same source
    amos_view._extract_sector_carrier_numbers() already reads; duplicated
    here as a thin wrapper so this module doesn't reach into amos_view.py's
    private helper across module boundaries."""
    import amos_view as av
    return av._extract_sector_carrier_numbers(text)


def compare_lte_cell_level(node_logs_text, ciq_wb):
    """Returns a list of row dicts, one per LTE cell in CIQ plus any
    Pre-only cell CIQ no longer has (Cell Deleted). Cross-verified against
    compareCellLevel(): for every CIQ cell, find its Pre match by SUFFIX; if
    none, 'Newly Adding Cell'; if the PREFIX also changed, 'Sector moved:
    X -> Y'; otherwise 'No Sector Movement'. Every field is a (text,
    is_match) pair from _cmp()/_cmp_sector_id(), ready for color rendering."""
    amos = _amos_lte_index(node_logs_text)
    ciq_rows = cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]) if "eUtran Parameters" in ciq_wb.sheetnames else []
    ciq_suffix_set = {_get_suffix(r.get("EutranCellFDDId") or r.get("Cell") or "") for r in ciq_rows}

    result = []
    for c in ciq_rows:
        cell_full = c.get("EutranCellFDDId") or c.get("Cell") or ""
        final_pfx, final_sfx = _get_prefix(cell_full), _get_suffix(cell_full)
        match = next((a for a in amos if _get_suffix(a["Cell"]) == final_sfx), None)
        if not match:
            comment, row_type = "Newly Adding Cell", "new"
        else:
            pre_pfx = _get_prefix(match["Cell"])
            comment, row_type = ("No Sector Movement", "nochange") if pre_pfx == final_pfx \
                else (f"Sector moved: {pre_pfx} -> {final_pfx}", "change")

        ciq_port = c.get("DUS / XMU Port")
        sc_text, sc_ok = _cmp_sector_id(_nz(match["SC"]) if match else "", c.get("sectorId"))
        cellid_text, cellid_ok = _cmp(_nz(match["CellID"]) if match else "", c.get("cellId"))
        tac_text, tac_ok = _cmp(_nz(match["TAC"]) if match else "", c.get("tac"))
        bw_text, bw_ok = _cmp(_nz(match["BW"]) if match else "", c.get("dlChannelBandwidth"))
        dl_text, dl_ok = _cmp(_nz(match["EARFCN_DL"]) if match else "", c.get("earfcnDl"))
        ul_text, ul_ok = _cmp(_nz(match["EARFCN_UL"]) if match else "", c.get("earfcnUl"))
        pwr_text, pwr_ok = _cmp(_nz(match["Pwr"]) if match else "", c.get("configuredOutputPower"))
        tx_text, tx_ok = _cmp(_nz(match["TX"]) if match else "", c.get("noOfTxAntennas"))
        rx_text, rx_ok = _cmp(_nz(match["RX"]) if match else "", c.get("noOfRxAntennas"))
        rru_text, rru_ok = _cmp(_nz(match["Model"]) if match else "", c.get("RRU type"), is_rru=True)

        result.append({
            "node": c.get("Node") or final_pfx, "cell": cell_full,
            "sc": sc_text, "_sc_ok": sc_ok, "cellid": cellid_text, "_cellid_ok": cellid_ok,
            "tac": tac_text, "_tac_ok": tac_ok, "bw": bw_text, "_bw_ok": bw_ok,
            "dl": dl_text, "_dl_ok": dl_ok, "ul": ul_text, "_ul_ok": ul_ok,
            "power": pwr_text, "_power_ok": pwr_ok, "tx": tx_text, "_tx_ok": tx_ok,
            "rx": rx_text, "_rx_ok": rx_ok, "rru": rru_text, "_rru_ok": rru_ok,
            "link": "-", "comment": comment, "row_type": row_type,
        })

    for a in amos:
        if _get_suffix(a["Cell"]) not in ciq_suffix_set:
            result.append({
                "node": a.get("Node", "-"), "cell": a["Cell"],
                "sc": "-", "_sc_ok": None, "cellid": "-", "_cellid_ok": None,
                "tac": "-", "_tac_ok": None, "bw": "-", "_bw_ok": None,
                "dl": "-", "_dl_ok": None, "ul": "-", "_ul_ok": None,
                "power": "-", "_power_ok": None, "tx": "-", "_tx_ok": None,
                "rx": "-", "_rx_ok": None, "rru": "-", "_rru_ok": None,
                "link": "-", "comment": "Cell Deleted", "row_type": "delete",
            })
    return result


def compare_nr_cell_level(node_logs_text, ciq_wb):
    """5G equivalent of compare_lte_cell_level(), cross-verified against
    compareNRCellLevel(). NR suffix matching in the HTML normalizes via
    normNR()/getNRSuffix(); this project's NR cell names use the same
    '<prefix>_<sector-suffix>' shape as LTE ones (confirmed against real
    logs earlier in this project), so _get_suffix()/_get_prefix() apply
    unchanged rather than needing a separate NR-specific normalizer."""
    amos = _amos_nr_index(node_logs_text)
    ciq_rows = cer.sheet_rows_as_dicts(ciq_wb["5G Info"]) if "5G Info" in ciq_wb.sheetnames else []
    ciq_suffix_set = {_get_suffix(r.get("NRCellDU") or "") for r in ciq_rows}

    result = []
    for c in ciq_rows:
        cell_full = c.get("NRCellDU") or ""
        final_pfx, final_sfx = _get_prefix(cell_full), _get_suffix(cell_full)
        match = next((a for a in amos if _get_suffix(a["Cell"]) == final_sfx), None)
        if not match:
            comment, row_type = "Newly Adding NR Cell", "new"
        else:
            pre_pfx = _get_prefix(match["Cell"])
            comment, row_type = ("No Sector Movement", "nochange") if pre_pfx == final_pfx \
                else (f"NR Sector moved: {pre_pfx} -> {final_pfx}", "change")

        cellid_text, cellid_ok = _cmp(_nz(match["CellID"]) if match else "", c.get("cellLocalId"))
        dl_text, dl_ok = _cmp(_nz(match["DL"]) if match else "", c.get("arfcnDL"))
        ul_text, ul_ok = _cmp(_nz(match["UL"]) if match else "", c.get("arfcnUL"))
        bwdl_text, bwdl_ok = _cmp(_nz(match["BW_DL"]) if match else "", c.get("bSChannelBwDL"))
        bwul_text, bwul_ok = _cmp(_nz(match["BW_UL"]) if match else "", c.get("bSChannelBwUL"))
        pwr_text, pwr_ok = _cmp(_nz(match["Pwr"]) if match else "", c.get("configuredMaxTxPower"))
        ssb_text, ssb_ok = _cmp(_nz(match["SSB"]) if match else "", c.get("ssbFrequency"))
        rru_text, rru_ok = _cmp(_nz(match["Model"]) if match else "", c.get("RRU Type") or c.get("RRU type"), is_rru=True)

        result.append({
            "node": c.get("Node") or final_pfx, "cell": cell_full,
            "cellid": cellid_text, "_cellid_ok": cellid_ok, "dl": dl_text, "_dl_ok": dl_ok,
            "ul": ul_text, "_ul_ok": ul_ok, "bw_dl": bwdl_text, "_bw_dl_ok": bwdl_ok,
            "bw_ul": bwul_text, "_bw_ul_ok": bwul_ok, "power": pwr_text, "_power_ok": pwr_ok,
            "ssb": ssb_text, "_ssb_ok": ssb_ok, "rru": rru_text, "_rru_ok": rru_ok,
            "link": "-", "comment": comment, "row_type": row_type,
        })

    for a in amos:
        if _get_suffix(a["Cell"]) not in ciq_suffix_set:
            result.append({
                "node": a.get("Node", "-"), "cell": a["Cell"],
                "cellid": "-", "_cellid_ok": None, "dl": "-", "_dl_ok": None,
                "ul": "-", "_ul_ok": None, "bw_dl": "-", "_bw_dl_ok": None,
                "bw_ul": "-", "_bw_ul_ok": None, "power": "-", "_power_ok": None,
                "ssb": "-", "_ssb_ok": None, "rru": "-", "_rru_ok": None,
                "link": "-", "comment": "NR Cell Deleted", "row_type": "delete",
            })
    return result


def summarize_rows(rows):
    """New/Deleted/Moved/No Change counts for the badge row, matching the
    HTML's own New/Deleted/Moved/No Change pill counts."""
    return {
        "new": sum(1 for r in rows if r["row_type"] == "new"),
        "deleted": sum(1 for r in rows if r["row_type"] == "delete"),
        "moved": sum(1 for r in rows if r["row_type"] == "change"),
        "nochange": sum(1 for r in rows if r["row_type"] == "nochange"),
    }
