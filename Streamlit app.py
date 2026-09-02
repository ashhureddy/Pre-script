"""
Streamlit app.py

UI restructured to match QUICKIX_Pre-Script_Validation.html's real top-level
tabs (confirmed from that tool's own screenshot): RFDS Validation, Audit,
EDP Validator, RET Antenna Checklist, Consolidated Report.

Every check below calls an existing, already-confirmed function from
checks_node.py / checks_sector.py / rfds_extract.py / pre_extract.py /
ciq_edp_reader.py / rrnrbl_checklist.py. Nothing new was invented for this
rewrite - see HANDOFF note: a prior document claimed a set of new modules
(antenna_resolve.py, edp_checks.py, amos_view.py, ciq_view.py) that turned
out not to exist anywhere in the repo. This file does not repeat that -
everything here is wired to code that is actually present and was run
against real project files before being delivered.
"""
import os
import re
import tempfile
import datetime
import io

import streamlit as st

import ciq_edp_reader as cer
import checks_node as cn
import checks_sector as cs
import rfds_extract as rf
import pre_extract as pe
import log_parser as lp
import run_validation as rv
import rrnrbl_checklist as rc
import antenna_resolve as ar
import sow_analysis as sa
import scope_of_work_text as sowt
import pre_cell_inventory as pci

st.set_page_config(page_title="QUICK IX", layout="wide", page_icon="📡")

# ── Styling: dark navy header + coral underline, matching the HTML tool ──
st.markdown("""
<style>
.block-container { padding-top: 1rem; max-width: 1200px; }
.qkx-header {
  background: linear-gradient(90deg, #011b36, #012a4e);
  padding: 14px 22px; border-bottom: 3px solid #ff6b4a;
  border-radius: 6px; margin-bottom: 14px;
}
.qkx-header h1 { color: #fff; font-size: 22px; margin: 0; letter-spacing: .04em; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
.qkx-card {
  border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px 16px;
  background: #f8fafc; margin-bottom: 10px;
}
.qkx-stat { text-align:center; border:1px solid #cbd5e1; border-radius:8px; padding:10px; background:#fff; }
.qkx-badge-match { color:#065f46; background:#d1fae5; padding:2px 8px; border-radius:5px; font-weight:600; }
.qkx-badge-mismatch { color:#991b1b; background:#fee2e2; padding:2px 8px; border-radius:5px; font-weight:600; }
.qkx-badge-manual { color:#92400e; background:#fef3c7; padding:2px 8px; border-radius:5px; font-weight:600; }
.qkx-badge-skip { color:#64748b; background:#f1f5f9; padding:2px 8px; border-radius:5px; font-weight:600; }
</style>
<div class="qkx-header"><h1>📡 QUICK IX</h1></div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# Shared session state — files uploaded in one tab are available in the
# others, same idea as the HTML tool's single shared upload bar.
# ══════════════════════════════════════════════════════════════════════
for k in ("ciq_bytes", "ciq_name", "edp_bytes", "edp_name", "rfds_bytes", "rfds_name"):
    st.session_state.setdefault(k, None)
st.session_state.setdefault("amos_texts", {})   # {node_id: raw_text}
st.session_state.setdefault("amos_names", [])

top_l, top_r = st.columns([1, 5])
with top_l:
    if st.button("🔄 New Validation Run", use_container_width=True):
        st.session_state.clear()
        st.rerun()
with top_r:
    loaded = []
    if st.session_state["ciq_name"]:
        loaded.append(f"CIQ: `{st.session_state['ciq_name']}`")
    if st.session_state["edp_name"]:
        loaded.append(f"EDP: `{st.session_state['edp_name']}`")
    if st.session_state["rfds_name"]:
        loaded.append(f"RFDS: `{st.session_state['rfds_name']}`")
    if st.session_state["amos_names"]:
        loaded.append(f"Pre logs: `{len(st.session_state['amos_names'])}` file(s)")
    st.caption(" &nbsp;·&nbsp; ".join(loaded) if loaded else "No files loaded yet.", unsafe_allow_html=True)


def _tmp_path(data, suffix):
    tf = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tf.write(data)
    tf.close()
    return tf.name


def _load_ciq_wb():
    if not st.session_state["ciq_bytes"]:
        return None
    return cer.load_ciq(_tmp_path(st.session_state["ciq_bytes"], ".xlsx"))


def _status_badge(status):
    cls = {"MATCH": "qkx-badge-match", "PASS": "qkx-badge-match", "EXPECTED": "qkx-badge-match",
           "MISMATCH": "qkx-badge-mismatch", "FAIL": "qkx-badge-mismatch",
           "SKIPPED": "qkx-badge-skip", "INFO": "qkx-badge-skip"}.get(str(status).upper(), "qkx-badge-skip")
    return f'<span class="{cls}">{status}</span>'


def _mm_nodes(ciq_wb):
    """[(node_id, e_name, g_name, mm_row), ...] for every node in Mixed Mode Info."""
    out = []
    for mm in cer.mixed_mode_rows(ciq_wb):
        node_id = str(mm.get("Node to be built as") or mm.get("eNodeB Name") or "").strip()
        if not node_id:
            continue
        out.append((node_id, str(mm.get("eNodeB Name") or "").strip(), str(mm.get("gNodeB Name") or "").strip(), mm))
    return out


tab_rfds, tab_audit, tab_edp, tab_checklist, tab_consolidated = st.tabs(
    ["RFDS Validation", "Audit", "EDP Validator", "RET Antenna Checklist", "Consolidated Report"]
)

# ══════════════════════════════════════════════════════════════════════
# TAB 1 — RFDS Validation (standalone: RFDS required, CIQ optional)
# ══════════════════════════════════════════════════════════════════════
with tab_rfds:
    st.subheader("RFDS Validation")
    c1, c2 = st.columns(2)
    with c1:
        up = st.file_uploader("RFDS PDF", type=["pdf"], key="up_rfds")
        if up:
            st.session_state["rfds_bytes"], st.session_state["rfds_name"] = up.read(), up.name
    with c2:
        up = st.file_uploader("CIQ workbook (optional — enables cell/RCN comparison)", type=["xlsx"], key="up_ciq_rfds")
        if up:
            st.session_state["ciq_bytes"], st.session_state["ciq_name"] = up.read(), up.name

    if not st.session_state["rfds_bytes"]:
        st.info("Upload an RFDS PDF to begin.")
    else:
        pages = rf.load_rfds_pages(st.session_state["rfds_bytes"])
        site_details = rf.extract_site_details(pages)

        st.markdown("#### Site Details")
        m1, m2, m3, m4 = st.columns(4)
        m1.markdown(f'<div class="qkx-stat"><b>FA Code</b><br>{site_details.get("fa_code") or "—"}</div>', unsafe_allow_html=True)
        m2.markdown(f'<div class="qkx-stat"><b>USID</b><br>{site_details.get("usid") or "—"}</div>', unsafe_allow_html=True)
        m3.markdown(f'<div class="qkx-stat"><b>Site ID</b><br>{site_details.get("site_id") or "—"}</div>', unsafe_allow_html=True)
        m4.markdown(f'<div class="qkx-stat"><b>Atoll Name</b><br>{site_details.get("atoll_site_name") or "—"}</div>', unsafe_allow_html=True)

        if not st.session_state["ciq_bytes"]:
            st.caption("Upload a CIQ above to compare cells / CellID / RRH against this RFDS.")
        else:
            ciq_wb = _load_ciq_wb()
            all_rows = []
            for node_id, e_name, g_name, mm in _mm_nodes(ciq_wb):
                all_rows.extend(cs.check_cells_vs_rfds(node_id, ciq_wb, pages, e_name, g_name))
            st.markdown("#### Cells vs RFDS (CellID / RCN / RRH presence — Rule #6/#18)")
            if all_rows:
                st.dataframe(all_rows, use_container_width=True, hide_index=True)
                n_bad = sum(1 for r in all_rows if r.get("status") == "MISMATCH")
                st.caption(f"{len(all_rows)} cell(s) checked — {n_bad} not found in RFDS.")
            else:
                st.caption("No comparable cells found (check node naming between CIQ and RFDS).")

# ══════════════════════════════════════════════════════════════════════
# TAB 2 — Audit (Pre checks / CIQ Checks / Audit — shared state, like the
# HTML tool's Telecom Audit Pro module)
# ══════════════════════════════════════════════════════════════════════
with tab_audit:
    st.subheader("Audit")
    sub_pre, sub_ciq, sub_audit, sub_crdesc = st.tabs(["Pre checks (AMOS)", "CIQ Checks", "Audit (Pre vs CIQ)", "CR Desc"])

    with sub_pre:
        ups = st.file_uploader("Pre kget-all logs (.txt/.log, one per node)", type=["txt", "log"],
                                accept_multiple_files=True, key="up_amos")
        if ups:
            for u in ups:
                text = u.read().decode("utf-8", errors="ignore")
                nid = pe.node_id_from_log(text) or u.name
                st.session_state["amos_texts"][nid] = text
                if nid not in st.session_state["amos_names"]:
                    st.session_state["amos_names"].append(nid)

        if not st.session_state["amos_texts"]:
            st.info("Upload one or more Pre kget-all logs to see extracted node/cell data.")
        else:
            rows = []
            for nid, text in st.session_state["amos_texts"].items():
                parsed = lp.parse_log(text)
                sw = pe.extract_sw_version(text) or {}
                ident = pe.extract_identity(parsed) or {}
                hw = pe.extract_hardware(parsed) or {}
                ptp = pe.extract_ptp_status(text)
                dss_map = pe.extract_dss_status(text)
                dss_cells = [c for c, active in dss_map.items() if active]
                rows.append({
                    "node": nid,
                    "sw_package": sw.get("sw_package"), "sw_version": sw.get("sw_version"),
                    "eNBId": ident.get("eNBId"), "gNBId": ident.get("gNBId"),
                    "boards": ", ".join(hw.get("boards") or []),
                    "radios": ", ".join(hw.get("radios") or []),
                    "PTP (unconfirmed)": ptp,
                    "Pre-existing DSS (unconfirmed)": ", ".join(dss_cells) if dss_cells else "None found",
                })
            st.caption("⚠️ PTP and DSS columns use a regex ported from the sibling HTML tool, not yet confirmed "
                       "against a real kget-all log from this project's own hget command set — spot-check before trusting.")
            st.markdown("#### Node Summary")
            st.dataframe(rows, use_container_width=True, hide_index=True)

            for nid, text in st.session_state["amos_texts"].items():
                with st.expander(f"{nid} — LTE / NR cells"):
                    lte = pe.extract_lte_sector_params(text) or {}
                    parsed = lp.parse_log(text)
                    nr = pe.extract_5g_sector_params(parsed, text) or {}
                    if lte:
                        st.markdown("**LTE cells**")
                        st.dataframe(list(lte.values()) if isinstance(lte, dict) else lte, use_container_width=True, hide_index=True)
                    if nr:
                        st.markdown("**NR cells**")
                        st.dataframe(list(nr.values()) if isinstance(nr, dict) else nr, use_container_width=True, hide_index=True)
                    if not lte and not nr:
                        st.caption("No LTE or NR sector params extracted from this log.")

    with sub_ciq:
        up = st.file_uploader("CIQ workbook", type=["xlsx"], key="up_ciq_audit")
        if up:
            st.session_state["ciq_bytes"], st.session_state["ciq_name"] = up.read(), up.name

        if not st.session_state["ciq_bytes"]:
            st.info("Upload a CIQ workbook to see Node Integration and parameter tables.")
        else:
            ciq_wb = _load_ciq_wb()
            nodes = _mm_nodes(ciq_wb)

            st.markdown("#### Node Integration")
            integ_rows = []
            for node_id, e_name, g_name, mm in nodes:
                enb_rows = cer.enb_info_rows(ciq_wb)
                enb_row = cer.find_enb_row(enb_rows, node_id)
                integ_rows.append({
                    "node": node_id, "eNodeB Name": e_name, "gNodeB Name": g_name,
                    "DU type": (enb_row or {}).get("DU type", ""),
                    "BBU Mode": mm.get("BBU Mode", ""),
                })
            st.dataframe(integ_rows, use_container_width=True, hide_index=True)

            st.markdown("#### Sanity checks (CIQ-only — no Pre/EDP/RFDS needed)")
            check_rows = []
            for node_id, e_name, g_name, mm in nodes:
                check_rows += cs.check_pci_uniqueness(node_id, ciq_wb, e_name)
                check_rows += cs.check_nr_pci_uniqueness(node_id, ciq_wb, g_name)
                check_rows += cs.check_antenna_uniqueness(node_id, ciq_wb)
                check_rows += cs.check_port_uniqueness(node_id, ciq_wb)
                check_rows += cs.check_sef_fru(node_id, ciq_wb)
                check_rows += cs.check_radio_sharing_pairs(node_id, ciq_wb)
                check_rows += cs.check_radio_port_conflict(node_id, ciq_wb)
            if check_rows:
                st.dataframe(check_rows, use_container_width=True, hide_index=True)
                n_bad = sum(1 for r in check_rows if r.get("status") == "MISMATCH")
                st.caption(f"{len(check_rows)} check(s) — {n_bad} mismatch(es) (PCI clash, antenna/port uniqueness, SEF/FRU).")
            else:
                st.caption("No checkable rows found.")

            with st.expander("Raw eUtran Parameters / 5G Info"):
                if "eUtran Parameters" in ciq_wb.sheetnames:
                    st.markdown("**eUtran Parameters (LTE)**")
                    st.dataframe(cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]), use_container_width=True, hide_index=True)
                if "5G Info" in ciq_wb.sheetnames:
                    st.markdown("**5G Info**")
                    st.dataframe(cer.sheet_rows_as_dicts(ciq_wb["5G Info"]), use_container_width=True, hide_index=True)

            st.markdown("#### Antenna model vs RFDS")
            if not st.session_state["rfds_bytes"]:
                st.caption("Upload an RFDS PDF (RFDS Validation tab) to compare antenna models.")
            else:
                rfds_pages_ant = rf.load_rfds_pages(st.session_state["rfds_bytes"])
                port_details = rf.extract_port_level_details(rfds_pages_ant)
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
                    st.caption("⚠️ New extractor/matcher, not yet as battle-tested as the numbered rules — spot-check results.")
                    st.dataframe(ant_rows, use_container_width=True, hide_index=True)

            st.markdown("#### FA Code follow-up email")
            fa = cn.build_site_details(ciq_wb, None).get("fa_code") or "UNKNOWN"
            n_mismatch = sum(1 for r in check_rows if r.get("status") == "MISMATCH")
            subject = f"FA {fa} — CIQ checks: {n_mismatch} mismatch(es) found"
            body = (f"FA Code: {fa}%0D%0ANodes: {', '.join(node_ids)}%0D%0A"
                    f"CIQ sanity checks found {n_mismatch} mismatch(es) out of {len(check_rows)} — see attached export.")
            st.link_button("✉️ Compose FA-code email", f"mailto:?subject={subject}&body={body}")

    with sub_audit:
        if not st.session_state["ciq_bytes"]:
            st.warning("Load a CIQ in the **CIQ Checks** tab first.")
        elif not st.session_state["amos_texts"]:
            st.warning("Load at least one Pre kget-all log in the **Pre checks (AMOS)** tab first.")
        else:
            ciq_wb = _load_ciq_wb()
            rfds_pages = rf.load_rfds_pages(st.session_state["rfds_bytes"]) if st.session_state["rfds_bytes"] else None
            rows = []
            for node_id, e_name, g_name, mm in _mm_nodes(ciq_wb):
                text = st.session_state["amos_texts"].get(node_id)
                if text is None:
                    # fall back to the only uploaded log if names don't line up 1:1
                    text = next(iter(st.session_state["amos_texts"].values()), "")
                has_pre = bool(text)
                rows += cs.check_rf_params_4g(node_id, text, ciq_wb, has_pre)
                parsed_nr = lp.parse_log(text) if text else {}
                rows += cs.check_rf_params_5g(node_id, parsed_nr, text, ciq_wb, has_pre)
                rows += cs.check_sector_swap_config(node_id, text, ciq_wb, e_name, g_name)
                if rfds_pages is not None:
                    rows += cs.check_cell_id_vs_rfds(node_id, text, ciq_wb, rfds_pages, e_name, g_name)
            st.markdown("#### Pre vs CIQ (+ RFDS if loaded)")
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
                n_bad = sum(1 for r in rows if r.get("status") == "MISMATCH")
                st.caption(f"{len(rows)} field(s) checked — {n_bad} mismatch(es).")
            else:
                st.caption("Nothing to compare yet.")

    with sub_crdesc:
        if not st.session_state["ciq_bytes"]:
            st.warning("Load a CIQ in the **CIQ Checks** tab first.")
        else:
            ciq_wb = _load_ciq_wb()
            mm_rows = cer.mixed_mode_rows(ciq_wb)
            pre_pairs, pre_nodes = pci.build_pre_inventory(st.session_state["amos_texts"])
            sow = sa.classify_carriers(ciq_wb, mm_rows, pre_pairs, pre_nodes)
            sow["target_band_sectors"] = sowt.build_target_band_sectors(ciq_wb, mm_rows)
            scope_lines = sowt.scope_lines_to_readable_text(sowt.format_scope_of_work(sow, ciq_wb))
            st.markdown("#### CR Description")
            if not st.session_state["amos_texts"]:
                st.caption("No Pre logs loaded — 'moved sector' lines need Pre data to detect the source node; "
                           "this is integration-only scope for now.")
            cr_text = "\n".join(f"- {l}" for l in scope_lines) if scope_lines else "(nothing to describe yet)"
            st.text_area("Generated CR description", value=cr_text, height=200)
            st.download_button("⬇️ Download CR description (.txt)", data=cr_text,
                                file_name="cr_description.txt", mime="text/plain")

# ══════════════════════════════════════════════════════════════════════
# TAB 3 — EDP Validator (standalone: EDP required, CIQ optional)
# ══════════════════════════════════════════════════════════════════════
with tab_edp:
    st.subheader("EDP Validator")
    c1, c2 = st.columns(2)
    with c1:
        up = st.file_uploader("EDP workbook (.xls/.xlsx)", type=["xls", "xlsx"], key="up_edp")
        if up:
            st.session_state["edp_bytes"], st.session_state["edp_name"] = up.read(), up.name
    with c2:
        up = st.file_uploader("CIQ workbook (optional — enables the full per-node checklist)", type=["xlsx"], key="up_ciq_edp")
        if up:
            st.session_state["ciq_bytes"], st.session_state["ciq_name"] = up.read(), up.name

    if not st.session_state["edp_bytes"]:
        st.info("Upload an EDP workbook to begin.")
    else:
        edp_ws = cer.load_edp(_tmp_path(st.session_state["edp_bytes"], os.path.splitext(st.session_state["edp_name"])[1]))
        _, edp_rows = cer.build_edp_index(edp_ws)

        if not st.session_state["ciq_bytes"]:
            st.caption("Upload a CIQ above to check EDP fields against the nodes actually requested.")
            st.markdown("#### EDP rows found")
            st.dataframe(edp_rows, use_container_width=True, hide_index=True)
        else:
            ciq_wb = _load_ciq_wb()
            nodes = _mm_nodes(ciq_wb)
            node_ids = [n[0] for n in nodes]

            mm_by_node = {nid: mm for nid, e, g, mm in nodes}
            controller_ids = [r.get("Controller ID") for r in cer.sheet_rows_as_dicts(ciq_wb["Controller Info"])
                               if r.get("Controller ID")] if "Controller Info" in ciq_wb.sheetnames else []
            checks = {
                "Found in EDP": rc._edp_found_status(edp_rows, node_ids),
                "Cabinet naming": rc._edp_cabinet_status(edp_rows, node_ids),
                "Port size (BBU mode)": rc._edp_port_size_status(edp_rows, node_ids, mm_by_node),
                "Port facing (Primary/Secondary)": rc._edp_port_facing_status(edp_rows, node_ids),
                "Bearer VLAN clash": rc._edp_bearer_vlan_status(edp_rows, node_ids),
                "IPv6 bearer addressing": rc._edp_group_status(edp_rows, node_ids, rc.IPV6_BEARER_FIELDS, "IPv6 bearer"),
                "IPv6 OAM addressing": rc._edp_group_status(edp_rows, node_ids, rc.IPV6_OAM_FIELDS, "IPv6 OAM"),
                "Controller (ANCEQ)": rc._edp_controller_status(edp_rows, controller_ids),
                "PTP configuration": rc._edp_ptp_status(edp_rows, node_ids),
            }
            n_pass = sum(1 for s, _ in checks.values() if s == "match")
            n_fail = sum(1 for s, _ in checks.values() if s == "mismatch")
            n_unk = sum(1 for s, _ in checks.values() if s == "unknown")

            s1, s2, s3, s4 = st.columns(4)
            s1.markdown(f'<div class="qkx-stat"><b>{len(node_ids)}</b><br>Expected Nodes</div>', unsafe_allow_html=True)
            s2.markdown(f'<div class="qkx-stat"><b>{n_pass}</b><br>Pass</div>', unsafe_allow_html=True)
            s3.markdown(f'<div class="qkx-stat"><b>{n_fail}</b><br>Fail</div>', unsafe_allow_html=True)
            s4.markdown(f'<div class="qkx-stat"><b>{n_unk}</b><br>No data</div>', unsafe_allow_html=True)

            view = st.radio("View", ["Table view", "Checklist view"], horizontal=True, key="edp_view_toggle")
            if view == "Table view":
                st.dataframe(
                    [{"check": k, "status": s, "detail": d} for k, (s, d) in checks.items()],
                    use_container_width=True, hide_index=True,
                )
            else:
                for k, (s, d) in checks.items():
                    st.markdown(f"{_status_badge(s.upper())} &nbsp; **{k}** — {d}", unsafe_allow_html=True)

            board = [cn.check_board_type(nid, mm, cer.find_enb_row(cer.enb_info_rows(ciq_wb), nid), ciq_wb, edp_rows)
                     for nid, e, g, mm in nodes]
            st.markdown("#### Board Type (CIQ vs EDP vs RFDS)")
            st.dataframe(board, use_container_width=True, hide_index=True)

            xmu_rows = []
            gnb_rows_all = cer.gnb_info_5g_info_rows(ciq_wb) if "gNB Info" in ciq_wb.sheetnames else []
            for nid, e_name, g_name, mm in nodes:
                enb_row = cer.find_enb_row(cer.enb_info_rows(ciq_wb), nid)
                gnb_row = next((r for r in gnb_rows_all if str(r.get("gNodeB Name", "")).strip().upper() == g_name.upper()), None) if g_name else None
                xmu_rows += cs.check_xmu_port_overlap(nid, enb_row, gnb_row, ciq_wb)
            st.markdown("#### XMU Port Overlap (Riport uniqueness)")
            if xmu_rows:
                st.dataframe(xmu_rows, use_container_width=True, hide_index=True)
            else:
                st.caption("No node declares an XMU (1st/2nd XMU = YES) — nothing to check.")

# ══════════════════════════════════════════════════════════════════════
# TAB 4 — RET Antenna Checklist (the 63-item RRNRBL checklist)
# ══════════════════════════════════════════════════════════════════════
with tab_checklist:
    st.subheader("RET Antenna Checklist")
    if not (st.session_state["ciq_bytes"] and st.session_state["edp_bytes"]):
        st.warning("Needs a CIQ **and** an EDP — load them in the RFDS Validation, Audit, or EDP Validator tabs, "
                   "or run the full pipeline in **Consolidated Report**.")
    else:
        if st.button("Build checklist", key="build_checklist_btn"):
            ciq_path = _tmp_path(st.session_state["ciq_bytes"], ".xlsx")
            edp_path = _tmp_path(st.session_state["edp_bytes"], os.path.splitext(st.session_state["edp_name"])[1])
            rfds_path = _tmp_path(st.session_state["rfds_bytes"], ".pdf") if st.session_state["rfds_bytes"] else None
            node_logs = {}
            for nid, text in st.session_state["amos_texts"].items():
                node_logs[nid] = _tmp_path(text.encode("utf-8"), ".txt")
            with tempfile.TemporaryDirectory() as tmp:
                out_pdf = os.path.join(tmp, "r.pdf")
                _, results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages = rv.run(
                    ciq_path, edp_path, rfds_path, node_logs, out_pdf
                )
            st.session_state["checklist"] = rc.build_checklist(results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages)
            st.session_state["checklist_site_id_fa"] = " / ".join(
                v for v in (site_details.get("site_id"), site_details.get("fa_code")) if v)

        checklist = st.session_state.get("checklist")
        if checklist:
            # Manual items: a real checkbox + comment box per row, keyed by the
            # fixed Excel row number (not list position) so Streamlit keeps the
            # value across reruns instead of resetting it — every checkbox and
            # text_area with a `key=` is auto-persisted in st.session_state by
            # Streamlit itself; we just read those same keys back below.
            manual_overrides = {}
            for row in checklist:
                if row["status"] != "manual":
                    continue
                r = row["row"]
                manual_overrides[r] = {
                    "done": st.session_state.get(f"manual_done_{r}", False),
                    "comment": st.session_state.get(f"manual_comment_{r}", ""),
                }

            xbytes = rc.fill_checklist_xlsx(checklist, st.session_state.get("checklist_site_id_fa", ""),
                                             manual_overrides=manual_overrides)
            st.download_button("⬇️ Download filled RRNRBL Checklist (.xlsx)", data=xbytes,
                                file_name="Checklist_RRNRBL_filled.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="checklist_download_btn")
            st.caption("Every manual checkbox/comment below is baked into this download live — check a box or "
                       "type a comment, then click Download again to get the updated file.")

            counts = {}
            for row in checklist:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            st.write(" &nbsp; ".join(f"**{v}** {k}" for k, v in counts.items()))

            cats = []
            for row in checklist:
                if not cats or cats[-1]["cat"] != row["cat"]:
                    cats.append({"cat": row["cat"], "rows": []})
                cats[-1]["rows"].append(row)
            icon = {"match": "✅", "mismatch": "❌", "manual": "✏️", "unknown": "❔", "na": "➖", "info": "ℹ️"}
            for c in cats:
                with st.expander(f"{c['cat']} ({sum(1 for r in c['rows'] if r['status']=='mismatch')} mismatch)"):
                    last_sub = object()
                    for row in c["rows"]:
                        if row["sub"] != last_sub:
                            if row["sub"]:
                                st.markdown(f"**{row['sub']}**")
                            last_sub = row["sub"]
                        if row["status"] == "manual":
                            r = row["row"]
                            cols = st.columns([0.06, 0.94])
                            with cols[0]:
                                st.checkbox("", key=f"manual_done_{r}", label_visibility="collapsed")
                            with cols[1]:
                                st.markdown(f"✏️ **{row['item']}**")
                                st.text_input("Comment", key=f"manual_comment_{r}", label_visibility="collapsed",
                                              placeholder="Comment / evidence…")
                        else:
                            st.markdown(f"{icon.get(row['status'],'?')} **{row['item']}** — {row['detail']}")

# ══════════════════════════════════════════════════════════════════════
# TAB 5 — Consolidated Report (the original full pipeline)
# ══════════════════════════════════════════════════════════════════════
with tab_consolidated:
    st.subheader("Consolidated Report")
    st.caption("Runs the full validation pass (CIQ + EDP required, RFDS + Pre logs optional) and produces the PDF "
               "report plus a filled RRNRBL checklist.")

    c1, c2 = st.columns(2)
    with c1:
        up = st.file_uploader("CIQ workbook", type=["xlsx"], key="up_ciq_cr")
        if up:
            st.session_state["ciq_bytes"], st.session_state["ciq_name"] = up.read(), up.name
        up = st.file_uploader("EDP workbook", type=["xls", "xlsx"], key="up_edp_cr")
        if up:
            st.session_state["edp_bytes"], st.session_state["edp_name"] = up.read(), up.name
    with c2:
        up = st.file_uploader("RFDS PDF (optional)", type=["pdf"], key="up_rfds_cr")
        if up:
            st.session_state["rfds_bytes"], st.session_state["rfds_name"] = up.read(), up.name
        ups = st.file_uploader("Pre kget-all logs (optional)", type=["txt", "log"], accept_multiple_files=True, key="up_amos_cr")
        if ups:
            for u in ups:
                text = u.read().decode("utf-8", errors="ignore")
                nid = pe.node_id_from_log(text) or u.name
                st.session_state["amos_texts"][nid] = text
                if nid not in st.session_state["amos_names"]:
                    st.session_state["amos_names"].append(nid)

    ready = bool(st.session_state["ciq_bytes"] and st.session_state["edp_bytes"])
    if st.button("▶ Generate Report", disabled=not ready, type="primary"):
        with st.spinner("Running validation…"):
            ciq_path = _tmp_path(st.session_state["ciq_bytes"], ".xlsx")
            edp_path = _tmp_path(st.session_state["edp_bytes"], os.path.splitext(st.session_state["edp_name"])[1])
            rfds_path = _tmp_path(st.session_state["rfds_bytes"], ".pdf") if st.session_state["rfds_bytes"] else None
            node_logs = {nid: _tmp_path(t.encode("utf-8"), ".txt") for nid, t in st.session_state["amos_texts"].items()}
            with tempfile.TemporaryDirectory() as tmp:
                out_pdf = os.path.join(tmp, "validation_report.pdf")
                try:
                    _, results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages = rv.run(
                        ciq_path, edp_path, rfds_path, node_logs, out_pdf
                    )
                except Exception as e:
                    st.error(f"Report generation failed: {e}")
                    st.stop()
                with open(out_pdf, "rb") as f:
                    pdf_bytes = f.read()
            checklist = rc.build_checklist(results, site_details, ciq_wb, edp_rows, checked_nodes, rfds_pages)
            site_id_fa = " / ".join(v for v in (site_details.get("site_id"), site_details.get("fa_code")) if v)
            checklist_xlsx = rc.fill_checklist_xlsx(checklist, site_id_fa)
            st.session_state["cr_pdf_bytes"] = pdf_bytes
            st.session_state["cr_checklist_xlsx"] = checklist_xlsx
        st.success("Report generated.")
    elif not ready:
        st.caption("CIQ and EDP are both required to generate the Consolidated Report.")

    if st.session_state.get("cr_pdf_bytes"):
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("⬇️ Download PDF", data=st.session_state["cr_pdf_bytes"],
                                file_name="validation_report.pdf", mime="application/pdf", use_container_width=True)
        with d2:
            st.download_button("⬇️ Download filled RRNRBL Checklist (.xlsx)", data=st.session_state["cr_checklist_xlsx"],
                                file_name="Checklist_RRNRBL_filled.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True)
