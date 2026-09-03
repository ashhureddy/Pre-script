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
import ciq_view as cv
import amos_view as av

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
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
.qkx-stat { text-align:center; border:1px solid #94a3b8; border-radius:4px; padding:8px; background:#fff; }
.qkx-table-wrap { overflow-x:auto; border:1px solid #64748b; border-radius:3px; margin-bottom:16px; }
.qkx-table { width:100%; border-collapse:collapse; font-size:12.8px; line-height:1.3; }
.qkx-table th {
  background:#dde3f7; color:#101F90; font-weight:700; text-align:left;
  padding:6px 10px; border:1px solid #8b97b8; white-space:nowrap;
}
.qkx-table td { padding:6px 10px; border:1px solid #a8b0c2; vertical-align:top; }
.qkx-table td.qkx-group-start, .qkx-table th.qkx-group-start { border-left:2px solid #334155; }
.qkx-empty { padding:12px 4px; color:#64748b; font-style:italic; font-size:13px; }
.qkx-section-title {
  font-weight:700; font-size:15px; color:#0f1720; margin: 14px 0 8px 0;
  padding-bottom:6px; border-bottom: 2px solid #101F90;
}
.qkx-warn-line {
  padding:6px 10px; margin-bottom:5px; border-radius:5px;
  background:#fee2e2; color:#7f1d1d; font-size:12.5px; border:1px solid #fca5a5;
}
.qkx-cat-banner {
  background:#101F90; color:#fff; font-weight:700; font-size:12.5px;
  padding:6px 10px; border-radius:5px 5px 0 0; margin-top:12px;
}
.qkx-count-pill { font-size:11.5px; color:#334155; margin-right:14px; }
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
    return (f'<div class="qkx-table-wrap"><table class="qkx-table"><thead><tr>{head}</tr></thead>'
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

    port_details = rf.extract_port_level_details(rfds_pages) if rfds_pages is not None else {}
    ant_by_cell = {}
    if "eUtran Parameters" in ciq_wb.sheetnames:
        for r in cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]):
            cell = r.get("EutranCellFDDId")
            if not cell:
                continue
            ciq_ant = r.get("antenna model")
            pd_row = port_details.get(cell)
            tier, detail = ar.resolve_antenna(ciq_ant, pd_row["vendor_model"] if pd_row else None)
            ant_by_cell[cell] = {"ciq": ciq_ant or "—", "rfds": (pd_row or {}).get("vendor_model", "NOT FOUND"),
                                  "tier": tier, "found": pd_row is not None}

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
        elif ant_tier in ("EXACT", "NORMALIZED", "SUFFIX"):
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


def section_title(text):
    st.markdown(f'<div class="qkx-section-title">{esc(text)}</div>', unsafe_allow_html=True)


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
def render_rrnrbl_checklist(rows):
    if not rows:
        st.markdown('<div class="qkx-empty">Run validation to populate the checklist.</div>', unsafe_allow_html=True)
        return

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    pills = "".join(
        f'<span class="qkx-count-pill"><b style="color:{STATUS_COLORS.get(k, DEFAULT_COLOR)[0]}">{v}</b> '
        f'{esc(STATUS_LABEL.get(k, k))}</span>'
        for k, v in counts.items()
    )
    st.markdown(f'<div style="margin-bottom:8px;">{pills}</div>', unsafe_allow_html=True)

    cats = []
    for r in rows:
        if not cats or cats[-1]["cat"] != r["cat"]:
            cats.append({"cat": r["cat"], "rows": []})
        cats[-1]["rows"].append(r)

    for c in cats:
        st.markdown(f'<div class="qkx-cat-banner">{esc(c["cat"])}</div>', unsafe_allow_html=True)
        buf = []
        last_sub = object()

        def flush(buf=buf):
            if buf:
                st.markdown(
                    render_table(buf, columns=[("item", "Item"), ("detail", "Detail")], status_key="status"),
                    unsafe_allow_html=True,
                )
            buf.clear()

        for r in c["rows"]:
            if r.get("sub") != last_sub:
                flush()
                if r.get("sub"):
                    st.markdown(f'*{esc(r["sub"])}*')
                last_sub = r.get("sub")
            if r["status"] == "manual":
                flush()
                key = f'rrnrbl_{r["row"]}'
                cc = st.columns([0.05, 0.95])
                with cc[0]:
                    st.checkbox("Done", key=f"{key}_done", label_visibility="collapsed")
                with cc[1]:
                    st.markdown(f'✎ **{esc(r["item"])}**')
                    st.text_input("Comment", key=f"{key}_comment", label_visibility="collapsed",
                                  placeholder="Comment / evidence…")
            else:
                buf.append({"item": r["item"], "detail": r.get("detail", ""), "status": r["status"]})
        flush()


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
            text = u.getvalue().decode("utf-8", errors="ignore")
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

top_l, top_r = st.columns([1, 5])
with top_l:
    if st.button("🔄 New Validation Run", use_container_width=True):
        st.session_state.clear()
        st.rerun()
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

    with st.container(border=True):
        section_title("Primary & Secondary Node")
        rows = results.get("primary_secondary", [])
        st.markdown(render_table_with_comments(rows, columns=[("node", "Node"), ("ciq", "CIQ"),
                                                               ("edp", "EDP"), ("rfds", "RFDS")]),
                    unsafe_allow_html=True)

    with st.container(border=True):
        section_title("Board Type")
        rows = results.get("board_type", [])
        st.markdown(render_table_with_comments(rows, columns=[("node", "Node"), ("ciq_du_type", "CIQ DU Type"),
                                                               ("edp_model", "EDP Model"), ("rfds_agrees", "RFDS Agrees")]),
                    unsafe_allow_html=True)

    with st.container(border=True):
        section_title("XMU Validation")
        rows = results.get("xmu", [])
        st.markdown(render_table_with_comments(rows, columns=[("node", "Node"), ("ciq_xmu", "CIQ XMU"),
                                                               ("rfds_xmu", "RFDS XMU")]),
                    unsafe_allow_html=True)

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
    st.subheader("Audit")
    sub_pre, sub_ciq, sub_audit, sub_crdesc = st.tabs(["Pre checks (AMOS)", "CIQ Checks", "Audit (Pre vs CIQ)", "CR Desc"])

    with sub_pre:
        if not node_logs_text:
            st.info("No Pre kget-all logs were loaded for this run.")
        else:
            summary_rows, lte_rows, nr_rows = av.build_amos_tables(node_logs_text)
            section_title("Node Summary")
            st.markdown(render_table(summary_rows, status_key=None), unsafe_allow_html=True)
            section_title("LTE Cells")
            st.markdown(render_table(lte_rows, status_key=None), unsafe_allow_html=True)
            section_title("NR Cells")
            st.markdown(render_table(nr_rows, status_key=None), unsafe_allow_html=True)

    with sub_ciq:
        section_title("Node Integration")
        st.markdown(render_table(cv.build_node_integration(ciq_wb), status_key=None), unsafe_allow_html=True)

        section_title("Sanity checks (CIQ-only — no Pre/EDP/RFDS needed)")
        check_rows = (results.get("pci_4g", []) + results.get("pci_5g", []) + results.get("antenna", [])
                      + results.get("port_uniqueness", []) + results.get("sef_fru", []) + results.get("radio_sharing", []))
        for nid in checked_nodes:
            check_rows += cs.check_radio_port_conflict(nid, ciq_wb)
        st.markdown(render_table(check_rows, columns=[("rule", "Rule"), ("node", "Node"), ("cell", "Cell"),
                                                        ("status", "Status"), ("note", "Note")]),
                    unsafe_allow_html=True)
        n_bad = sum(1 for r in check_rows if r.get("status") == "MISMATCH")
        st.caption(f"{len(check_rows)} check(s) — {n_bad} mismatch(es).")

        with st.expander("Raw eUtran Parameters / 5G Info"):
            st.markdown("**eUtran Parameters (LTE)**")
            st.markdown(render_table(cv.build_param_table(ciq_wb, "eUtran Parameters", cv.LTE_PARAM_COLS), status_key=None),
                        unsafe_allow_html=True)
            st.markdown("**5G Info**")
            st.markdown(render_table(cv.build_param_table(ciq_wb, "5G Info", cv.NR_PARAM_COLS), status_key=None),
                        unsafe_allow_html=True)

        section_title("Antenna model vs RFDS")
        if rfds_pages is None:
            st.caption("Upload an RFDS PDF to compare antenna models.")
        else:
            port_details = rf.extract_port_level_details(rfds_pages)
            if not port_details:
                st.caption("No 'Port Level Details (Final)' table found in this RFDS PDF.")
            else:
                ant_rows = []
                for r in (cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]) if "eUtran Parameters" in ciq_wb.sheetnames else []):
                    cell = r.get("EutranCellFDDId")
                    ciq_ant = r.get("antenna model")
                    pd_row = port_details.get(cell)
                    tier, detail = ar.resolve_antenna(ciq_ant, pd_row["vendor_model"] if pd_row else None)
                    ant_rows.append({"cell": cell, "ciq_antenna": ciq_ant,
                                      "rfds_vendor_model": pd_row["vendor_model"] if pd_row else "NOT FOUND",
                                      "match_tier": tier, "detail": detail})
                st.markdown(render_table(ant_rows, columns=[("cell", "Cell"), ("ciq_antenna", "CIQ Antenna"),
                                                              ("rfds_vendor_model", "RFDS Vendor/Model"),
                                                              ("match_tier", "Match Tier"), ("detail", "Detail")],
                                          status_key=None),
                            unsafe_allow_html=True)

        section_title("FA Code follow-up email")
        fa = site_details.get("fa_code") or "UNKNOWN"
        n_mismatch = sum(1 for r in check_rows if r.get("status") == "MISMATCH")
        subject = f"FA {fa} — CIQ checks: {n_mismatch} mismatch(es) found"
        body = (f"FA Code: {fa}%0D%0ANodes: {', '.join(checked_nodes)}%0D%0A"
                f"CIQ sanity checks found {n_mismatch} mismatch(es) out of {len(check_rows)} — see attached export.")
        st.link_button("✉️ Compose FA-code email", f"mailto:?subject={subject}&body={body}")

    with sub_audit:
        if not node_logs_text:
            st.warning("No Pre kget-all logs were loaded for this run — nothing to compare against CIQ.")
        else:
            section_title("Parameters — 4G (Pre vs CIQ)")
            st.markdown(render_table(results.get("params_4g", []), status_key="status"), unsafe_allow_html=True)
            section_title("Parameters — 5G (Pre vs CIQ)")
            st.markdown(render_table(results.get("params_5g", []), status_key="status"), unsafe_allow_html=True)
            section_title("Sector / TX-RX / Power (Pre vs CIQ, best-effort)")
            st.markdown(render_table(results.get("sector_swap", []), status_key="status"), unsafe_allow_html=True)
            if rfds_pages is not None:
                section_title("Cell ID vs RFDS (cross-check)")
                st.markdown(render_table(results.get("cell_id_vs_rfds", []), status_key="status"), unsafe_allow_html=True)
            rows = results.get("params_4g", []) + results.get("params_5g", []) + results.get("sector_swap", [])
            n_bad = sum(1 for r in rows if r.get("status") == "MISMATCH")
            st.caption(f"{len(rows)} field(s) checked — {n_bad} mismatch(es).")

    with sub_crdesc:
        section_title("CR Description")
        if not node_logs_text:
            st.caption("No Pre logs loaded — 'moved sector' lines need Pre data to detect the source node; "
                       "this is integration-only scope for now.")
        scope_lines = state["scope_lines"]
        cr_text = "\n".join(f"- {l}" for l in scope_lines) if scope_lines else "(nothing to describe yet)"
        st.text_area("Generated CR description", value=cr_text, height=200)
        st.download_button("⬇️ Download CR description (.txt)", data=cr_text,
                            file_name="cr_description.txt", mime="text/plain")

# ══════════════════════════════════════════════════════════════════════
# TAB 3 — EDP Validator
# ══════════════════════════════════════════════════════════════════════
with tab_edp:
    st.subheader("EDP Validator")
    mm_by_node = {}
    for m in cer.mixed_mode_rows(ciq_wb):
        n = str(m.get("Node to be built as") or m.get("eNodeB Name") or "").strip()
        if n:
            mm_by_node[n] = m
    controller_ids = [r.get("Controller ID") for r in cer.sheet_rows_as_dicts(ciq_wb["Controller Info"])
                       if r.get("Controller ID")] if "Controller Info" in ciq_wb.sheetnames else []
    checks = {
        "Found in EDP": rc._edp_found_status(edp_rows, checked_nodes),
        "Cabinet naming": rc._edp_cabinet_status(edp_rows, checked_nodes),
        "Port size (BBU mode)": rc._edp_port_size_status(edp_rows, checked_nodes, mm_by_node),
        "Port facing (Primary/Secondary)": rc._edp_port_facing_status(edp_rows, checked_nodes),
        "Bearer VLAN clash": rc._edp_bearer_vlan_status(edp_rows, checked_nodes),
        "IPv6 bearer addressing": rc._edp_group_status(edp_rows, checked_nodes, rc.IPV6_BEARER_FIELDS, "IPv6 bearer"),
        "IPv6 OAM addressing": rc._edp_group_status(edp_rows, checked_nodes, rc.IPV6_OAM_FIELDS, "IPv6 OAM"),
        "Controller (ANCEQ)": rc._edp_controller_status(edp_rows, controller_ids),
        "PTP configuration": rc._edp_ptp_status(edp_rows, checked_nodes),
    }
    n_pass = sum(1 for s, _ in checks.values() if s == "match")
    n_fail = sum(1 for s, _ in checks.values() if s == "mismatch")
    n_unk = sum(1 for s, _ in checks.values() if s not in ("match", "mismatch"))

    s1, s2, s3, s4 = st.columns(4)
    s1.markdown(f'<div class="qkx-stat"><b>{len(checked_nodes)}</b><br>Expected Nodes</div>', unsafe_allow_html=True)
    s2.markdown(f'<div class="qkx-stat"><b>{n_pass}</b><br>Pass</div>', unsafe_allow_html=True)
    s3.markdown(f'<div class="qkx-stat"><b>{n_fail}</b><br>Fail</div>', unsafe_allow_html=True)
    s4.markdown(f'<div class="qkx-stat"><b>{n_unk}</b><br>No data</div>', unsafe_allow_html=True)

    section_title("EDP field checks")
    st.markdown(render_table([{"check": k, "status": s, "detail": d} for k, (s, d) in checks.items()],
                              columns=[("check", "Check"), ("status", "Status"), ("detail", "Detail")]),
                unsafe_allow_html=True)

    section_title("Board Type (CIQ vs EDP vs RFDS)")
    st.markdown(render_table(results.get("board_type", []), columns=[
        ("node", "Node"), ("ciq_du_type", "CIQ DU Type"), ("edp_model", "EDP Model"),
        ("rfds_agrees", "RFDS Agrees"), ("status", "Status"), ("note", "Note")]),
                unsafe_allow_html=True)

    section_title("XMU Port Overlap (RIport uniqueness)")
    st.markdown(render_table(results.get("xmu_port_overlap", []), columns=[
        ("node", "Node"), ("cell", "Cell"), ("du_type", "DU Type"), ("xmu", "XMU"),
        ("xmu_ports", "XMU Ports"), ("status", "Status"), ("note", "Note")]),
                unsafe_allow_html=True)

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

    section_title("Warnings & Comments")
    warn_keys = ["warn_xmu", "warn_params_4g", "warn_params_5g", "warn_pci", "warn_radio_type",
                 "warn_sector_swap", "warn_nr_tac", "warn_air_radio", "warn_antenna"]
    all_warnings = []
    for k in warn_keys:
        all_warnings += results.get(k, [])
    all_warnings += results.get("unavailable_notes", [])
    if all_warnings:
        for w in all_warnings:
            st.markdown(f'<div class="qkx-warn-line">{esc(w)}</div>', unsafe_allow_html=True)
    else:
        st.caption("No warnings.")

    with st.expander("RRNRBL Checklist", expanded=False):
        checklist = state["checklist"]
        render_rrnrbl_checklist(checklist)

    with st.expander("RFDS vs CIQ & Pre vs CIQ", expanded=False):
        for label, key in [("Cells vs RFDS", "cells_vs_rfds"), ("Cell ID vs RFDS", "cell_id_vs_rfds"),
                            ("Radio Type vs RFDS", "radio_type"), ("Parameters — 4G (Pre vs CIQ)", "params_4g"),
                            ("Parameters — 5G (Pre vs CIQ)", "params_5g"), ("Sector/TX-RX/Power (Pre vs CIQ)", "sector_swap")]:
            st.markdown(f"**{label}**")
            st.markdown(render_table(results.get(key, [])), unsafe_allow_html=True)

    with st.expander("CIQ Sanity Check", expanded=False):
        sanity_rows = (results.get("pci_4g", []) + results.get("pci_5g", []) + results.get("antenna", [])
                       + results.get("port_uniqueness", []) + results.get("sef_fru", [])
                       + results.get("radio_sharing", []) + results.get("nbiot", []))
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
