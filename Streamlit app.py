"""
Streamlit front-end for the Pre-Scripting Validation tool.

Styling matches QUICKIX (ashhureddy/integration-template-generator) exactly -
same sticky navy topbar, same button gradient, same card style, same MasTec
accent palette (Prussian Blue #00284e, Endeavour #024ea4, Orange #ff5b24) -
with the real MasTec logo image in place of QUICKIX's CSS-text placeholder.

Tab layout mirrors the QUICKIX HTML tool's top navigation, so the same
standalone tools work independently AND feed a Consolidated Report, exactly
like the HTML version's four embedded sub-tools + consolidated report page:

    Consolidated Report   - full CIQ+EDP(+RFDS)(+kget logs) run -> PDF +
                            RRNRBL checklist download, plus every
                            intermediate result shown live (Pre/Post Config,
                            SOW, Warnings & Comments, per-rule tables) -
                            same content the PDF has, just live in-app too.
    RFDS Checker          - standalone RFDS-only (or RFDS+CIQ) cell table,
                            mirrors the embedded "RFDS to CIQ Auto-Validator".
    EDP Validator         - standalone EDP-only (or EDP+CIQ) node table,
                            mirrors the embedded "EDP Validator".
    CIQ Checks            - standalone CIQ-only site-wide checks (PCI/antenna
                            uniqueness/port uniqueness/SEF-FRU/radio sharing),
                            mirrors the embedded "Audit -> CIQ Checks" tab.
    Audit                 - standalone CIQ + kget-log Pre-vs-Post comparison
                            (RF params, radio type, sector swap, NR TAC),
                            mirrors the embedded "Audit -> Pre vs Post" tab.
    Pre checks (AMOS)     - standalone kget-log-only per-node cell inventory,
                            mirrors the embedded "Audit -> AMOS" tab.
    CR Desc               - CR description string generator from CIQ + kget
                            logs, mirrors the embedded "CR Desc" tab.

Run locally:    streamlit run "Streamlit app.py"
Deploy:         push this repo to GitHub, then deploy on share.streamlit.io
"""
import base64
import datetime
import io
import os
import tempfile

import pandas as pd
import streamlit as st

import urllib.parse

import antenna_resolve as ar
import amos_view as av
import band_labels as bl
import checks_node as cn
import checks_sector as cs
import ciq_edp_reader as cer
import ciq_view as cv
import edp_checks as ec
import log_parser as lp
import pre_cell_inventory as pci
import pre_extract as pe
import pre_post_config as ppc
import rfds_extract as rf
import rrnrbl_checklist as rc
import run_validation as rv
import scope_of_work_text as sowt
import sow_analysis as sa
import warnings_text as wt

st.set_page_config(page_title="Pre-Scripting Validation", page_icon="\U0001F4E1", layout="wide")

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mastec_logo_trim.png")


def _logo_b64():
    if not os.path.exists(LOGO_PATH):
        return None
    with open(LOGO_PATH, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# ---- sticky top bar + shared styling (same palette/structure as QUICKIX) ----
_logo = _logo_b64()
_logo_html = (f'<img src="data:image/png;base64,{_logo}" style="height:28px;filter:brightness(0) invert(1);" />'
              if _logo else '<div class="qkx-logo">MAS<span>TEC</span></div>')

st.markdown(f"""
<style>
  .stApp {{
      background: linear-gradient(180deg, #eef3fa 0%, #f7f9fc 100%);
  }}
  .qkx-topbar {{
      position: sticky; top: 0; z-index: 999;
      display: flex; justify-content: space-between; align-items: center;
      padding: 0.9rem 1.75rem; margin: -1rem -1rem 1.5rem -1rem;
      background: linear-gradient(90deg, #011b36 0%, #012a4e 100%);
      border-bottom: 1px solid rgba(255,91,36,0.55);
      box-shadow: 0 4px 18px rgba(0,0,0,0.2);
  }}
  .qkx-topbar .qkx-logo {{ font-size: 1.4rem; font-weight: 900; color: #ffffff; letter-spacing: 1px; }}
  .qkx-topbar .qkx-logo span {{ color: #ffffff; }}
  .qkx-topbar .qkx-credit {{ font-size: 0.78rem; color: #cfe0f5; text-align: right; line-height: 1.3; }}

  .qkx-hero {{ text-align: center; margin: 1rem 0 2.5rem 0; }}
  .qkx-hero h1 {{
      font-size: 2.6rem; font-weight: 900; letter-spacing: 2px; margin-bottom: 0.3rem;
      color: #012a4e;
  }}
  .qkx-hero p {{ color: #4a5b70; font-size: 1.02rem; }}

  div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {{
      border-radius: 10px; font-weight: 700; border: 1.5px solid #013a6b;
      background: linear-gradient(135deg, #024ea4, #013a6b); color: #ffffff;
      box-shadow: 0 3px 8px rgba(1,42,78,0.25);
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
  }}
  div[data-testid="stButton"] button:hover, div[data-testid="stFormSubmitButton"] button:hover {{
      border-color: #ff5b24; color: #ffffff; transform: translateY(-1px);
      box-shadow: 0 6px 14px rgba(255,91,36,0.35);
  }}
  div[data-testid="stButton"] button:active, div[data-testid="stFormSubmitButton"] button:active {{ transform: translateY(0); }}

  /* Bordered containers / file uploaders - clean light cards */
  div[data-testid="stVerticalBlockBorderWrapper"], div[data-testid="stFileUploaderDropzone"] {{
      background: #ffffff !important;
      border: 1px solid #dde5ef !important;
      border-radius: 12px !important;
      box-shadow: 0 2px 10px rgba(1,42,78,0.06);
  }}

  .qkx-section-label {{ font-weight: 700; color: #012a4e; font-size: 0.95rem; margin: 0.6rem 0 0.3rem 0; }}

  div[data-testid="stTabs"] button[data-baseweb="tab"] {{ font-weight: 700; color: #012a4e; }}
  div[data-testid="stTabs"] button[aria-selected="true"] {{ color: #ff5b24; border-bottom-color: #ff5b24 !important; }}
</style>
<div class="qkx-topbar">
  {_logo_html}
  <div class="qkx-credit">Pre-Scripting Validation<br>Powered by <b>MASTEC</b></div>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="qkx-hero">
  <h1>PRE-SCRIPT</h1>
  <p>Pre-Scripting Validation — CIQ vs EDP vs RFDS vs Pre kget-all</p>
</div>
""", unsafe_allow_html=True)


# ── Shared helpers ────────────────────────────────────────────────────────

def _save_upload(f, tmp_dir):
    path = os.path.join(tmp_dir, f.name)
    with open(path, "wb") as fh:
        fh.write(f.getbuffer())
    return path


def _decode_logs(log_files):
    """{node_id: text} for every uploaded kget-all log."""
    node_logs = {}
    for lf in (log_files or []):
        text = lf.getvalue().decode("utf-8", errors="replace")
        node_id = pe.node_id_from_log(text) or os.path.splitext(lf.name)[0]
        node_logs[node_id] = text
    return node_logs


STATUS_COLOR = {"MATCH": "#d1fae5", "MISMATCH": "#fee2e2", "INFO": "#dbeafe",
                "SKIPPED": "#f1f5f9", "NA": "#f1f5f9", "WARN": "#fef3c7"}


def _style_map(styler, func, subset):
    """pandas 2.1 renamed Styler.applymap -> Styler.map (removed applymap in
    later versions); this works on either."""
    if hasattr(styler, "map"):
        return styler.map(func, subset=subset)
    return styler.applymap(func, subset=subset)


def _style_status(df):
    if "status" not in df.columns:
        return df
    return _style_map(df.style, lambda v: f"background-color:{STATUS_COLOR.get(str(v), '')}", ["status"])


def show_rows(rows, empty_msg="No data.", drop=("rule",)):
    """Renders a list of result dicts as a sortable, status-colored table -
    same per-cell/per-node granularity as the HTML tool's collapsible
    RFDS vs CIQ / CIQ Sanity Check / EDP Checks drop-downs (every row here
    is one cell or one node, never pre-aggregated)."""
    if not rows:
        st.caption(empty_msg)
        return
    df = pd.DataFrame(rows)
    for col in drop:
        if col in df.columns:
            df = df.drop(columns=[col])
    st.dataframe(_style_status(df), use_container_width=True, hide_index=True)


def df_download_button(rows_by_sheet, label, file_name, key):
    """rows_by_sheet: {sheet_name: [row_dict, ...]}. Renders an Excel
    download button, one sheet per entry - same "Export Excel" affordance
    every HTML sub-tool has."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        wrote_any = False
        for sheet, rows in rows_by_sheet.items():
            if not rows:
                continue
            pd.DataFrame(rows).to_excel(writer, sheet_name=sheet[:31], index=False)
            wrote_any = True
        if not wrote_any:
            pd.DataFrame([{"Result": "No data"}]).to_excel(writer, sheet_name="Result", index=False)
    st.download_button(label, data=buf.getvalue(), file_name=file_name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True, key=key)


def render_revision_history(sheet_name, rows):
    """Revision History — same "Revision History" button/modal the HTML
    tool has, reading the CIQ's own 'Revision History' sheet (columns A-E,
    two stacked mini-tables, header auto-detected per row)."""
    with st.expander(f"Revision History{f' ({sheet_name})' if sheet_name else ''}", expanded=False):
        if not sheet_name:
            st.caption("No 'Revision History' sheet found in this CIQ.")
            return
        display_rows = [[str(v) for v in row] for row in rows]
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)


def mailto_link(to_addr, cc_addr, subject, body):
    """Builds a mailto: link exactly like the HTML tool's composeMail() -
    opens the user's own mail client with subject/body pre-filled."""
    params = {"cc": cc_addr, "subject": subject, "body": body}
    return f"mailto:{to_addr}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def render_edp_node_cards(node_results):
    """Node cards — mirrors the HTML EDP Validator's checklist view: one
    card per expected node, PASS/FAIL/WARN/MISSING status chip, checks
    listed underneath with a pass/fail/warn/info icon each."""
    icon = {"pass": "\u2705", "fail": "\u274c", "warn": "\u26a0\ufe0f", "info": "\u2139\ufe0f"}
    status_color = {"PASS": "#059669", "FAIL": "#dc2626", "WARN": "#f59e0b", "MISSING": "#94a3b8"}
    for n in node_results:
        status = n["status"]
        title = f"{n['name']} — {n['role']} — {status}"
        with st.expander(title, expanded=(status in ("FAIL", "MISSING"))):
            st.markdown(f"<span style='color:{status_color.get(status,'#333')};font-weight:700'>{status}</span>"
                        + (f"  ·  {n.get('tech','')}" if n.get("tech") else ""), unsafe_allow_html=True)
            if not n["found"]:
                st.caption(f"EDP not published for this node — \"{n['name']}\" was not found in SITE_NAME.")
                continue
            st.caption(f"Cabinet: **{n.get('cabinet') or '—'}**  ·  Cabinet ID: **{n.get('cabinet_id') or '—'}**")
            for c in n["checks"]:
                st.markdown(f"{icon.get(c['status'], '•')} **{c['label']}** — {c['detail']}")


def render_edp_global_checks(global_checks, unexpected):
    st.markdown('<div class="qkx-section-label">Cross-node Conflicts</div>', unsafe_allow_html=True)
    pc, vc = global_checks["port_clashes"], global_checks["vlan_clashes"]
    if not pc and not vc:
        st.caption("No port or VLAN reuse detected across the EDP file.")
    else:
        for p in pc:
            st.markdown(f"- Port **{p['port']}** on **{p['device']}** — {', '.join(sorted(p['sites']))}")
        for v in vc:
            st.markdown(f"- Bearer VLAN **{v['vlan']}** on **{v['device']}** — {', '.join(sorted(v['sites']))}")

    st.markdown('<div class="qkx-section-label">Unexpected Nodes in EDP</div>', unsafe_allow_html=True)
    if not unexpected:
        st.caption("Every SITE_NAME row in the EDP maps back to a node requested in the CIQ.")
    else:
        show_rows(unexpected, drop=())


PIPE_FIELDS_4G = ["earfcndl", "earfcnul", "dlChannelBandwidth", "ulChannelBandwidth"]
PIPE_FIELDS_5G = ["arfcnDL", "arfcnUL", "bSChannelBwDL", "bSChannelBwUL", "ssbfrequency"]


def render_pipe_compare_table(rows, pipe_fields, empty_msg="No data."):
    """Same PRE|POST colored-field format as the HTML tool's Pre-vs-Post
    compare tables — each cell shows 'pre | ciq', green when they match,
    red when they don't."""
    if not rows:
        st.caption(empty_msg)
        return
    df = pd.DataFrame(rows)
    present = [f for f in pipe_fields if f in df.columns]

    def _pipe_style(v):
        parts = [p.strip() for p in str(v).split("|")]
        if len(parts) == 2 and parts[0] and parts[0].upper() != "NA" and parts[0] != parts[1]:
            return "background-color:#fee2e2;color:#991b1b;font-weight:600"
        return "background-color:#d1fae5;color:#065f46;font-weight:600"

    drop_cols = [c for c in ("rule",) if c in df.columns]
    df = df.drop(columns=drop_cols)
    st.dataframe(_style_map(df.style, _pipe_style, present), use_container_width=True, hide_index=True)


RULE_LABELS = {
    "sw_version": "SW Version (#1)", "identity": "Identity (#2/12/14/17)",
    "primary_secondary": "Primary/Secondary (#3/#31)", "board_type": "Board Type (#5/#15/#13)",
    "xmu": "XMU (#27)", "tac": "TAC (#16)", "cells_vs_rfds": "Cells vs RFDS (#6/#18)",
    "cell_id_vs_rfds": "Cell ID vs RFDS (#6/#24)", "params_4g": "4G RF Parameters (#19)",
    "params_5g": "5G RF Parameters (#19)", "pci_4g": "4G PCI Uniqueness", "pci_5g": "5G PCI Uniqueness",
    "radio_type": "Radio Type (#6/#13)", "sector_swap": "Sector/TX-RX/Power Swap (#21/#22/#32)",
    "radio_sharing": "Radio Sharing", "port_uniqueness": "Port Uniqueness",
    "xmu_port_overlap": "XMU Port Overlap", "antenna": "Antenna Uniqueness (#27/#34)",
    "nbiot": "NB-IoT", "nr_tac": "NR TAC / SA-NSA (#17)", "sef_fru": "SEF / FRU",
}
WARN_LABELS = {
    "warn_xmu": "XMU", "warn_params_4g": "4G Parameters", "warn_params_5g": "5G Parameters",
    "warn_pci": "PCI", "warn_radio_type": "Radio Type", "warn_sector_swap": "Sector Swap",
    "warn_nr_tac": "NR TAC / SA-NSA", "warn_air_radio": "AIR Radio", "warn_antenna": "Antenna",
}


def render_warnings_and_comments(results):
    """Warnings & Comments — same section name/spirit as the HTML tool's
    consolidated report; wording here stays exactly as confirmed against
    the Blueprint (not rephrased into the HTML tool's band+sector wording,
    since that wording was independently confirmed for this rule-based
    checklist and shouldn't be silently overridden)."""
    any_warn = False
    for key, label in WARN_LABELS.items():
        lines = results.get(key) or []
        if not lines:
            continue
        any_warn = True
        st.markdown(f"**{label}**")
        for line in lines:
            st.markdown(f"- {line}")
    if results.get("sa_note"):
        any_warn = True
        st.markdown(f"**SA Configuration** — {results['sa_note']}")
    if not any_warn:
        st.caption("No warnings — all checked cells passed validation.")
    if results.get("unavailable_notes"):
        with st.expander("Checks not available from Pre kget-all logs"):
            for n in results["unavailable_notes"]:
                st.markdown(f"- {n}")


def render_result_tables(results, groups):
    """groups: [(section_title, [result_key, ...]), ...] — each result_key's
    rows render as its own sortable, status-colored, cell/node-wise table,
    grouped under a collapsible section (mirrors the HTML tool's collapsible
    RFDS vs CIQ / CIQ Sanity Check / EDP Checks drop-downs)."""
    for title, keys in groups:
        present = [k for k in keys if results.get(k)]
        if not present:
            continue
        with st.expander(title, expanded=False):
            for k in present:
                st.markdown(f"**{RULE_LABELS.get(k, k)}**")
                show_rows(results[k])


# ── Tabs (mirrors the HTML tool's top navigation) ──────────────────────────
(tab_consolidated, tab_rfds, tab_edp, tab_ciq, tab_audit, tab_amos, tab_crdesc) = st.tabs([
    "Consolidated Report", "RFDS Checker", "EDP Validator",
    "CIQ Checks", "Audit", "Pre checks (AMOS)", "CR Desc",
])

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Consolidated Report
# ═══════════════════════════════════════════════════════════════════════════
with tab_consolidated:
    with st.form("inputs"):
        st.markdown('<div class="qkx-section-label">Required files</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            ciq_file = st.file_uploader("CIQ (.xlsx)", type=["xlsx"], key="cr_ciq")
        with c2:
            edp_file = st.file_uploader("EDP (.xls)", type=["xls"], key="cr_edp")

        st.markdown('<div class="qkx-section-label">Optional</div>', unsafe_allow_html=True)
        c3, c4 = st.columns(2)
        with c3:
            rfds_file = st.file_uploader("RFDS (.pdf)", type=["pdf"], key="cr_rfds")
        with c4:
            log_files = st.file_uploader(
                "Pre kget-all logs (.log / .txt) — one per node",
                type=["log", "txt"], accept_multiple_files=True, key="cr_logs",
            )

        submitted = st.form_submit_button("Generate report", type="primary", use_container_width=True)

    if submitted:
        if not ciq_file or not edp_file:
            st.error("CIQ and EDP are both required.")
            st.stop()

        with st.spinner("Running validation checks..."):
            with tempfile.TemporaryDirectory() as tmp:
                ciq_path = _save_upload(ciq_file, tmp)
                edp_path = _save_upload(edp_file, tmp)
                rfds_path = _save_upload(rfds_file, tmp) if rfds_file else None

                node_log_paths = {}
                for lf in (log_files or []):
                    text = lf.getvalue().decode("utf-8", errors="replace")
                    node_id = pe.node_id_from_log(text) or os.path.splitext(lf.name)[0]
                    log_path = os.path.join(tmp, lf.name)
                    with open(log_path, "w") as f:
                        f.write(text)
                    node_log_paths[node_id] = log_path

                out_pdf = os.path.join(tmp, "validation_report.pdf")
                try:
                    (out_pdf, results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages,
                     pre_text, post_text, scope_lines, sow) = rv.run(
                        ciq_path, edp_path, rfds_path, node_log_paths, out_pdf
                    )
                except Exception as e:
                    st.error(f"Report generation failed: {e}")
                    st.stop()

                with open(out_pdf, "rb") as f:
                    pdf_bytes = f.read()

                checklist = rc.build_checklist(results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages)
                site_id_fa = " / ".join(v for v in (site_details.get("site_id"), site_details.get("fa_code")) if v)
                checklist_xlsx = rc.fill_checklist_xlsx(checklist, site_id_fa)
                rev_sheet, rev_rows = cer.read_revision_history(ciq_wb)

        st.session_state["consolidated"] = {
            "pdf_bytes": pdf_bytes, "checklist_xlsx": checklist_xlsx, "checklist": checklist,
            "site_id_fa": site_id_fa, "results": results, "site_details": site_details,
            "pre_text": pre_text, "post_text": post_text, "scope_lines": scope_lines, "sow": sow,
            "rev_sheet": rev_sheet, "rev_rows": rev_rows,
        }

    state = st.session_state.get("consolidated")
    if state:
        st.success("Report generated.")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("\u2b07\ufe0f Download PDF", data=state["pdf_bytes"], file_name="validation_report.pdf",
                                mime="application/pdf", use_container_width=True)
        with c2:
            st.download_button("\u2b07\ufe0f Download filled RRNRBL Checklist (.xlsx)", data=state["checklist_xlsx"],
                                file_name="Checklist_RRNRBL_filled.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True)

        render_revision_history(state["rev_sheet"], state["rev_rows"])

        st.markdown('<div class="qkx-section-label">Pre / Post Configuration</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.markdown(f"**Pre:** {state['pre_text'] or '—'}")
        c2.markdown(f"**Post:** {state['post_text'] or '—'}")

        st.markdown('<div class="qkx-section-label">SOW Summary</div>', unsafe_allow_html=True)
        if state["scope_lines"]:
            for line in state["scope_lines"]:
                st.markdown(f"- {line}")
        else:
            st.caption("No additions, deletions, sector movements, or retunes detected.")

        st.markdown('<div class="qkx-section-label">Warnings & Comments</div>', unsafe_allow_html=True)
        render_warnings_and_comments(state["results"])

        st.markdown('<div class="qkx-section-label">Detailed Checks (cell/node-wise)</div>', unsafe_allow_html=True)
        render_result_tables(state["results"], [
            ("Node Checks", ["sw_version", "identity", "primary_secondary", "board_type", "xmu", "tac"]),
            ("RFDS vs CIQ", ["cells_vs_rfds", "cell_id_vs_rfds"]),
            ("Pre vs CIQ (Audit)", ["params_4g", "params_5g", "pci_4g", "pci_5g", "radio_type",
                                     "sector_swap", "nr_tac"]),
            ("CIQ Site-wide Checks", ["radio_sharing", "port_uniqueness", "xmu_port_overlap",
                                       "antenna", "nbiot", "sef_fru"]),
        ])

        st.markdown('<div class="qkx-section-label">RRNRBL Checklist</div>', unsafe_allow_html=True)
        st.caption(f"Site ID/FA: **{state['site_id_fa'] or 'not found'}**  ·  "
                   f"Date: **{datetime.date.today().strftime('%m/%d/%Y')}** — both auto-filled in the downloaded checklist above.")

        STATUS_ICON = {"match": "\u2705", "mismatch": "\u274c", "manual": "\u270f\ufe0f",
                       "unknown": "\u2754", "na": "\u2796", "info": "\u2139\ufe0f"}
        counts = {}
        for row in state["checklist"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        st.write(" &nbsp; ".join(f"{STATUS_ICON.get(k, '?')} **{v}** {k}" for k, v in counts.items()), unsafe_allow_html=True)

        cats = []
        for row in state["checklist"]:
            if not cats or cats[-1]["cat"] != row["cat"]:
                cats.append({"cat": row["cat"], "rows": []})
            cats[-1]["rows"].append(row)

        for c in cats:
            with st.expander(f"{c['cat']}  ({sum(1 for r in c['rows'] if r['status'] == 'mismatch')} mismatch)", expanded=False):
                last_sub = object()
                for row in c["rows"]:
                    if row["sub"] != last_sub:
                        if row["sub"]:
                            st.markdown(f"**{row['sub']}**")
                        last_sub = row["sub"]
                    icon = STATUS_ICON.get(row["status"], "?")
                    st.markdown(f"{icon} **{row['item']}** &nbsp;·&nbsp; {row['detail']}", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB: RFDS Checker — standalone RFDS-only (or RFDS+CIQ) validator, mirrors
# the HTML tool's embedded "RFDS to CIQ Auto-Validator".
# ═══════════════════════════════════════════════════════════════════════════
with tab_rfds:
    st.markdown('<div class="qkx-section-label">Files</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        rfds_only_file = st.file_uploader("RFDS (.pdf)", type=["pdf"], key="rfds_only_rfds")
    with c2:
        rfds_only_ciq = st.file_uploader("CIQ (.xlsx) — optional, enables cell/RCN comparison", type=["xlsx"], key="rfds_only_ciq")
    with c3:
        rfds_only_antfile = st.file_uploader(
            "Antenna Info XLSX — optional, resolves CIQ antenna names",
            type=["xlsx"], key="rfds_only_ant",
            help="Columns: kget_ant_model / rfds_ant_model. Only used when CIQ is also uploaded.")

    if st.button("Run RFDS check", type="primary", key="rfds_run"):
        if not rfds_only_file:
            st.error("RFDS is required.")
            st.stop()
        with st.spinner("Parsing RFDS..."):
            rfds_bytes = rfds_only_file.getvalue()
            pages = rf.load_rfds_pages(rfds_bytes)
            site_details = rf.extract_site_details(pages)
            cell_details = rf.extract_cell_details(pages)

            compare_rows = []
            rev_sheet, rev_rows = None, []
            antenna_rows = []
            if rfds_only_ciq:
                with tempfile.TemporaryDirectory() as tmp:
                    ciq_path = _save_upload(rfds_only_ciq, tmp)
                    ciq_wb = cer.load_ciq(ciq_path)
                mm_rows = cer.mixed_mode_rows(ciq_wb)
                rev_sheet, rev_rows = cer.read_revision_history(ciq_wb)
                for mm in mm_rows:
                    node_id = str(mm.get("Node to be built as") or "").strip()
                    e_name = str(mm.get("eNodeB Name") or "").strip()
                    g_name = str(mm.get("gNodeB Name") or "").strip()
                    compare_rows += cs.check_cells_vs_rfds(node_id, ciq_wb, pages, e_name, g_name)
                    compare_rows += cs.check_cell_id_vs_rfds(node_id, None, ciq_wb, pages, e_name, g_name)

                # Antenna resolution pipeline (exact/normalised/suffix-strip),
                # applied to the CIQ's own antenna model columns against an
                # optionally-uploaded Antenna Info map — same confidence tiers
                # as the HTML tool's resolveAntenna().
                antenna_map = {}
                if rfds_only_antfile:
                    ant_wb = cer.load_ciq(_save_upload(rfds_only_antfile, tempfile.mkdtemp()))
                    ant_sheet = next((s for s in ant_wb.sheetnames if "antenna" in s.lower()), ant_wb.sheetnames[0])
                    antenna_map = ar.build_antenna_map(cer.sheet_rows_as_dicts(ant_wb[ant_sheet]))
                for row in cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]) if "eUtran Parameters" in ciq_wb.sheetnames else []:
                    raw = str(row.get("antenna model") or "").strip()
                    if not raw:
                        continue
                    resolved = ar.resolve_antenna(raw, antenna_map)
                    antenna_rows.append({"cell": row.get("EutranCellFDDId"), "raw_ciq_antenna": raw,
                                          "resolved_antenna": resolved["value"],
                                          "match_method": resolved["method"], "matched_kget": resolved["matched_kget"]})
                for row in cer.sheet_rows_as_dicts(ciq_wb["5G Info"]) if "5G Info" in ciq_wb.sheetnames else []:
                    raw = str(row.get("Antenna Type") or "").strip()
                    if not raw:
                        continue
                    resolved = ar.resolve_antenna(raw, antenna_map)
                    antenna_rows.append({"cell": row.get("NRCellDU"), "raw_ciq_antenna": raw,
                                          "resolved_antenna": resolved["value"],
                                          "match_method": resolved["method"], "matched_kget": resolved["matched_kget"]})

            cell_rows = [{"cell": k, **v} for k, v in cell_details.items()]
            st.session_state["rfds_only"] = {"site_details": site_details, "cell_rows": cell_rows,
                                              "compare_rows": compare_rows, "rev_sheet": rev_sheet,
                                              "rev_rows": rev_rows, "antenna_rows": antenna_rows}

    state = st.session_state.get("rfds_only")
    if state:
        render_revision_history(state["rev_sheet"], state["rev_rows"])
        st.markdown('<div class="qkx-section-label">Site Details</div>', unsafe_allow_html=True)
        sd = state["site_details"]
        if sd:
            st.write(" &nbsp;·&nbsp; ".join(f"**{k}**: {v}" for k, v in sd.items()))
        else:
            st.caption("Site Details not found in RFDS.")

        st.markdown('<div class="qkx-section-label">Cell Details (Final)</div>', unsafe_allow_html=True)
        show_rows(state["cell_rows"], empty_msg="No 'Cell Details (Final)' table found in RFDS.")

        if state["compare_rows"]:
            st.markdown('<div class="qkx-section-label">RFDS vs CIQ</div>', unsafe_allow_html=True)
            show_rows(state["compare_rows"])

        if state["antenna_rows"]:
            st.markdown('<div class="qkx-section-label">Antenna Resolution (CIQ antenna model)</div>', unsafe_allow_html=True)
            adf = pd.DataFrame(state["antenna_rows"])

            def _method_style(v):
                return f"color:{ar.CONFIDENCE_COLOR.get(str(v), '')};font-weight:700"
            st.dataframe(_style_map(adf.style, _method_style, ["match_method"]),
                         use_container_width=True, hide_index=True)
            st.caption(" · ".join(f"{m}: {ar.CONFIDENCE_LABEL[m]}" for m in ("exact", "normalised", "suffix-strip", "none")))

        df_download_button({"Cell Details": state["cell_rows"], "RFDS vs CIQ": state["compare_rows"],
                             "Antenna Resolution": state["antenna_rows"]},
                            "\u2b07\ufe0f Export Excel", "rfds_checker.xlsx", "rfds_export")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: EDP Validator — standalone EDP-only (or EDP+CIQ) validator, mirrors
# the HTML tool's embedded "EDP Validator".
# ═══════════════════════════════════════════════════════════════════════════
with tab_edp:
    st.markdown('<div class="qkx-section-label">Files</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        edp_only_file = st.file_uploader("EDP (.xls)", type=["xls"], key="edp_only_edp")
    with c2:
        edp_only_ciq = st.file_uploader("CIQ (.xlsx) — optional, enables the full node checklist",
                                         type=["xlsx"], key="edp_only_ciq")

    if st.button("Run EDP check", type="primary", key="edp_run"):
        if not edp_only_file:
            st.error("EDP is required.")
            st.stop()
        with st.spinner("Parsing EDP..."):
            with tempfile.TemporaryDirectory() as tmp:
                edp_path = _save_upload(edp_only_file, tmp)
                ws = cer.load_edp(edp_path)
                _, edp_rows = cer.build_edp_index(ws)

                validation = None
                rev_sheet, rev_rows = None, []
                if edp_only_ciq:
                    ciq_path = _save_upload(edp_only_ciq, tmp)
                    ciq_wb = cer.load_ciq(ciq_path)
                    rev_sheet, rev_rows = cer.read_revision_history(ciq_wb)
                    validation = ec.run_edp_validation(ciq_wb, edp_rows)

        st.session_state["edp_only"] = {"edp_rows": edp_rows, "validation": validation,
                                         "rev_sheet": rev_sheet, "rev_rows": rev_rows}

    state = st.session_state.get("edp_only")
    if state:
        render_revision_history(state["rev_sheet"], state["rev_rows"])

        if state["validation"]:
            v = state["validation"]
            total = len(v["node_results"])
            counts = {}
            for n in v["node_results"]:
                counts[n["status"]] = counts.get(n["status"], 0) + 1
            st.markdown('<div class="qkx-section-label">Node Checklist</div>', unsafe_allow_html=True)
            st.write(" &nbsp; ".join(f"**{k}**: {v_}" for k, v_ in {"Total": total, **counts}.items()))
            render_edp_node_cards(v["node_results"])
            render_edp_global_checks(v["global_checks"], v["unexpected"])

            flat_checks = []
            for n in v["node_results"]:
                for c in n["checks"]:
                    flat_checks.append({"node": n["name"], "role": n["role"], "status_node": n["status"],
                                        "check": c["label"], "check_status": c["status"], "detail": c["detail"]})
            df_download_button({"EDP Rows": state["edp_rows"], "Node Checklist": flat_checks,
                                 "Unexpected Nodes": v["unexpected"]},
                                "\u2b07\ufe0f Export Excel", "edp_validator.xlsx", "edp_export")
        else:
            st.markdown('<div class="qkx-section-label">EDP Rows (raw)</div>', unsafe_allow_html=True)
            show_rows(state["edp_rows"], empty_msg="No EDP rows found.", drop=())
            st.caption("Upload a CIQ too for the full per-node checklist (cabinet, ports, VLANs, IPv6).")
            df_download_button({"EDP Rows": state["edp_rows"]}, "\u2b07\ufe0f Export Excel", "edp_validator.xlsx", "edp_export")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: CIQ Checks — standalone, CIQ-only site-wide checks (PCI/antenna/port
# uniqueness, SEF-FRU, radio sharing), mirrors the HTML tool's CIQ Checks tab.
# ═══════════════════════════════════════════════════════════════════════════
with tab_ciq:
    ciq_checks_file = st.file_uploader("CIQ (.xlsx)", type=["xlsx"], key="ciqchecks_ciq")

    if st.button("Run CIQ checks", type="primary", key="ciqchecks_run"):
        if not ciq_checks_file:
            st.error("CIQ is required.")
            st.stop()
        with st.spinner("Running CIQ checks..."):
            with tempfile.TemporaryDirectory() as tmp:
                ciq_path = _save_upload(ciq_checks_file, tmp)
                ciq_wb = cer.load_ciq(ciq_path)
            mm_rows = cer.mixed_mode_rows(ciq_wb)
            rev_sheet, rev_rows = cer.read_revision_history(ciq_wb)

            pci_rows, nr_pci_rows = [], []
            node_names = []
            for mm in mm_rows:
                node_id = str(mm.get("Node to be built as") or "").strip()
                if node_id:
                    node_names.append(node_id)
                e_name = str(mm.get("eNodeB Name") or "").strip()
                g_name = str(mm.get("gNodeB Name") or "").strip()
                pci_rows += cs.check_pci_uniqueness(node_id, ciq_wb, e_name)
                nr_pci_rows += cs.check_nr_pci_uniqueness(node_id, ciq_wb, g_name)

            site_label = str(mm_rows[0].get("Node to be built as") or "").strip() if mm_rows else ""
            results = {
                "pci_4g": pci_rows, "pci_5g": nr_pci_rows,
                "radio_sharing": cs.check_radio_sharing_pairs(site_label, ciq_wb),
                "port_uniqueness": cs.check_port_uniqueness(site_label, ciq_wb),
                "antenna": cs.check_antenna_uniqueness(site_label, ciq_wb),
                "sef_fru": cs.check_sef_fru(site_label, ciq_wb),
            }
        st.session_state["ciq_checks"] = {"results": results, "rev_sheet": rev_sheet,
                                           "rev_rows": rev_rows, "node_names": node_names,
                                           "node_integration": cv.build_node_integration(ciq_wb),
                                           "lte_params": cv.build_param_table(ciq_wb, "eUtran Parameters", cv.LTE_PARAM_COLS),
                                           "nr_params": cv.build_param_table(ciq_wb, "5G Info", cv.NR_PARAM_COLS)}

    state = st.session_state.get("ciq_checks")
    if state:
        render_revision_history(state["rev_sheet"], state["rev_rows"])

        st.markdown('<div class="qkx-section-label">Node Integration</div>', unsafe_allow_html=True)
        show_rows(state["node_integration"], drop=())

        st.markdown('<div class="qkx-section-label">LTE / NR Parameters</div>', unsafe_allow_html=True)
        band_opts = ["All"] + sorted({r.get("eUTRA operating band") for r in state["lte_params"] if r.get("eUTRA operating band")}
                                      | {r.get("Operating Band") for r in state["nr_params"] if r.get("Operating Band")})
        node_opts = ["All"] + sorted({n for n in state["node_names"] if n})
        c1, c2 = st.columns(2)
        band_filter = c1.selectbox("Band filter", band_opts, key="ciqchecks_bandfilter")
        node_filter = c2.selectbox("Node (eNB/gNB ID) filter", node_opts, key="ciqchecks_nodefilter")

        lte_view = [r for r in state["lte_params"]
                    if (band_filter == "All" or r.get("eUTRA operating band") == band_filter)
                    and (node_filter == "All" or str(r.get("EutranCellFDDId", "")).startswith(node_filter))]
        nr_view = [r for r in state["nr_params"]
                   if (band_filter == "All" or r.get("Operating Band") == band_filter)
                   and (node_filter == "All" or str(r.get("NRCellDU", "")).startswith(node_filter))]
        with st.expander(f"LTE eUtran Parameters ({len(lte_view)} cells)", expanded=False):
            show_rows(lte_view, drop=())
        with st.expander(f"5G NR Parameters ({len(nr_view)} cells)", expanded=False):
            show_rows(nr_view, drop=())

        st.markdown('<div class="qkx-section-label">Site-wide Checks</div>', unsafe_allow_html=True)
        render_result_tables(state["results"], [
            ("PCI Uniqueness", ["pci_4g", "pci_5g"]),
            ("Radio Sharing / Port Uniqueness / Antenna / SEF-FRU", ["radio_sharing", "port_uniqueness", "antenna", "sef_fru"]),
        ])
        df_download_button({**state["results"], "Node Integration": state["node_integration"],
                             "LTE Parameters": state["lte_params"], "NR Parameters": state["nr_params"]},
                            "\u2b07\ufe0f Export Excel", "ciq_checks.xlsx", "ciqchecks_export")

        mismatch_count = sum(1 for rows in state["results"].values() for r in rows if r.get("status") == "MISMATCH")
        st.markdown('<div class="qkx-section-label">Pre Integration Issue Mail</div>', unsafe_allow_html=True)
        fa_code = st.text_input("FA Code", key="ciqchecks_fa")
        if fa_code:
            node_str = "/".join(state["node_names"]) or "NODE"
            subject = f"{node_str} - {fa_code} <Pre Integration Issue>"
            body = (f"Hi Team,\n\n{mismatch_count} CIQ check issue(s) found (PCI/antenna/port uniqueness) "
                    f"on {node_str}. Please check and update the CIQ.")
            link = mailto_link("eran_design@quadgen.com", "eran_scripting@quadgen.com", subject, body)
            st.link_button("\u2709\ufe0f Compose Mail", link, use_container_width=True)
        else:
            st.caption("Enter a FA Code to compose the issue mail.")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Audit — standalone CIQ + kget-log Pre-vs-Post comparison, mirrors the
# HTML tool's Audit -> Pre vs Post tab (no EDP needed).
# ═══════════════════════════════════════════════════════════════════════════
with tab_audit:
    c1, c2 = st.columns(2)
    with c1:
        audit_ciq_file = st.file_uploader("CIQ (.xlsx)", type=["xlsx"], key="audit_ciq")
    with c2:
        audit_log_files = st.file_uploader("Pre kget-all logs (.log / .txt) — one per node",
                                            type=["log", "txt"], accept_multiple_files=True, key="audit_logs")

    if st.button("Run Audit", type="primary", key="audit_run"):
        if not audit_ciq_file or not audit_log_files:
            st.error("CIQ and at least one kget-all log are required.")
            st.stop()
        with st.spinner("Running Audit..."):
            with tempfile.TemporaryDirectory() as tmp:
                ciq_path = _save_upload(audit_ciq_file, tmp)
                ciq_wb = cer.load_ciq(ciq_path)
            node_logs = _decode_logs(audit_log_files)
            mm_rows = cer.mixed_mode_rows(ciq_wb)
            rev_sheet, rev_rows = cer.read_revision_history(ciq_wb)

            pre_pairs, pre_nodes = pci.build_pre_inventory(node_logs)
            sow = sa.classify_carriers(ciq_wb, mm_rows, pre_pairs, pre_nodes)
            pre_text, post_text = ppc.build_pre_post_config_text(node_logs, ciq_wb)
            scope_lines = sowt.scope_lines_to_readable_text(sowt.format_scope_of_work(sow, ciq_wb))

            results = {k: [] for k in ("params_4g", "params_5g", "pci_4g", "pci_5g",
                                        "radio_type", "sector_swap", "nr_tac")}
            for mm in mm_rows:
                node_id = str(mm.get("Node to be built as") or "").strip()
                e_name = str(mm.get("eNodeB Name") or "").strip()
                g_name = str(mm.get("gNodeB Name") or "").strip()
                log_text = node_logs.get(node_id)
                has_pre = bool(log_text)
                parsed = lp.parse_log(log_text) if log_text else []

                results["params_4g"] += cs.check_rf_params_4g(node_id, log_text, ciq_wb, has_pre)
                results["params_5g"] += cs.check_rf_params_5g(node_id, parsed, log_text, ciq_wb, has_pre)
                results["pci_4g"] += cs.check_pci_uniqueness(node_id, ciq_wb, e_name)
                results["pci_5g"] += cs.check_nr_pci_uniqueness(node_id, ciq_wb, g_name)
                results["radio_type"] += cs.check_radio_type(node_id, log_text, ciq_wb, None, e_name, g_name)
                results["sector_swap"] += cs.check_sector_swap_config(node_id, log_text, ciq_wb, e_name, g_name)
                results["nr_tac"] += cs.check_nr_tac(node_id, log_text, ciq_wb, has_pre, False, g_name)

        st.session_state["audit"] = {"pre_text": pre_text, "post_text": post_text,
                                      "scope_lines": scope_lines, "results": results,
                                      "rev_sheet": rev_sheet, "rev_rows": rev_rows}

    state = st.session_state.get("audit")
    if state:
        render_revision_history(state["rev_sheet"], state["rev_rows"])
        st.markdown('<div class="qkx-section-label">Pre / Post Configuration</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.markdown(f"**Pre:** {state['pre_text'] or '—'}")
        c2.markdown(f"**Post:** {state['post_text'] or '—'}")

        st.markdown('<div class="qkx-section-label">SOW Summary</div>', unsafe_allow_html=True)
        if state["scope_lines"]:
            for line in state["scope_lines"]:
                st.markdown(f"- {line}")
        else:
            st.caption("No additions, deletions, sector movements, or retunes detected.")

        st.markdown('<div class="qkx-section-label">Pre vs CIQ — RF Parameters (PRE | POST)</div>', unsafe_allow_html=True)
        st.caption("4G")
        render_pipe_compare_table(state["results"]["params_4g"], PIPE_FIELDS_4G, "No 4G cells with Pre data to compare.")
        st.caption("5G")
        render_pipe_compare_table(state["results"]["params_5g"], PIPE_FIELDS_5G, "No 5G cells with Pre data to compare.")

        st.markdown('<div class="qkx-section-label">Engineer Comments</div>', unsafe_allow_html=True)
        comments = (wt.param_warnings(state["results"]["params_4g"]) + wt.param_warnings(state["results"]["params_5g"])
                    + wt.pci_warnings(state["results"]["pci_4g"] + state["results"]["pci_5g"])
                    + wt.radio_type_warnings(state["results"]["radio_type"])
                    + wt.sector_swap_warnings(state["results"]["radio_type"]))
        if comments:
            for c in comments:
                st.markdown(f"- {c}")
        else:
            st.caption("No warnings — all checked parameters match Pre.")

        st.markdown('<div class="qkx-section-label">PCI / Radio Type / Sector Swap / NR TAC</div>', unsafe_allow_html=True)
        render_result_tables(state["results"], [
            ("Detail tables", ["pci_4g", "pci_5g", "radio_type", "sector_swap", "nr_tac"]),
        ])
        df_download_button(state["results"], "\u2b07\ufe0f Export Excel", "audit.xlsx", "audit_export")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Pre checks (AMOS) — standalone, kget-log-only per-node cell inventory,
# mirrors the HTML tool's Audit -> AMOS tab.
# ═══════════════════════════════════════════════════════════════════════════
with tab_amos:
    amos_log_files = st.file_uploader("Pre kget-all logs (.log / .txt) — one or more",
                                       type=["log", "txt"], accept_multiple_files=True, key="amos_logs")

    if st.button("Parse logs", type="primary", key="amos_run"):
        if not amos_log_files:
            st.error("At least one kget-all log is required.")
            st.stop()
        with st.spinner("Parsing logs..."):
            node_logs = _decode_logs(amos_log_files)
            summary_rows, lte_rows, nr_rows = av.build_amos_tables(node_logs)
        st.session_state["amos"] = {"summary_rows": summary_rows, "lte_rows": lte_rows, "nr_rows": nr_rows}

    state = st.session_state.get("amos")
    if state:
        st.markdown('<div class="qkx-section-label">Node Summary</div>', unsafe_allow_html=True)
        show_rows(state["summary_rows"], drop=())
        st.markdown('<div class="qkx-section-label">LTE Cells</div>', unsafe_allow_html=True)
        show_rows(state["lte_rows"], drop=())
        st.markdown('<div class="qkx-section-label">NR Cells</div>', unsafe_allow_html=True)
        show_rows(state["nr_rows"], drop=())
        st.caption("PTP status and Pre-existing DSS aren't captured by this project's Pre kget-all "
                   "commands (confirmed limitation — see run_validation.py), so both are labelled "
                   "rather than guessed.")
        df_download_button({"Node Summary": state["summary_rows"], "LTE Cells": state["lte_rows"],
                             "NR Cells": state["nr_rows"]},
                            "\u2b07\ufe0f Export Excel", "amos_pre_checks.xlsx", "amos_export")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: CR Desc — CR description string generator from CIQ + kget logs,
# mirrors the HTML tool's CR Desc tab.
# ═══════════════════════════════════════════════════════════════════════════
with tab_crdesc:
    c1, c2, c3 = st.columns(3)
    with c1:
        mic_mca = st.selectbox("MIC DESC", ["MIC - MCA", "MIC - CRAN"], key="cr_mic")
    with c2:
        site_name = st.text_input("Site Name", key="cr_site").strip().upper()
    with c3:
        fa_number = st.text_input("FA Number", key="cr_fa").strip()

    c1, c2 = st.columns(2)
    with c1:
        crdesc_ciq_file = st.file_uploader("CIQ (.xlsx)", type=["xlsx"], key="crdesc_ciq")
    with c2:
        crdesc_log_files = st.file_uploader("Pre kget-all logs (.log / .txt) — one per node",
                                             type=["log", "txt"], accept_multiple_files=True, key="crdesc_logs")

    if st.button("Generate CR Description", type="primary", key="crdesc_run"):
        if not crdesc_ciq_file or not crdesc_log_files:
            st.error("CIQ and at least one kget-all log are required.")
            st.stop()
        if not site_name or not fa_number:
            st.error("Site Name and FA Number are required.")
            st.stop()
        with st.spinner("Building CR description..."):
            with tempfile.TemporaryDirectory() as tmp:
                ciq_path = _save_upload(crdesc_ciq_file, tmp)
                ciq_wb = cer.load_ciq(ciq_path)
            node_logs = _decode_logs(crdesc_log_files)
            mm_rows = cer.mixed_mode_rows(ciq_wb)
            rev_sheet, rev_rows = cer.read_revision_history(ciq_wb)

            pre_pairs, pre_nodes = pci.build_pre_inventory(node_logs)
            sow = sa.classify_carriers(ciq_wb, mm_rows, pre_pairs, pre_nodes)

            regular_nodes = [str(r.get("Node to be built as") or "").strip() for r in mm_rows
                              if r.get("Node to be built as")]
            deleted_nodes = [f"Delete_{n}" for n in sow.get("deleted_nodes", [])]
            all_nodes = regular_nodes + deleted_nodes

            # Bands: every band with an ADDED cell, excluding bands that only
            # appear via a sector MOVE or RETUNE (those aren't new config).
            added_bands = set()
            for cells in sow.get("added", {}).values():
                for cell in cells:
                    band, _ = bl.band_label(cell)
                    if band:
                        added_bands.add(band)
            excluded_bands = set()
            for m in sow.get("moved", []):
                band, _ = bl.band_label(m.get("cell"))
                if band:
                    excluded_bands.add(band)
            for r in sow.get("retuned", []):
                if r.get("label"):
                    excluded_bands.add(r["label"])
            bands = sorted(added_bands - excluded_bands)

            mic_mca_str = mic_mca.replace(" - ", " | ")
            node_str = "/".join(all_nodes) if all_nodes else "-"
            band_str = ("CONFIG_UPDATE_" + "/".join(bands)) if bands else "CONFIG_UPDATE"
            cr_text = f"{mic_mca_str} | {site_name} | FA {fa_number} | {node_str} | {band_str} - RSF"

        st.session_state["crdesc"] = {"cr_text": cr_text, "regular_nodes": regular_nodes,
                                       "deleted_nodes": deleted_nodes, "bands": bands,
                                       "rev_sheet": rev_sheet, "rev_rows": rev_rows}

    state = st.session_state.get("crdesc")
    if state:
        render_revision_history(state["rev_sheet"], state["rev_rows"])
        st.markdown('<div class="qkx-section-label">Generated CR Description</div>', unsafe_allow_html=True)
        st.code(state["cr_text"], language=None)

        c1, c2, c3 = st.columns(3)
        c1.markdown("**Regular Nodes**\n\n" + (", ".join(state["regular_nodes"]) or "—"))
        c2.markdown("**Delete Nodes**\n\n" + (", ".join(state["deleted_nodes"]) or "None"))
        c3.markdown("**Bands**\n\n" + (", ".join(state["bands"]) or "—"))
