"""
Streamlit app.py — QUICKIX Pre-Script Validation (Streamlit port)

Single input page (CIQ + EDP required, RFDS PDF + Pre kget-all logs
optional) -> "Run Validation" runs the full pipeline ONCE and stores it in
session_state -> tabbed results view (RFDS Validation / Audit / EDP
Validator / RET Antenna Checklist / Consolidated Report), every tab reads
from that one stored run. "New Validation Run" clears state and returns to
the input page. This matches QUICKIX_Pre-Script_Validation.html's own
flow: inputs are on the first page only, "Run Validation" swaps to the
tabbed results, and there is no way back to the inputs except starting a
new run.

Nothing here invents new validation logic — every check is an existing,
already-confirmed function from checks_node.py / checks_sector.py /
rfds_extract.py / pre_extract.py / ciq_edp_reader.py / rrnrbl_checklist.py /
ciq_view.py / amos_view.py / antenna_resolve.py. Fixes/integrations versus
the prior version of this file:

  - run_validation.run() returns 11 values; this file now unpacks all 11
    (was silently truncated to 7, which crashed the Consolidated Report
    and Checklist buttons the moment they were used).
  - RET Antenna Checklist and the RRNRBL Checklist were conflated onto one
    tab. RRNRBL now lives only inside Consolidated Report (matching the
    HTML tool's layout); RET Antenna Checklist is its own tab and stays an
    empty placeholder while that feature is on hold.
  - ciq_view.py and amos_view.py (present in the repo, never imported
    anywhere) now drive the CIQ Checks / Pre checks (AMOS) tables — they
    are the purpose-built table builders for exactly this, replacing
    cruder inline table assembly that duplicated their job.
  - edp_checks.py is intentionally NOT used: it is an earlier, superseded
    EDP Validator with cross-node/"unexpected nodes" sections that were
    explicitly dropped from scope; rrnrbl_checklist.py's simpler per-check
    functions are the current design and are what's wired in everywhere.
  - Every comparison table is now rendered with a coloured, bordered HTML
    table (MATCH/PASS green, MISMATCH/FAIL red, INFO blue, MANUAL amber,
    SKIPPED/unknown grey) instead of a plain st.dataframe — same palette
    the PDF report and the RRNRBL checklist already use, so the look is
    consistent across every surface this tool produces.
"""
import os
import re
import html
import tempfile

import streamlit as st

import ciq_edp_reader as cer
import checks_sector as cs
import rfds_extract as rf
import pre_extract as pe
import run_validation as rv
import rrnrbl_checklist as rc
import antenna_resolve as ar
import warnings_text as wt
import ciq_view as cv
import amos_view as av
from rfds_verification_summary import build_rfds_verification_summary
from engineer_comments import build_engineer_comments, extract_bands_from_comments, extract_nodes_from_audit
from cr_description import build_cr_description, build_radio_ret_email

st.set_page_config(page_title="QUICK IX", layout="wide", page_icon="📡")

# ══════════════════════════════════════════════════════════════════════
# Styling — ported from the QUICKIX report-feature branch's own CSS
# (sticky navy topbar with MAS/TEC logo + credit, gradient buttons, white
# bordered cards for st.container(border=True)) so both features share one
# visual language ahead of being combined, plus this file's own bordered/
# colour-coded HTML table renderer for every comparison table. Table
# colours match the RRNRBL checklist's palette and the PDF report's header
# banner (navy #101F90 / #dde3f7), so a status reads the same everywhere.
# ══════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
.stApp { background: linear-gradient(180deg, #eef3fa 0%, #f7f9fc 100%); }
.block-container { padding-top: 1rem; padding-left: 2rem; padding-right: 2rem; max-width: 100%; }
.qkx-topbar {
  position: sticky; top: 0; z-index: 999;
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.9rem 1.75rem; margin: -1rem -1rem 1.5rem -1rem;
  background: linear-gradient(90deg, #011b36 0%, #012a4e 100%);
  border-bottom: 1px solid rgba(255,91,36,0.55);
  box-shadow: 0 4px 18px rgba(0,0,0,0.2);
}
.qkx-topbar .qkx-logo { font-size: 1.3rem; font-weight: 900; color: #ffffff; letter-spacing: 1px; }
.qkx-topbar .qkx-logo span { color: #ffffff; }
.qkx-topbar .qkx-title { font-size: 0.95rem; color: #cfe0f5; margin-left: 14px; font-weight: 600; }
.qkx-topbar .qkx-credit { font-size: 0.78rem; color: #cfe0f5; text-align: right; line-height: 1.3; }
div[data-testid="stButton"] button {
  border-radius: 10px; font-weight: 700; border: 1.5px solid #013a6b;
  background: linear-gradient(135deg, #024ea4, #013a6b); color: #ffffff;
  box-shadow: 0 3px 8px rgba(1,42,78,0.25);
  transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
}
div[data-testid="stButton"] button:hover {
  border-color: #ff5b24; color: #ffffff; transform: translateY(-1px);
  box-shadow: 0 6px 14px rgba(255,91,36,0.35);
}
div[data-testid="stButton"] button:active { transform: translateY(0); }
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: #ffffff !important; border: 1px solid #dde5ef !important;
  border-radius: 12px !important; box-shadow: 0 2px 10px rgba(1,42,78,0.06);
  padding: 4px 2px;
}
.stTabs [data-baseweb="tab-list"] {
  gap: 4px; border-bottom: 2px solid #dde5ef; padding-bottom: 0;
}
.stTabs [data-baseweb="tab"] {
  font-weight: 700; font-size: 13.5px; color:#475569;
  padding: 8px 16px; border-radius: 8px 8px 0 0;
}
.stTabs [aria-selected="true"] {
  color:#101F90 !important; background:#eef1fb;
  box-shadow: inset 0 -3px 0 #101F90;
}
div[data-testid="stExpander"] details {
  border:1px solid #dde5ef !important; border-radius:10px !important;
  background:#fff; box-shadow:0 2px 8px rgba(1,42,78,.05); margin-bottom:10px;
}
div[data-testid="stExpander"] summary {
  font-weight:700 !important; font-size:13.5px !important; color:#101F90 !important;
  padding:11px 14px !important;
}
div[data-testid="stExpander"] summary:hover { background:#f4f7fc; border-radius:10px; }
.qkx-stat {
  text-align:center; border:1px solid #dde5ef; border-radius:10px; padding:10px 8px;
  background:#fff; box-shadow:0 2px 8px rgba(1,42,78,.05); font-size:12.5px;
}
.qkx-stat b { color:#64748b; font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; }
.qkx-table-wrap {
  overflow-x:auto; border:1px solid #dde5ef; border-radius:0 0 10px 10px;
  margin: 0 0 22px 0; border-top:none; box-shadow:0 2px 8px rgba(1,42,78,.05);
}
.qkx-table { width:100%; border-collapse:collapse; font-size:12.8px; line-height:1.35; }
.qkx-table th {
  background:#101F90; color:#ffffff; font-weight:700; text-align:left;
  padding:8px 11px; border:none; border-right:1px solid rgba(255,255,255,.14);
  white-space:nowrap; font-size:11.5px; letter-spacing:.03em; text-transform:uppercase;
  position:sticky; top:0;
}
.qkx-table td { padding:7px 11px; border-bottom:1px solid #eef1f6; vertical-align:top; }
.qkx-table tbody tr:hover td { background:rgba(16,31,144,.04); }
.qkx-table.qkx-zebra tbody tr:nth-child(even) td { background:#f8fafc; }
.qkx-table.qkx-zebra tbody tr:hover td { background:rgba(16,31,144,.06); }
.qkx-table td.qkx-group-start, .qkx-table th.qkx-group-start { border-left:2px solid #94a3b8; }
.qkx-empty {
  padding:16px; color:#64748b; font-style:italic; font-size:13px;
  background:#fff; border:1px dashed #cbd5e1; border-radius:10px; text-align:center;
}
.qkx-section-title {
  font-weight:700; font-size:13.5px; color:#fff; margin: 22px 0 0 0;
  padding:10px 14px; border:none;
  border-radius:10px 10px 0 0;
  background: linear-gradient(90deg, #101F90 0%, #1e3a8a 100%);
  box-shadow:0 2px 6px rgba(16,31,144,.16);
}
.qkx-warn-line {
  padding:8px 12px; margin-bottom:6px; border-radius:8px;
  background:#fff5f5; color:#991b1b; font-size:12.5px;
  border:1px solid #fecaca; border-left:3px solid #dc2626;
}
.qkx-cat-banner {
  background: linear-gradient(90deg, #101F90 0%, #1e3a8a 100%); color:#fff;
  font-weight:700; font-size:13px; letter-spacing:.02em;
  padding:9px 14px; border-radius:8px 8px 0 0; margin-top:22px;
  display:flex; justify-content:space-between; align-items:center;
  box-shadow:0 2px 6px rgba(16,31,144,.18);
}
.qkx-cat-counts { display:flex; gap:6px; align-items:center; }
.qkx-cat-count {
  font-size:10.5px; font-weight:700; padding:2px 8px; border-radius:999px;
  background:rgba(255,255,255,.16); color:#fff; white-space:nowrap;
}
.qkx-cat-count.ok   { background:#059669; }
.qkx-cat-count.bad  { background:#dc2626; }
.qkx-cat-count.man  { background:#d97706; }
.qkx-cat-count.na   { background:rgba(255,255,255,.22); }

/* Status chip — every check row carries one, so a status is readable at a
   glance instead of relying on a pale row background alone. */
.qkx-chip {
  display:inline-block; font-size:10px; font-weight:800; letter-spacing:.05em;
  padding:3px 9px; border-radius:999px; text-transform:uppercase;
  white-space:nowrap; border:1px solid transparent;
}
.qkx-chip.match    { background:#d1fae5; color:#065f46; border-color:#6ee7b7; }
.qkx-chip.mismatch { background:#fee2e2; color:#991b1b; border-color:#fca5a5; }
.qkx-chip.manual   { background:#fef3c7; color:#92400e; border-color:#fcd34d; }
.qkx-chip.info     { background:#dbeafe; color:#1d4ed8; border-color:#93c5fd; }
.qkx-chip.unknown  { background:#f1f5f9; color:#64748b; border-color:#cbd5e1; }

.qkx-count-pill {
  font-size:11.5px; color:#334155; margin-right:6px;
  background:#fff; border:1px solid #dde5ef; border-radius:999px;
  padding:4px 11px; display:inline-block; margin-bottom:4px;
}
.qkx-title-badge {
  background:#101F90; color:#fff; font-weight:700; font-size:11px;
  padding:3px 10px; border-radius:999px; white-space:nowrap;
}
.qkx-manual-label {
  font-size:13px; font-weight:600; color:#0f1720; margin-bottom:6px;
  display:flex; align-items:center; gap:8px;
}
.qkx-manual-tag {
  background:#fef3c7; color:#92400e; font-size:9.5px; font-weight:800;
  padding:2px 8px; border-radius:999px; letter-spacing:.05em;
  border:1px solid #fcd34d;
}
.qkx-manual-item {
  font-size:12.8px; font-weight:600; color:#0f1720;
  padding:2px 0 6px 0;
}
.qkx-manual-detail { font-size:11.5px; color:#64748b; font-weight:400; }
.qkx-sub-header {
  font-size:12px; font-weight:800; color:#1e3a8a; text-transform:uppercase;
  letter-spacing:.06em; margin:14px 0 6px 0;
  border-left:3px solid #101F90; padding:3px 0 3px 9px;
  background:linear-gradient(90deg,#eef1fb 0%,rgba(238,241,251,0) 100%);
}
/* Spreadsheet-style grid for the RRNRBL checklist: real vertical column
   borders on every cell (qkx-table's default only has horizontal row
   borders), so merged Category/Sub-section cells (via rowspan) read as
   genuine grouped spreadsheet cells rather than a plain list. */
.qkx-grid td, .qkx-grid th {
  border-right:1px solid #dde5ef;
}
.qkx-grid td:first-child, .qkx-grid td:nth-child(2) {
  border-right:2px solid #cbd5e1;
}
.qkx-grid-wrap { margin-bottom:16px; }
</style>
<div class="qkx-topbar">
  <div><span class="qkx-logo">MAS<span>TEC</span></span><span class="qkx-title">QUICK IX — Pre-Script Validation</span></div>
  <div class="qkx-credit">Made by <b>AKSHATHA KALLUR</b><br>Powered by <b>MASTEC</b></div>
</div>
""", unsafe_allow_html=True)

STATUS_COLORS = {
    "MATCH": ("#065f46", "#d1fae5"), "match": ("#065f46", "#d1fae5"), "PASS": ("#065f46", "#d1fae5"),
    "MISMATCH": ("#991b1b", "#fee2e2"), "mismatch": ("#991b1b", "#fee2e2"), "FAIL": ("#991b1b", "#fee2e2"),
    "SKIPPED": ("#64748b", "#f1f5f9"), "unknown": ("#64748b", "#f1f5f9"),
    "na": ("#64748b", "#f1f5f9"), "N/A": ("#64748b", "#f1f5f9"),
    "INFO": ("#1d4ed8", "#dbeafe"), "info": ("#1d4ed8", "#dbeafe"),
    "manual": ("#92400e", "#fef3c7"), "MANUAL": ("#92400e", "#fef3c7"), "EXPECTED": ("#92400e", "#fef3c7"),
}
DEFAULT_COLOR = ("#334155", "#ffffff")
STATUS_LABEL = {"match": "match", "mismatch": "mismatch", "manual": "manual",
                "unknown": "no data", "na": "n/a", "info": "info"}


def esc(v):
    return html.escape("" if v is None else str(v))


def render_table(rows, columns=None, status_key="status", empty_msg="No data."):
    """rows: list[dict]. Bordered HTML table, each row's background/text
    colour driven by rows[i][status_key]. columns: optional [(key,label),
    ...] order; defaults to the first row's own key order. status_key=None
    disables colouring (plain bordered table)."""
    if not rows:
        return f'<div class="qkx-empty">{esc(empty_msg)}</div>'
    if columns is None:
        columns = [(k, k.replace("_", " ").title()) for k in rows[0].keys()]
    head = "".join(f"<th>{esc(label)}</th>" for _, label in columns)
    body = []
    for r in rows:
        color, bg = STATUS_COLORS.get(str(r.get(status_key, "")), DEFAULT_COLOR) if status_key else DEFAULT_COLOR
        cells = "".join(f"<td>{esc(r.get(k, ''))}</td>" for k, _ in columns)
        body.append(f'<tr style="background:{bg};color:{color};">{cells}</tr>')
    # Zebra striping only on uncoloured tables: a `td` background paints over
    # the row's inline `tr` background, so applying it globally would wash out
    # every status colour.
    zebra = " qkx-zebra" if not status_key else ""
    return (f'<div class="qkx-table-wrap"><table class="qkx-table{zebra}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def render_table_with_comments(rows, columns, status_key="status", note_key="note", bad_value="MISMATCH"):
    """Row background/text colour driven by rows[i][status_key] (same
    green/red/etc. palette as every other table), ending in one 'Comments'
    column holding the note text — no separate Status/Note columns."""
    if not rows:
        return '<div class="qkx-empty">No data.</div>'
    head = "".join(f"<th>{esc(label)}</th>" for _, label in columns) + '<th style="min-width:170px;">Comments</th>'
    body = []
    for r in rows:
        color, bg = STATUS_COLORS.get(str(r.get(status_key, "")), DEFAULT_COLOR)
        cells = "".join(f"<td>{esc(r.get(k, ''))}</td>" for k, _ in columns)
        cells += f'<td style="min-width:170px;">{esc(r.get(note_key, ""))}</td>'
        body.append(f'<tr style="background:{bg};color:{color};">{cells}</tr>')
    return (f'<div class="qkx-table-wrap"><table class="qkx-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


# ── Pre vs Post row-type palette — mirrors QUICKIX HTML's .new/.delete/
# .change/.nochange row classes exactly (background colours match the
# screenshots: pale green=new, pale red=delete, pale amber=change/moved,
# plain white=nochange). ──
PRE_POST_ROW_COLORS = {
    "new": "#d1fae5", "delete": "#fee2e2", "change": "#fef3c7", "nochange": "#ffffff",
}


def render_node_pre_post_table(rows):
    """Node / Status / PTP, whole-row background from row['type']."""
    if not rows:
        return '<div class="qkx-empty">Run validation with Pre logs and a CIQ to populate this.</div>'
    head = "".join(f"<th>{h}</th>" for h in ("Node", "Status", "PTP"))
    body = []
    for r in rows:
        bg = PRE_POST_ROW_COLORS.get(r["type"], "#ffffff")
        status_color = "#b45309" if r["type"] == "change" else "#0f1720"
        ptp_color = "#991b1b" if r.get("_ptp_flag") else "#0f1720"
        body.append(
            f'<tr style="background:{bg};"><td>{esc(r["node"])}</td>'
            f'<td style="color:{status_color};font-weight:600;">{esc(r["status"])}</td>'
            f'<td style="color:{ptp_color};font-weight:600;">{esc(r["ptp"])}</td></tr>'
        )
    return (f'<div class="qkx-table-wrap"><table class="qkx-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _pre_post_summary_pills(summary):
    """New/Deleted/Moved/No Change count pills, same labels+order as the
    HTML's own badge row above each LTE/5G Pre vs Post table."""
    items = [("new", "New", "#059669"), ("deleted", "Deleted", "#dc2626"),
             ("moved", "Moved", "#b45309"), ("nochange", "No Change", "#334155")]
    return "".join(
        f'<span class="qkx-count-pill"><b style="color:{color}">{summary.get(key, 0)}</b> {label}</span>'
        for key, label, color in items
    )


def render_cell_pre_post_table(rows, field_columns):
    """field_columns: list of (value_key, ok_key, label) for the PRE|POST
    comparison columns (e.g. ('sc','_sc_ok','SC')) — each cell coloured
    green/red from its own ok flag (None -> plain, used for Deleted rows
    where there's nothing to compare). Row background still follows
    row_type like the node table, but lighter, since the HTML also tints
    New/Deleted/Moved rows while still colouring individual mismatched
    fields red within them."""
    if not rows:
        return '<div class="qkx-empty">No data.</div>'
    head = "".join(f"<th>{h}</th>" for h in ("Node", "Cell")) \
        + "".join(f"<th>{esc(label)}</th>" for _, _, label in field_columns) \
        + "".join(f"<th>{h}</th>" for h in ("Link", "Comments"))
    body = []
    for r in rows:
        row_bg = PRE_POST_ROW_COLORS.get(r["row_type"], "#ffffff")
        cells = f"<td>{esc(r['node'])}</td><td><b>{esc(r['cell'])}</b></td>"
        for val_key, ok_key, _ in field_columns:
            ok = r.get(ok_key)
            if ok is None:
                cells += f"<td>{esc(r.get(val_key, '-'))}</td>"
            else:
                color = "#059669" if ok else "#dc2626"
                cells += f'<td style="color:{color};font-weight:700;">{esc(r.get(val_key, "-"))}</td>'
        cells += f"<td>{esc(r.get('link', '-'))}</td><td>{esc(r.get('comment', ''))}</td>"
        body.append(f'<tr style="background:{row_bg};">{cells}</tr>')
    return (f'<div class="qkx-table-wrap"><table class="qkx-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _sheet_mentions_cell(ciq_wb, sheet_name, cell_id):
    """True if any cell in the given CIQ sheet contains cell_id as a
    substring anywhere — mirrors the HTML's own Antenna Info / Losses &
    Delays presence check (antenna.some(r => Object.values(r).some(v =>
    String(v).includes(cellId)))), not an RFDS lookup."""
    if not cell_id or sheet_name not in ciq_wb.sheetnames:
        return False
    for row in cer.sheet_rows_as_dicts(ciq_wb[sheet_name]):
        for v in row.values():
            if v is not None and cell_id in str(v):
                return True
    return False


def build_rfds_grouped_rows(results, ciq_wb, rfds_pages):
    """One row per cell, merging Cell verification / RRU verification /
    Antenna verification / Cell id / Antenna info / Losses & Delays /
    Warning — matches the confirmed HTML grouped-header table, including
    its exact pass/fail criteria: RRH match, antenna match-or-N/A, cell ID
    match, Antenna Info found (CIQ 'Antenna Information' sheet mentions
    the cell), and Losses & Delays found (CIQ 'Losses and Delays' sheet
    mentions the cell) unless the antenna is AIR-series, where loss data
    isn't mandatory."""
    cell_map = {}
    for r in results.get("cells_vs_rfds", []):
        cell_map.setdefault(r["cell"], {})["cv"] = r
    for r in results.get("radio_type", []):
        cell_map.setdefault(r["cell"], {})["rt"] = r
    for r in results.get("cell_id_vs_rfds", []):
        cell_map.setdefault(r["cell"], {})["ci"] = r

    # Antenna model: 'RF Inventory Details (Final)' filtered to
    # EquipmentType=='ANTENNA' — the authoritative per-antenna record
    # (Model + Linked Cells explicitly), not inferred from Sec-Pos position
    # in 'Port Level Details'. AIR-series radios have no separate antenna
    # row here (antenna is integrated into the radio) — those cells simply
    # get no RFDS antenna entry, which is correct, not a gap.
    rf_antennas = rf.extract_rf_inventory_antennas(rfds_pages) if rfds_pages is not None else {}
    ant_by_cell = {}
    if "eUtran Parameters" in ciq_wb.sheetnames:
        for r in cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]):
            cell = r.get("EutranCellFDDId")
            if not cell:
                continue
            ciq_ant = r.get("antenna model")
            ant_row = rf_antennas.get(cell)
            tier, detail = ar.resolve_antenna(ciq_ant, ant_row["model"] if ant_row else None)
            ant_by_cell[cell] = {"ciq": ciq_ant or "—", "rfds": (ant_row or {}).get("model", "NOT FOUND"),
                                  "tier": tier, "found": ant_row is not None}

    rows = []
    for cell, parts in cell_map.items():
        cv, rt, ci = parts.get("cv", {}), parts.get("rt", {}), parts.get("ci", {})
        an = ant_by_cell.get(cell, {})
        cell_status = cv.get("status", "SKIPPED")
        rru_status = rt.get("status", "SKIPPED")
        cellid_status = ci.get("status", "SKIPPED")
        ant_tier = an.get("tier")
        if not an:
            ant_status = "SKIPPED"
        elif not an.get("found"):
            ant_status = "MANUAL"  # amber — RFDS has no antenna data at all ("N/A", not a real mismatch)
        elif ant_tier in ("EXACT", "NORMALIZED", "SUFFIX", "TRUNCATED"):
            ant_status = "MATCH"
        else:
            ant_status = "MISMATCH"

        is_air = str(an.get("rfds") or "").upper().startswith("AIR") or str(an.get("ciq") or "").upper().startswith("AIR")
        ant_info_found = _sheet_mentions_cell(ciq_wb, "Antenna Information", cell)
        loss_found = _sheet_mentions_cell(ciq_wb, "Losses and Delays", cell)
        loss_mandatory = not is_air
        loss_status = "MATCH" if loss_found else ("MANUAL" if not loss_mandatory else "MISMATCH")
        loss_display = "FOUND" if loss_found else ("N/A" if not loss_mandatory else "NOT FOUND")

        warnings = []
        if cell_status == "MISMATCH":
            warnings.append("Cell mismatch")
        if rru_status == "MISMATCH":
            warnings.append("RRU mismatch")
        if ant_status == "MISMATCH":
            warnings.append("Antenna mismatch")
        if cellid_status == "MISMATCH":
            warnings.append("Cell ID mismatch")
        if not ant_info_found:
            warnings.append("Antenna Info missing")
        if loss_mandatory and not loss_found:
            warnings.append("Losses & Delays missing")

        fail = any(s == "MISMATCH" for s in (cell_status, rru_status, ant_status, cellid_status)) \
            or not ant_info_found or (loss_mandatory and not loss_found)

        rows.append({
            "node": cv.get("node") or rt.get("node") or ci.get("node") or "",
            "cell_rfds": cv.get("rfds_cell", "—"), "cell_ciq": cv.get("ciq_cell", "—") or cell, "cell_status": cell_status,
            "rru_rfds": rt.get("rfds", "—"), "rru_ciq": rt.get("ciq", "—"), "rru_status": rru_status,
            "ant_rfds": an.get("rfds", "—"), "ant_ciq": an.get("ciq", "—"), "ant_status": ant_status,
            "cellid_rfds": ci.get("rfds_rcn", "—"), "cellid_ciq": ci.get("ciq", "—"), "cellid_status": cellid_status,
            "ant_info": "FOUND" if ant_info_found else "NOT FOUND", "ant_info_status": "MATCH" if ant_info_found else "MISMATCH",
            "losses_delays": loss_display, "losses_status": loss_status,
            "warning": "; ".join(warnings) if warnings else "—",
            "overall": "FAIL" if fail else "PASS",
        })
    rows.sort(key=lambda r: r["cell_ciq"] or "")
    return rows


def render_rfds_grouped_table(rows):
    if not rows:
        return '<div class="qkx-empty">No data.</div>'

    def gcell(val, status, group_start=False):
        color, bg = STATUS_COLORS.get(status, DEFAULT_COLOR)
        cls = ' class="qkx-group-start"' if group_start else ""
        return f'<td{cls} style="background:{bg};color:{color};">{esc(val)}</td>'

    def scell(val, status, group_start=False):
        color, bg = STATUS_COLORS.get(status, DEFAULT_COLOR)
        cls = ' class="qkx-group-start"' if group_start else ""
        return f'<td{cls} style="background:{bg};color:{color};font-weight:600;">{esc(val)}</td>'

    head1 = ('<th colspan="2">Cell verification</th><th colspan="2" class="qkx-group-start">RRU verification</th>'
             '<th colspan="2" class="qkx-group-start">Antenna verification</th>'
             '<th colspan="2" class="qkx-group-start">Cell id</th>'
             '<th rowspan="2" class="qkx-group-start">Antenna info</th><th rowspan="2">Losses &amp; Delays</th>'
             '<th rowspan="2" style="min-width:160px;">Warning</th>')
    head2 = '<th>RFDS</th><th>CIQ</th>' * 4
    body = []
    for r in rows:
        warn_cell = (f'<td style="min-width:160px;color:#991b1b;font-weight:700;">{esc(r["warning"])}</td>'
                     if r["warning"] != "—" else '<td style="min-width:160px;">—</td>')
        tds = (
            gcell(r["cell_rfds"], r["cell_status"]) + gcell(r["cell_ciq"], r["cell_status"])
            + gcell(r["rru_rfds"], r["rru_status"], True) + gcell(r["rru_ciq"], r["rru_status"])
            + gcell(r["ant_rfds"], r["ant_status"], True) + gcell(r["ant_ciq"], r["ant_status"])
            + gcell(r["cellid_rfds"], r["cellid_status"], True) + gcell(r["cellid_ciq"], r["cellid_status"])
            + scell(r["ant_info"], r["ant_info_status"], True) + scell(r["losses_delays"], r["losses_status"]) + warn_cell
        )
        body.append(f"<tr>{tds}</tr>")
    return (f'<div class="qkx-table-wrap"><table class="qkx-table">'
            f'<thead><tr>{head1}</tr><tr>{head2}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def render_pre_vs_edp_pivot_table(rows):
    """Node ID + one 2-col (pre | EDP) group per Bearer/OAM field — matches
    the wide screenshot layout. rows come from
    rrnrbl_checklist.build_pre_vs_edp_pivot_rows()."""
    if not rows:
        return '<div class="qkx-empty">No data.</div>'
    groups = [("Bearer VLAN", "bearer_vlan"), ("Bearer IPv6", "bearer_ipv6"),
              ("Bearer Default Router", "bearer_router"), ("OAM VLAN", "oam_vlan"),
              ("OAM IPv6", "oam_ipv6"), ("OAM Default Router", "oam_router")]
    head1 = '<th rowspan="2">Node ID</th>' + "".join(
        f'<th colspan="2" class="qkx-group-start">{esc(label)}</th>' for label, _ in groups)
    head2 = "".join('<th class="qkx-group-start">pre</th><th>EDP</th>' for _ in groups)
    body = []
    for r in rows:
        cells = f"<td>{esc(r['label'])}</td>"
        for _, key in groups:
            cells += (f'<td class="qkx-group-start">{esc(r.get(key + "_pre", ""))}</td>'
                      f'<td>{esc(r.get(key + "_edp", ""))}</td>')
        body.append(f"<tr>{cells}</tr>")
    return (f'<div class="qkx-table-wrap"><table class="qkx-table">'
            f'<thead><tr>{head1}</tr><tr>{head2}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def section_title(text, badge=None):
    """badge: optional right-aligned pill (e.g. '18 CELLS'), matching
    QUICKIX HTML's card-header count badge."""
    badge_html = f'<span class="qkx-title-badge">{esc(badge)}</span>' if badge else ''
    st.markdown(f'<div class="qkx-section-title" style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span>{esc(text)}</span>{badge_html}</div>', unsafe_allow_html=True)


def count_caption(rows, status_key="status", bad_value="MISMATCH", noun="row"):
    n_bad = sum(1 for r in rows if r.get(status_key) == bad_value)
    st.caption(f"{len(rows)} {noun}(s) checked — {n_bad} mismatch(es).")


# ══════════════════════════════════════════════════════════════════════
# RRNRBL Checklist renderer — flat category banners (no nesting; Streamlit
# expanders can't nest and the HTML tool's own renderer doesn't collapse
# per-category either), auto rows batched into one coloured table per
# run, manual rows get a real checkbox + comment box so the value survives
# reruns and feeds the downloadable xlsx.
# ══════════════════════════════════════════════════════════════════════
def _chip(status):
    """Status chip markup — every checklist row carries one so a status is
    readable on its own, not only via a pale row background."""
    cls = {"match": "match", "MATCH": "match", "PASS": "match",
           "mismatch": "mismatch", "MISMATCH": "mismatch", "FAIL": "mismatch",
           "manual": "manual", "MANUAL": "manual", "EXPECTED": "manual",
           "info": "info", "INFO": "info",
           }.get(status, "unknown")
    return f'<span class="qkx-chip {cls}">{esc(STATUS_LABEL.get(status, status))}</span>'


def render_checklist_grid(rows, manual_values):
    """One continuous spreadsheet-style grid for the WHOLE checklist:
    Category | Sub-section | Item | Detail | Status, covering every row
    (auto AND manual) in reading order. Category/Sub-section cells use
    rowspan to merge consecutive identical values — the actual spreadsheet
    "grouped cell" look, rather than repeating the same category name on
    every row or breaking the table into one fragment per category (the
    old render_rrnrbl_checklist() approach).

    manual_values: {row_number: {"done": bool, "comment": str}} — the
    CURRENTLY SAVED values for manual items (read from session_state by the
    caller), so a manual row's Detail column shows what's actually been
    entered so far instead of always looking blank. Manual rows remain
    read-only in this grid; actually entering a comment still happens in
    the separate fill-in section below (a raw HTML table cannot host a
    live checkbox/text-input widget)."""
    if not rows:
        return '<div class="qkx-empty">Run validation to populate the checklist.</div>'

    # Precompute rowspans: for each row, how many rows below it (inclusive)
    # share the same (cat) or (cat, sub) — 0 means "this row is covered by
    # an earlier rowspan, emit no <td> for this column at all".
    n = len(rows)
    cat_span = [0] * n
    sub_span = [0] * n
    i = 0
    while i < n:
        j = i
        while j < n and rows[j]["cat"] == rows[i]["cat"]:
            j += 1
        cat_span[i] = j - i
        i = j
    i = 0
    while i < n:
        j = i
        while j < n and rows[j]["cat"] == rows[i]["cat"] and rows[j].get("sub") == rows[i].get("sub"):
            j += 1
        sub_span[i] = j - i
        i = j

    head = ('<th style="width:15%;">Category</th><th style="width:15%;">Sub-section</th>'
            '<th style="width:24%;">Item</th><th>Detail</th><th style="width:96px;">Status</th>')
    body = []
    for idx, r in enumerate(rows):
        status = r["status"]
        color, bg = STATUS_COLORS.get(status, DEFAULT_COLOR)
        if status == "manual":
            mv = manual_values.get(r["row"], {})
            detail = mv.get("comment") or "—"
            if mv.get("done"):
                detail = f"\u2713 {detail}" if detail != "—" else "\u2713 Marked done"
        else:
            detail = r.get("detail", "")

        cells = ""
        if cat_span[idx] > 0:
            cells += f'<td rowspan="{cat_span[idx]}" style="font-weight:700;vertical-align:top;background:#f8fafc;">{esc(r["cat"])}</td>'
        if sub_span[idx] > 0:
            sub_text = esc(r.get("sub")) if r.get("sub") else "\u2014"
            cells += f'<td rowspan="{sub_span[idx]}" style="vertical-align:top;color:#475569;">{sub_text}</td>'
        cells += (f'<td style="color:{color};font-weight:600;">{esc(r["item"])}</td>'
                  f'<td style="color:{color};">{esc(detail)}</td>'
                  f'<td style="width:96px;">{_chip(status)}</td>')
        body.append(f'<tr style="background:{bg};">{cells}</tr>')

    return (f'<div class="qkx-table-wrap qkx-grid-wrap"><table class="qkx-table qkx-grid">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def render_rrnrbl_checklist(rows):
    if not rows:
        st.markdown('<div class="qkx-empty">Run validation to populate the checklist.</div>', unsafe_allow_html=True)
        return

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    order = ["mismatch", "manual", "match", "info", "unknown", "na"]
    pills = "".join(
        f'<span class="qkx-count-pill"><b style="color:{STATUS_COLORS.get(k, DEFAULT_COLOR)[0]}">{counts[k]}</b> '
        f'{esc(STATUS_LABEL.get(k, k))}</span>'
        for k in sorted(counts, key=lambda x: (order.index(x) if x in order else 99, x))
    )
    st.markdown(f'<div style="margin:2px 0 10px 0;">{pills}</div>', unsafe_allow_html=True)

    manual_values = {
        r["row"]: {
            "done": st.session_state.get(f'rrnrbl_{r["row"]}_done', False),
            "comment": st.session_state.get(f'rrnrbl_{r["row"]}_comment', ""),
        }
        for r in rows if r["status"] == "manual"
    }
    st.markdown(render_checklist_grid(rows, manual_values), unsafe_allow_html=True)

    man_rows = [r for r in rows if r["status"] == "manual"]
    if man_rows:
        with st.expander(f"\u270e Fill in manual items ({len(man_rows)})", expanded=False):
            last_cat = last_sub = object()
            for i, mr in enumerate(man_rows):
                if mr["cat"] != last_cat or mr.get("sub") != last_sub:
                    st.markdown(f'<div class="qkx-sub-header">{esc(mr["cat"])}'
                                + (f' \u2014 {esc(mr["sub"])}' if mr.get("sub") else "") + '</div>',
                                unsafe_allow_html=True)
                    last_cat, last_sub = mr["cat"], mr.get("sub")
                key = f'rrnrbl_{mr["row"]}'
                st.markdown(f'<div class="qkx-manual-item">{esc(mr["item"])}</div>', unsafe_allow_html=True)
                col = st.columns([0.06, 0.94])
                with col[0]:
                    st.checkbox("Done", key=f"{key}_done", label_visibility="collapsed")
                with col[1]:
                    st.text_input("Comment", key=f"{key}_comment", label_visibility="collapsed",
                                  placeholder="Comment / evidence…")
                if i < len(man_rows) - 1:
                    st.markdown('<div style="height:1px;background:#eef1f6;margin:2px 0 10px 0;"></div>',
                                unsafe_allow_html=True)


def collect_manual_overrides(checklist):
    overrides = {}
    for row in checklist:
        if row["status"] != "manual":
            continue
        r = row["row"]
        overrides[r] = {
            "done": st.session_state.get(f"rrnrbl_{r}_done", False),
            "comment": st.session_state.get(f"rrnrbl_{r}_comment", ""),
        }
    return overrides


# ══════════════════════════════════════════════════════════════════════
# Session state / one-shot validation run
# ══════════════════════════════════════════════════════════════════════
st.session_state.setdefault("has_run", False)


def strip_ansi(text):
    """Removes terminal control sequences from a raw log capture. Some Pre
    kget-all logs are captured via a terminal client (e.g. PuTTY) with
    color/bold formatting enabled, which wraps the node-id prompt in ANSI
    codes: 'FCL04120> lt all' becomes '\\x1b[1mFCL04120\\x1b[0m> lt all'.
    Every regex in this project that matches a prompt line expects it to
    start with the bare node id, so without this the ANSI codes make
    node_id_from_log()/split_commands() fail silently — the whole log then
    parses to nothing, and node identification falls back to the uploaded
    FILENAME (confirmed: a real PuTTY-captured log showed Node ID as
    'FCL04120.txt', SW Version, BB Type, and all cell tables empty).
    Applied once here, at the single point every uploaded log's raw bytes
    are first decoded to text, so every downstream function (which all
    receive already-decoded text) is unaffected regardless of capture tool."""
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)  # CSI: colors, bold, cursor movement
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)  # OSC: window title/icon name
    text = re.sub(r'\x1b.', '', text)  # any remaining lone ESC + one char
    return text


def _tmp_path(data, suffix):
    tf = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tf.write(data)
    tf.close()
    return tf.name


def run_full_validation(ciq_bytes, edp_bytes, edp_ext, rfds_bytes, node_logs_text):
    ciq_path = _tmp_path(ciq_bytes, ".xlsx")
    edp_path = _tmp_path(edp_bytes, edp_ext or ".xls")
    rfds_path = _tmp_path(rfds_bytes, ".pdf") if rfds_bytes else None
    node_log_paths = {nid: _tmp_path(text.encode("utf-8"), ".txt") for nid, text in node_logs_text.items()}

    with tempfile.TemporaryDirectory() as tmp:
        out_pdf = os.path.join(tmp, "validation_report.pdf")
        (pdf_path, results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages,
         pre_text, post_text, scope_lines, sow) = rv.run(ciq_path, edp_path, rfds_path, node_log_paths, out_pdf)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

    checklist = rc.build_checklist(results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages)
    site_id_fa = " / ".join(v for v in (site_details.get("site_id"), site_details.get("fa_code")) if v)

    return dict(
        results=results, site_details=site_details, ciq_wb=ciq_wb, edp_rows=edp_rows,
        checked_nodes=checked_nodes, rfds_pages=rfds_pages, pre_text=pre_text, post_text=post_text,
        scope_lines=scope_lines, sow=sow, checklist=checklist, site_id_fa=site_id_fa,
        pdf_bytes=pdf_bytes, node_logs_text=node_logs_text,
    )


# ══════════════════════════════════════════════════════════════════════
# INPUT PAGE — shown only until "Run Validation" succeeds. Everything the
# tool needs is uploaded here once; no tab has its own uploader anymore.
# ══════════════════════════════════════════════════════════════════════
if not st.session_state["has_run"]:
    st.markdown("#### Load site data")
    c1, c2 = st.columns(2)
    with c1:
        ciq_up = st.file_uploader("CIQ workbook — required", type=["xlsx"], key="in_ciq")
        edp_up = st.file_uploader("EDP workbook — required (.xls or .xlsx)", type=["xls", "xlsx"], key="in_edp")
    with c2:
        rfds_up = st.file_uploader("RFDS PDF — optional", type=["pdf"], key="in_rfds")
        log_ups = st.file_uploader("Pre kget-all / hget logs — optional, one per node", type=["txt", "log"],
                                    accept_multiple_files=True, key="in_logs")

    ready = bool(ciq_up and edp_up)
    if st.button("▶ Run Validation", type="primary", disabled=not ready, use_container_width=True):
        node_logs_text = {}
        for u in (log_ups or []):
            text = strip_ansi(u.getvalue().decode("utf-8", errors="ignore"))
            nid = pe.node_id_from_log(text) or u.name
            node_logs_text[nid] = text
        with st.spinner("Running full validation…"):
            try:
                state = run_full_validation(
                    ciq_up.getvalue(), edp_up.getvalue(), os.path.splitext(edp_up.name)[1],
                    rfds_up.getvalue() if rfds_up else None, node_logs_text,
                )
            except Exception as e:
                st.error(f"Validation failed: {e}")
                st.stop()
        st.session_state["state"] = state
        st.session_state["has_run"] = True
        st.rerun()
    elif not ready:
        st.caption("CIQ and EDP are both required to run validation. RFDS PDF and Pre logs are optional but enable more checks.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════
# RESULTS — one validation run, five tabs, all reading the same state.
# ══════════════════════════════════════════════════════════════════════
state = st.session_state["state"]
results = state["results"]
ciq_wb = state["ciq_wb"]
site_details = state["site_details"]
edp_rows = state["edp_rows"]
checked_nodes = state["checked_nodes"]
rfds_pages = state["rfds_pages"]
node_logs_text = state["node_logs_text"]
sow = state["sow"]

@st.dialog("Revision History", width="large")
def _show_revision_history_dialog(ciq_wb):
    sheet_name, rows = cer.read_revision_history(ciq_wb)
    if not rows:
        st.caption("No Revision History sheet found in this CIQ.")
        return
    # The sheet stacks TWO mini-tables with different headers (Version/
    # Description/Updated Date/Updated By, then Date/Confirmations
    # Received) — a header row is any row whose first two cells are both
    # non-numeric-looking text, same detection QUICKIX's own renderer uses
    # rather than assuming a fixed row count for the first table.
    def _looks_like_header(row):
        a, b = str(row[0]).strip(), str(row[1]).strip()
        return bool(a) and bool(b) and not any(ch.isdigit() for ch in a[:1])

    blocks, current = [], None
    for row in rows:
        if _looks_like_header(row):
            current = {"header": row, "rows": []}
            blocks.append(current)
        elif current is not None:
            current["rows"].append(row)
        else:
            current = {"header": ["", "", "", "", ""], "rows": [row]}
            blocks.append(current)

    for block in blocks:
        # Keep every header column that has a label OR that any data row in
        # this block actually uses (drops the sheet's trailing blank 5th
        # column when nothing in the block ever fills it, without hiding a
        # legitimately blank-labelled column that does have data).
        n = len(block["header"])
        used = [bool(str(block["header"][i]).strip()) or any(str(r[i]).strip() for r in block["rows"] if i < len(r))
                for i in range(n)]
        columns = [(i, str(block["header"][i]) or f"Col {i+1}") for i in range(n) if used[i]]
        if not columns:
            continue
        st.markdown(render_table(
            [dict(zip(range(n), r)) for r in block["rows"]],
            columns=columns, status_key=None,
        ), unsafe_allow_html=True)


top_l, top_m, top_r = st.columns([1, 1, 4])
with top_l:
    if st.button("🔄 New Validation Run", use_container_width=True):
        st.session_state.clear()
        st.rerun()
with top_m:
    if st.button("📜 Revision History", use_container_width=True):
        _show_revision_history_dialog(ciq_wb)
with top_r:
    bits = [f"Site ID: `{site_details.get('site_id') or '—'}`", f"FA Code: `{site_details.get('fa_code') or '—'}`",
            f"USID: `{site_details.get('usid') or '—'}`", f"Nodes: `{', '.join(checked_nodes) or '—'}`"]
    st.caption(" &nbsp;·&nbsp; ".join(bits), unsafe_allow_html=True)

tab_rfds, tab_audit, tab_edp, tab_checklist, tab_consolidated = st.tabs(
    ["RFDS Validation", "Audit", "EDP Validator", "RET Antenna Checklist", "Consolidated Report"]
)

# ══════════════════════════════════════════════════════════════════════
# TAB 1 — RFDS Validation: every RFDS-vs-CIQ(-vs-Pre) comparison the run
# already computed (Primary/Secondary, Board type, XMU, Cells, Cell ID,
# Radio type) — all colour-coded, bordered tables.
# ══════════════════════════════════════════════════════════════════════
with tab_rfds:
    st.subheader("RFDS Validation")
    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(f'<div class="qkx-stat"><b>FA Code</b><br>{esc(site_details.get("fa_code") or "—")}</div>', unsafe_allow_html=True)
    m2.markdown(f'<div class="qkx-stat"><b>USID</b><br>{esc(site_details.get("usid") or "—")}</div>', unsafe_allow_html=True)
    m3.markdown(f'<div class="qkx-stat"><b>Site ID</b><br>{esc(site_details.get("site_id") or "—")}</div>', unsafe_allow_html=True)
    m4.markdown(f'<div class="qkx-stat"><b>Atoll Name</b><br>{esc(site_details.get("atoll_site_name") or "—")}</div>', unsafe_allow_html=True)

    if rfds_pages is None:
        st.info("No RFDS PDF was loaded for this run — RFDS-dependent comparisons below are skipped.")

    _rfds_inventory_text = None
    if rfds_pages is not None:
        _t = rf.find_pages_by_heading(rfds_pages, "Non RF Inventory Details (Final)")
        _rfds_inventory_text = re.sub(r"\s+", "", _t) if _t else None

    def _board_rfds_display(r):
        ciq_du = r.get("ciq_du_type") or ""
        if r.get("rfds_agrees") is True:
            return ciq_du or "FOUND"
        if _rfds_inventory_text:
            candidates = sorted(set(re.findall(r"\d{4,5}", _rfds_inventory_text)) - {ciq_du})
            return "/".join(candidates[:3]) if candidates else "NOT FOUND"
        return "NOT CHECKED"

    def _ciq_xmu_count(node_id, ciq_wb):
        mm = next((m for m in cer.mixed_mode_rows(ciq_wb)
                   if str(m.get("Node to be built as") or m.get("eNodeB Name") or "").strip() == node_id), None)
        if mm is None:
            return None
        e_name, g_name = mm.get("eNodeB Name"), mm.get("gNodeB Name")
        row = None
        if e_name and "eNB Info" in ciq_wb.sheetnames:
            row = next((r for r in cer.sheet_rows_as_dicts(ciq_wb["eNB Info"])
                        if str(r.get("eNodeB Name", "")).strip().upper() == str(e_name).strip().upper()), None)
        if row is None and g_name and "gNB Info" in ciq_wb.sheetnames:
            row = next((r for r in cer.sheet_rows_as_dicts(ciq_wb["gNB Info"])
                        if str(r.get("gNodeB Name", "")).strip().upper() == str(g_name).strip().upper()), None)
        if row is None:
            return None
        return sum(1 for k in ("1st XMU", "2nd XMU", "3rd XMU") if str(row.get(k, "")).strip().upper() == "YES")

    with st.container(border=True):
        section_title("Primary & Secondary Node")
        rows = results.get("primary_secondary", [])
        display_rows = [
            dict(r, comments="Match" if r.get("status") != "MISMATCH" else
                 f"Mismatch found on {wt.primary_secondary_mismatched_role(r)} id on {r.get('node')}.")
            for r in rows
        ]
        st.markdown(render_table_with_comments(display_rows, columns=[("node", "Node"), ("ciq", "CIQ"),
                                                                        ("edp", "EDP"), ("rfds", "RFDS")],
                                                note_key="comments"),
                    unsafe_allow_html=True)

    with st.container(border=True):
        section_title("Board Type")
        rows = results.get("board_type", [])
        display_rows = [
            dict(r, rfds=_board_rfds_display(r),
                 comments=("Match" if r.get("status") == "MATCH" else
                           f"Board swap (expected) on {r.get('node')}." if r.get("status") == "EXPECTED" else
                           f"Board type mismatch found on the {r.get('node')}."))
            for r in rows
        ]
        st.markdown(render_table_with_comments(display_rows, columns=[("node", "Node"), ("ciq_du_type", "CIQ DU Type"),
                                                                        ("edp_model", "EDP Model"), ("rfds", "RFDS")],
                                                note_key="comments"),
                    unsafe_allow_html=True)
        if rfds_pages is not None:
            st.caption("RFDS model is a best-effort text match against the RFDS's Non RF Inventory section, not a structured per-node field.")

    with st.container(border=True):
        section_title("XMU Validation")
        rows = results.get("xmu", [])
        display_rows = []
        for r in rows:
            n = _ciq_xmu_count(r.get("node"), ciq_wb)
            ciq_label = f"{n} XMU" if n else ("0 XMU" if n == 0 else "—")
            rfds_val = r.get("rfds_xmu")
            rfds_label = "XMU Found" if rfds_val is True else ("XMU Not Found" if rfds_val is False else "NOT CHECKED")
            comments = "Match" if r.get("status") == "MATCH" else f"XMU mismatch found on the {r.get('node')}."
            display_rows.append(dict(r, ciq_xmu=ciq_label, rfds_xmu=rfds_label, comments=comments))
        st.markdown(render_table_with_comments(display_rows, columns=[("node", "Node"), ("ciq_xmu", "CIQ XMU"),
                                                                        ("rfds_xmu", "RFDS XMU")],
                                                note_key="comments"),
                    unsafe_allow_html=True)
        st.caption("RFDS doesn't expose an XMU count (only presence) — RFDS XMU shows Found/Not Found, not a count.")

    with st.container(border=True):
        grouped_rows = build_rfds_grouped_rows(results, ciq_wb, rfds_pages)
        n_fail = sum(1 for r in grouped_rows if r["overall"] == "FAIL")
        n_pass = len(grouped_rows) - n_fail
        st.markdown(
            f'<div style="text-align:right;font-weight:700;margin:0 0 8px;">'
            f'Total: {len(grouped_rows)} &nbsp;|&nbsp; '
            f'<span style="color:#065f46;">PASS: {n_pass}</span> &nbsp;|&nbsp; '
            f'<span style="color:#991b1b;">FAIL: {n_fail}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(render_rfds_grouped_table(grouped_rows), unsafe_allow_html=True)
        st.caption('"Losses & Delays" has no extractor in this backend yet — always shows NOT AVAILABLE, not a fabricated pass.')

# ══════════════════════════════════════════════════════════════════════
# TAB 2 — Audit: Pre checks (AMOS) / CIQ Checks / Audit (Pre vs CIQ) / CR Desc
# ══════════════════════════════════════════════════════════════════════
with tab_audit:
    sub_pre, sub_ciq, sub_audit, sub_crdesc = st.tabs(["Pre checks (AMOS)", "CIQ Checks", "Audit (Pre vs CIQ)", "CR Desc"])

    with sub_pre:
        if not node_logs_text:
            st.info("No Pre kget-all logs were loaded for this run.")
        else:
            summary_rows, lte_rows, nr_rows = av.build_amos_tables(node_logs_text)

            section_title("Node Summary", badge=f"{len(summary_rows)} NODE(S)")
            st.markdown(render_table(summary_rows, status_key=None, columns=[
                ("node", "Node ID"), ("sw_package", "BB Type"), ("sw_version", "SW Version"),
                ("type", "Mode"), ("ptp_status", "PTP Status"), ("sa_nsa_status", "SA/NSA Status"),
            ]), unsafe_allow_html=True)

            section_title(f"LTE Cells — {', '.join(summary_rows and [r['node'] for r in summary_rows] or sorted(node_logs_text))}",
                          badge=f"{len(lte_rows)} CELLS")
            st.markdown(render_table(lte_rows, status_key=None, columns=[
                ("node", "Node"), ("cell", "Cell"), ("sector_carrier", "Sector Carries"), ("rru", "RRUs"),
                ("radio_type", "Radio Type"), ("sharing_radio", "Sharing Radio"), ("tx", "TX"), ("rx", "RX"),
                ("rfbranch_tx_ref", "RFBRANCHTXREF"), ("rfbranch_rx_ref", "RFBRANCHRXREF"),
                ("sef_rfbranches", "SEF RFBRANCHES"), ("pre_existing_dss", "Pre Existing DSS"),
            ]), unsafe_allow_html=True)

            section_title("5G NR Cells", badge=f"{len(nr_rows)} CELLS")
            st.markdown(render_table(nr_rows, status_key=None, columns=[
                ("node", "Node"), ("cell", "Cell"), ("rru", "RRUs"), ("tx", "TX"), ("rx", "RX"),
                ("sef_rfbranches", "SEF RFBRANCHES"),
            ]), unsafe_allow_html=True)

    with sub_ciq:
        import ciq_checks as cc

        controller_rows = cv.build_controller_info(ciq_wb)
        if controller_rows:
            col1, col2 = st.columns([2, 1])
            with col1:
                section_title("Node Integration")
                st.markdown(render_table(cv.build_node_integration(ciq_wb), status_key=None, columns=[
                    ("node", "Node"), ("eNBId", "ENBID"), ("eNodeB", "ENODEB"), ("gNBId", "GNBID"), ("gNodeB", "GNODEB"),
                    ("mode", "Mode"), ("bb_type", "BB Type"), ("mme_region", "MME Region"), ("enm", "ENM"),
                    ("xmu", "XMU"), ("ports", "Ports"),
                ]), unsafe_allow_html=True)
            with col2:
                section_title("Controller Info")
                st.markdown(render_table(controller_rows, status_key=None, columns=[
                    ("usid", "USID"), ("controller", "Controller"), ("id", "ID"),
                ]), unsafe_allow_html=True)
        else:
            section_title("Node Integration")
            st.markdown(render_table(cv.build_node_integration(ciq_wb), status_key=None, columns=[
                ("node", "Node"), ("eNBId", "ENBID"), ("eNodeB", "ENODEB"), ("gNBId", "GNBID"), ("gNodeB", "GNODEB"),
                ("mode", "Mode"), ("bb_type", "BB Type"), ("mme_region", "MME Region"), ("enm", "ENM"),
                ("xmu", "XMU"), ("ports", "Ports"),
            ]), unsafe_allow_html=True)

        ciq_lte_rows = cc.build_lte_ciq_rows(ciq_wb)
        ciq_nr_rows = cc.build_nr_ciq_rows(ciq_wb)
        cc.apply_link_and_sharing(ciq_lte_rows, ciq_nr_rows)

        section_title("LTE E-UTRAN Parameters", badge=f"{len(ciq_lte_rows)}")
        st.markdown(render_table(ciq_lte_rows, status_key=None, columns=[
            ("node", "Node"), ("cell", "Cell"), ("pci", "PCI"), ("electrical_tilt", "Electrical Tilt"),
            ("rbb_type", "RBB Type Verification"), ("tx", "TX"), ("rx", "RX"),
            ("riport", "RIPORT"), ("sharing_radio", "Sharing Radio"),
            ("link", "Link (Single/Doublelink)"), ("comments_html", "Comments/Warning"),
        ]), unsafe_allow_html=True)

        section_title("5G NR Parameters", badge=f"{len(ciq_nr_rows)}")
        st.markdown(render_table(ciq_nr_rows, status_key=None, columns=[
            ("node", "Node"), ("cell", "Cell"), ("sef", "SEF"), ("fru", "FRU"), ("nr_pci", "NR PCI"),
            ("electrical_tilt", "Electrical Tilt"), ("rbb_type", "RBB Type Verification"), ("riport", "RIPORT"),
            ("sharing_radio", "Sharing Radio"), ("link", "Link (Single/Doublelink)"), ("comments_html", "Comments/Warning"),
        ]), unsafe_allow_html=True)

        antenna_rows = cs.check_antenna_uniqueness(node_id="", ciq_wb=ciq_wb)
        section_title("Antenna Uniqueness", badge=f"{len(antenna_rows)}")
        st.markdown(render_table(antenna_rows, status_key="status", columns=[
            ("cell", "Cells"), ("aug_au_asu_1", "AUG/AU/ASU (1)"), ("aug_au_asu_2", "AUG/AU/ASU (2)"),
            ("verdict", "Status"),
        ]), unsafe_allow_html=True)

    with sub_audit:
        import pre_post_audit as ppa

        section_title("Pre vs Post")
        pre_summary_rows, _, _ = av.build_amos_tables(node_logs_text) if node_logs_text else ([], [], [])
        ciq_node_rows = cv.build_node_integration(ciq_wb)
        node_pre_post_rows = ppa.build_node_pre_post(pre_summary_rows, ciq_node_rows, node_logs_text, edp_rows)
        st.markdown(render_node_pre_post_table(node_pre_post_rows), unsafe_allow_html=True)

        if node_logs_text:
            lte_pp_rows = ppa.compare_lte_cell_level(node_logs_text, ciq_wb)
            lte_pp_summary = ppa.summarize_rows(lte_pp_rows)
            section_title("LTE: Pre vs Post")
            st.markdown(_pre_post_summary_pills(lte_pp_summary), unsafe_allow_html=True)
            st.caption("Green = Match  Red = Mismatch  Format: PRE | POST")
            st.markdown(render_cell_pre_post_table(lte_pp_rows, [
                ("sc", "_sc_ok", "Sec Carrier"), ("cellid", "_cellid_ok", "Cell ID"), ("tac", "_tac_ok", "TAC"),
                ("bw", "_bw_ok", "BW"), ("dl", "_dl_ok", "EARFCN DL"), ("ul", "_ul_ok", "EARFCN UL"),
                ("power", "_power_ok", "Power"), ("tx", "_tx_ok", "TX"), ("rx", "_rx_ok", "RX"),
                ("rru", "_rru_ok", "RRU Model"),
            ]), unsafe_allow_html=True)

            nr_pp_rows = ppa.compare_nr_cell_level(node_logs_text, ciq_wb)
            nr_pp_summary = ppa.summarize_rows(nr_pp_rows)
            section_title("5G: Pre vs Post")
            st.markdown(_pre_post_summary_pills(nr_pp_summary), unsafe_allow_html=True)
            st.caption("Green = Match  Red = Mismatch  Format: PRE | POST")
            st.markdown(render_cell_pre_post_table(nr_pp_rows, [
                ("cellid", "_cellid_ok", "Cell ID"), ("dl", "_dl_ok", "ARFCN DL"), ("ul", "_ul_ok", "ARFCN UL"),
                ("bw_dl", "_bw_dl_ok", "BW DL"), ("bw_ul", "_bw_ul_ok", "BW UL"), ("power", "_power_ok", "TX Power"),
                ("ssb", "_ssb_ok", "SSB Frequency"), ("rru", "_rru_ok", "RRU Model"),
            ]), unsafe_allow_html=True)
        else:
            st.caption("Upload Pre kget-all logs to see the LTE/5G cell-level Pre vs Post tables.")

        # Engineer Comments is computed silently here (not displayed in this
        # tab) purely so CR Desc's auto-detected Nodes/Bands still populate —
        # CR Desc reads state["engineer_comments"] via extract_bands_from_comments().
        amos_lte_rows = amos_nr_rows = None
        if node_logs_text:
            _, amos_lte_rows, amos_nr_rows = av.build_amos_tables(node_logs_text)
        ciq_lte_rows = cv.build_param_table(ciq_wb, "eUtran Parameters", ["EutranCellFDDId", "RRU type"])
        ciq_nr_rows = cv.build_param_table(ciq_wb, "5G Info", ["NRCellDU", "RRU Type"])
        state["engineer_comments"] = build_engineer_comments(
            sow, results, checked_nodes,
            amos_lte_rows=amos_lte_rows, amos_nr_rows=amos_nr_rows,
            ciq_lte_rows=ciq_lte_rows, ciq_nr_rows=ciq_nr_rows,
            node_logs_text=node_logs_text,
        )

    with sub_crdesc:
        section_title("CR Description")
        engineer_comments = state.get("engineer_comments", [])
        all_nodes, deleted_nodes_cr, regular_nodes_cr = extract_nodes_from_audit(sow, checked_nodes)
        bands_cr = extract_bands_from_comments(engineer_comments)

        c1, c2, c3 = st.columns(3)
        with c1:
            mic_mca = st.selectbox("MIC DESC", ["MIC - MCA", "MCA - CRAN"], key="cr_mic_mca")
        with c2:
            site_name_in = st.text_input("Site Name", placeholder="e.g. DOWNTOWN_EAST", key="cr_site_name")
        with c3:
            # Auto-fetched from the CIQ's own 5G Info 'FA Code' column
            # (site_details['fa_code'] is always CIQ-sourced - see
            # checks_node.build_site_details()) - still editable, since the
            # user may need to override it.
            fa_number_in = st.text_input("FA Number", value=site_details.get("fa_code") or "",
                                          placeholder="e.g. 1034567", key="cr_fa_number")
        c4, c5 = st.columns(2)
        with c4:
            sw_version_in = st.text_input("Sw Version", placeholder="e.g. 25.Q4", key="cr_sw_version")
        with c5:
            link_in = st.text_input("Link", placeholder="link to CIQ / ticket / script", key="cr_link")

        rfds_fa = site_details.get("rfds_fa_code")
        if rfds_fa:
            ciq_fa = site_details.get("fa_code")
            if ciq_fa and rfds_fa != ciq_fa:
                st.warning(f"FA Code mismatch — CIQ: `{ciq_fa}` vs RFDS: `{rfds_fa}`. "
                           f"The field above uses the CIQ value; verify which is correct before sending.")
            else:
                st.caption(f"FA Code confirmed — CIQ and RFDS both report `{ciq_fa}`.")
        elif rfds_pages is not None:
            st.caption("RFDS was provided but no FA Code was found on it — CIQ value used, not cross-checked.")

        n1, n2 = st.columns(2)
        with n1:
            st.markdown("**Nodes (from Audit)** — auto-detected")
            st.markdown(", ".join(all_nodes) if all_nodes else "_Run validation to auto-populate…_")
        with n2:
            st.markdown("**Bands (from Audit)** — auto-detected")
            st.markdown(" / ".join(bands_cr) if bands_cr else "_Run validation to auto-populate…_")

        if st.button("Generate CR Description", type="primary", key="btn_gen_cr"):
            cr_text, breakdown = build_cr_description(mic_mca, site_name_in, fa_number_in, all_nodes, bands_cr)
            if cr_text is None:
                st.error("Please enter Site Name and FA Number (and make sure a validation run has produced node data).")
            else:
                st.session_state["cr_output"] = cr_text
                st.session_state["cr_breakdown"] = breakdown

        if st.session_state.get("cr_output"):
            st.text_area("Generated CR description", value=st.session_state["cr_output"], height=80, key="cr_output_area")
            st.markdown(render_table(
                [{"field": k, "value": v} for k, v in (st.session_state.get("cr_breakdown") or [])],
                columns=[("field", "Field"), ("value", "Value")], status_key=None,
            ), unsafe_allow_html=True)

        st.divider()
        email_text = build_radio_ret_email(sw_version_in, fa_number_in, link_in, engineer_comments)
        st.text_area("Radio/RET Comments Email", value=email_text, height=260, key="cr_email_area")

# ══════════════════════════════════════════════════════════════════════
# TAB 3 — EDP Validator
# ══════════════════════════════════════════════════════════════════════
with tab_edp:
    st.subheader("EDP Validator")
    # Primary AND Secondary node ids, not just checked_nodes (which only ever
    # holds the Primary name — 'Node to be built as' — so every check below
    # was silently skipping every Secondary physical node, e.g. HXIN010147
    # paired with HXL04147, even though these check functions already have
    # their own Primary/Secondary-aware logic via _edp_role() and were
    # clearly written to validate both — they just never received the
    # Secondary node's name as input.
    node_role_list = rc.build_primary_secondary_node_list(ciq_wb)
    edp_check_node_ids = [n["node"] for n in node_role_list]

    section_title("EDP Field Values — Primary & Secondary Nodes")
    edp_field_rows = rc.build_edp_field_table(edp_rows, node_role_list)
    st.markdown(render_table(edp_field_rows, status_key=None, columns=[
        ("node", "Node"), ("role", "Role"), ("SITE_NAME", "SITE_NAME"), ("CABINET", "CABINET"),
        ("BBU_TYPE", "BBU_TYPE"), ("NODE_MODEL", "NODE_MODEL"), ("SIAD_PORT_SIZE_BBU", "SIAD_PORT_SIZE_BBU"),
        ("SIAD_PORT_FACING_BBU", "SIAD_PORT_FACING_BBU"), ("BEARER_ENODEB_SB_VLAN_ID", "BEARER_ENODEB_SB_VLAN_ID"),
        ("IPV6_SIAD_BEARER_IP_DEF_ROUTER", "IPV6_SIAD_BEARER_IP_DEF_ROUTER"),
        ("IPV6_ENODEB_BEARER_IP", "IPV6_ENODEB_BEARER_IP"),
        ("OAM_ENODEB_SIAD_OAM_VLAN", "OAM_ENODEB_SIAD_OAM_VLAN"),
        ("IPV6_SIAD_OAM_IP_DEF_ROUTER", "IPV6_SIAD_OAM_IP_DEF_ROUTER"),
        ("IPV6_ENODEB_OAM_IP", "IPV6_ENODEB_OAM_IP"),
    ]), unsafe_allow_html=True)

    section_title("Pre vs EDP — Bearer & OAM IPv6/VLAN")
    if not node_logs_text:
        st.caption("Upload Pre kget-all logs to compare these fields against EDP.")
    else:
        pivot_rows = rc.build_pre_vs_edp_pivot_rows(node_logs_text, node_role_list, edp_rows)
        if not pivot_rows:
            st.caption("No Pre log matched any Primary/Secondary node for this run.")
        else:
            st.markdown(render_pre_vs_edp_pivot_table(pivot_rows), unsafe_allow_html=True)

    section_title("Checklist — Pre/CIQ vs Post (EDP)")
    st.caption("Highlighted fields per the confirmed spec — Default Router (Bearer/OAM) shown but never "
               "highlighted. A node with no uploaded Pre log shows 'no data' (grey), not a mismatch — this "
               "is what an SMBB→MMBB-added Secondary is expected to look like; the Primary's own row is "
               "unaffected.")
    checklist_field_rows = rc.build_checklist_field_table(node_role_list, node_logs_text, edp_rows, ciq_wb, results)
    st.markdown(render_table(checklist_field_rows, status_key="status", columns=[
        ("node", "Node"), ("role", "Role"), ("field", "Field"),
        ("pre_value", "Pre / CIQ Value"), ("edp_value", "Post (EDP) Value"),
    ]), unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# TAB 4 — RET Antenna Checklist — ON HOLD. Placeholder only, no logic.
# The RRNRBL Checklist is a different feature and lives inside
# Consolidated Report, not here — see that tab.
# ══════════════════════════════════════════════════════════════════════
with tab_checklist:
    st.subheader("RET Antenna Checklist")
    st.info("This feature is on hold. Nothing is computed here yet.")

# ══════════════════════════════════════════════════════════════════════
# TAB 5 — Consolidated Report: Pre/Post Config → SOW Summary → Warnings &
# Comments (always visible) → RRNRBL Checklist → RFDS vs CIQ & Pre vs CIQ
# → CIQ Sanity Check → EDP Checks (each collapsible) → PDF/xlsx downloads.
# ══════════════════════════════════════════════════════════════════════
with tab_consolidated:
    st.subheader("Consolidated Report")

    section_title("Pre / Post Configuration")
    pc1, pc2 = st.columns(2)
    pc1.markdown(f'<div class="qkx-stat" style="text-align:left;"><b>Pre</b><br>{esc(state["pre_text"] or "(none — new build / no Pre log)")}</div>', unsafe_allow_html=True)
    pc2.markdown(f'<div class="qkx-stat" style="text-align:left;"><b>Post</b><br>{esc(state["post_text"] or "—")}</div>', unsafe_allow_html=True)

    section_title("SOW Summary")
    scope_lines = state["scope_lines"]
    if scope_lines:
        st.markdown("\n".join(f"- {esc(l)}" for l in scope_lines))
    else:
        st.caption("Nothing to report.")

    with st.expander("RRNRBL Checklist", expanded=False):
        checklist = state["checklist"]
        render_rrnrbl_checklist(checklist)

    with st.expander("Warnings & Comments", expanded=False):
        rfds_verification_rows = build_rfds_verification_summary(build_rfds_grouped_rows(results, ciq_wb, rfds_pages))
        if rfds_verification_rows:
            st.markdown("**RFDS Verification:**")
            st.markdown(render_table(rfds_verification_rows,
                                      columns=[("Finding", "Finding"), ("Corrective Action", "Corrective Action")],
                                      status_key=None),
                        unsafe_allow_html=True)

        warn_keys = ["warn_primary_secondary", "warn_board_type", "warn_xmu", "warn_params_4g", "warn_params_5g",
                     "warn_pci", "warn_radio_type", "warn_sector_swap", "warn_nr_tac", "warn_air_radio", "warn_antenna"]
        all_warnings = []
        for k in warn_keys:
            all_warnings += results.get(k, [])
        all_warnings += results.get("unavailable_notes", [])
        if all_warnings:
            for w in all_warnings:
                st.markdown(f'<div class="qkx-warn-line">{esc(w)}</div>', unsafe_allow_html=True)
        elif not rfds_verification_rows:
            st.caption("No warnings.")

    with st.expander("RFDS vs CIQ & Pre vs CIQ", expanded=False):
        for label, key in [("Cells vs RFDS", "cells_vs_rfds"), ("Cell ID vs RFDS", "cell_id_vs_rfds"),
                            ("Radio Type vs RFDS", "radio_type"), ("Parameters — 4G (Pre vs CIQ)", "params_4g"),
                            ("Parameters — 5G (Pre vs CIQ)", "params_5g"), ("Sector/TX-RX/Power (Pre vs CIQ)", "sector_swap")]:
            st.markdown(f"**{label}**")
            st.markdown(render_table(results.get(key, [])), unsafe_allow_html=True)

    with st.expander("CIQ Sanity Check", expanded=False):
        sanity_rows = (results.get("pci_4g", []) + results.get("pci_5g", []) + results.get("antenna", [])
                       + results.get("port_uniqueness", []) + results.get("sef_fru", [])
                       + results.get("radio_sharing", []) + results.get("nbiot", [])
                       + results.get("sector_id_4890", []) + results.get("rfbranch_per_aug", [])
                       + results.get("dss", []) + results.get("ptp_matrix", []))
        st.markdown(render_table(sanity_rows, columns=[("rule", "Rule"), ("node", "Node"), ("cell", "Cell"),
                                                          ("status", "Status"), ("note", "Note")]),
                    unsafe_allow_html=True)

    with st.expander("EDP Checks", expanded=False):
        mm_by_node2 = {}
        for m in cer.mixed_mode_rows(ciq_wb):
            n = str(m.get("Node to be built as") or m.get("eNodeB Name") or "").strip()
            if n:
                mm_by_node2[n] = m
        controller_ids2 = [r.get("Controller ID") for r in cer.sheet_rows_as_dicts(ciq_wb["Controller Info"])
                            if r.get("Controller ID")] if "Controller Info" in ciq_wb.sheetnames else []
        edp_checks2 = {
            "Found in EDP": rc._edp_found_status(edp_rows, checked_nodes),
            "Cabinet naming": rc._edp_cabinet_status(edp_rows, checked_nodes),
            "Port size (BBU mode)": rc._edp_port_size_status(edp_rows, checked_nodes, mm_by_node2),
            "Port facing (Primary/Secondary)": rc._edp_port_facing_status(edp_rows, checked_nodes),
            "Bearer VLAN clash": rc._edp_bearer_vlan_status(edp_rows, checked_nodes),
            "IPv6 bearer addressing": rc._edp_group_status(edp_rows, checked_nodes, rc.IPV6_BEARER_FIELDS, "IPv6 bearer"),
            "IPv6 OAM addressing": rc._edp_group_status(edp_rows, checked_nodes, rc.IPV6_OAM_FIELDS, "IPv6 OAM"),
            "Controller (ANCEQ)": rc._edp_controller_status(edp_rows, controller_ids2),
            "PTP configuration": rc._edp_ptp_status(edp_rows, checked_nodes),
        }
        st.markdown(render_table([{"check": k, "status": s, "detail": d} for k, (s, d) in edp_checks2.items()],
                                  columns=[("check", "Check"), ("status", "Status"), ("detail", "Detail")]),
                    unsafe_allow_html=True)

    st.divider()
    manual_overrides = collect_manual_overrides(state["checklist"])
    checklist_xlsx = rc.fill_checklist_xlsx(state["checklist"], state["site_id_fa"], manual_overrides=manual_overrides)
    d1, d2 = st.columns(2)
    with d1:
        st.download_button("⬇️ Download PDF", data=state["pdf_bytes"], file_name="validation_report.pdf",
                            mime="application/pdf", use_container_width=True)
    with d2:
        st.download_button("⬇️ Download filled RRNRBL Checklist (.xlsx)", data=checklist_xlsx,
                            file_name="Checklist_RRNRBL_filled.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True, key="cr_checklist_dl")
    st.caption("Check a manual box or type a comment above, then click Download again to bake it into the file.")
