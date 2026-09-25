"""
CIQ Checks — per-cell Comments/Warning + Corrective Action, and cross-sector
Sharing Radio, for both the LTE eUtran Parameters and 5G NR Parameters cards.

Ported from QUICKIX_Pre-Script_Validation.html's ciqValidateLTE() /
ciqBuildNRTable() comment-generation logic and ciqCorrectiveAction(), with
one deliberate substitution: the AUG/AU/ASU antenna-uniqueness half of the
LTE comment set reuses this project's own checks_sector.
check_antenna_uniqueness() rather than re-deriving the HTML's own (simpler)
version — this project's version already has a more precise 4890/8843
trigger-model naming and MATCH/MISMATCH status, and keeping ONE antenna-
uniqueness implementation avoids two logic paths silently disagreeing.

Sharing Radio (LTE and 5G) is NEW — the HTML has no such column in CIQ
Checks (only a "Link (Single/Doublelink)" column, which is a different
concept: same node+RRU+RadioPort dual-carrier, not cross-sector sharing).
Built to the same rule already used in amos_view.build_lte_cell_rows() for
Pre checks: same RRU + same band serving DIFFERENT sector letters.
"""
import re

import band_labels as bl
import ciq_edp_reader as cer
import checks_sector as cs
import pre_extract as pe


def _band_family(label):
    """AWS_2 -> AWS, 5G_PCS_1 -> PCS — same family reduction the antenna-
    uniqueness check already uses, reused here for readability grouping only
    (not a new rule)."""
    if not label:
        return None
    stripped = re.sub(r'_\d+$', '', label)
    return re.sub(r'^5G_', '', stripped)


def _node_name_maps(ciq_wb):
    """eNBId -> node name, gNBId -> node name, from Mixed Mode Info's own
    'Node to be built as' column — same source ciq_view.build_node_integration()
    already reads, kept separate here since this module doesn't otherwise
    depend on ciq_view.py."""
    node_by_enb, node_by_gnb = {}, {}
    for m in cer.mixed_mode_rows(ciq_wb):
        node = m.get("Node to be built as")
        if not node:
            continue
        enb = str(m.get("eNBId") or "").strip()
        gnb = str(m.get("gNBId") or "").strip()
        if enb:
            node_by_enb[enb] = node
        if gnb:
            node_by_gnb[gnb] = node
    return node_by_enb, node_by_gnb


def _clean_ports(*vals):
    out = []
    for v in vals:
        s = str(v or "").strip().upper()
        if s and s not in ("N/A", "NOT USED"):
            out.append(s)
    return ",".join(out)


def _clean_link_name(radio_port_raw):
    """CIQ's own scripted link name (DATA1/DATA2/...) for display, straight
    off the sheet's 'Radio Port' column - e.g. 'DATA1' or 'DATA1/DATA2' for
    a dual-carrier row. Purely a display value; does NOT feed the existing
    'link' (Single/Doublelink) field or apply_link_and_sharing() at all -
    that field's own node+RRU aggregation logic is unchanged."""
    s = str(radio_port_raw or "").strip().upper()
    if not s or s in ("N/A", "NOT USED"):
        return "-"
    parts = [p.strip() for p in re.split(r'[/,]', s) if p.strip()]
    return "/".join(parts) if parts else "-"


# Confirmed real format across every CIQ checked: a RIPORT token is always
# either a single letter (A-Z) or a plain, digits-only number (single or
# multi-digit — "5" through "15" all seen), comma-separated when a
# position carries more than one. Anything else (multi-letter text,
# letter+digit combos, symbols) is a garbled/mistyped port value in the
# CIQ, not a valid one — flagged rather than silently displayed or fed
# into the Sharing Radio / Link comparison as if it were real.
_RIPORT_TOKEN_RE = re.compile(r'^[A-Z]$|^\d+$')


def _riport_format_warning(riport):
    if not riport or riport == "-":
        return None
    bad = [t.strip() for t in riport.split(",") if t.strip() and not _RIPORT_TOKEN_RE.match(t.strip())]
    if bad:
        return f'RIPORT value(s) not a single letter or number: {", ".join(bad)}'
    return None


# ── Corrective actions, verbatim from QUICKIX's ciqCorrectiveAction() ──
def _corrective_action(warning):
    if warning.startswith("PCI Clash"):
        return "Raise a pre integration issue mail"
    if warning.startswith("Incorrect PCI calculation"):
        return ("Raise PI mail to update the PCI value as per "
                "PCI = PhysicalLayerCellIdGroup x 3 + physicalLayerSubCellId")
    if warning.startswith("Electrical tilt is not integer"):
        return "Change the electrical tilt value to integer"
    if warning.startswith("Port clash") or warning.startswith("Shared Radio Port"):
        return "Raise a pre integration issue mail"
    if warning.startswith("[AUG/AU/ASU]") or "not unique" in warning.lower() or "not shared" in warning.lower():
        return "Change the [AUG/AU/ASU] as per standard."
    if warning.startswith("Sharing Radio") or warning.startswith("Radio Sharing"):
        return "Verify the RRU/sector assignment against the design."
    return "Verify and correct in the CIQ"


def build_lte_ciq_rows(ciq_wb, node_id_col_map=None, rbb_results=None):
    """One row per eUtran Parameters entry: Node, Cell, PCI, Cell ID,
    Electrical Tilt, RBB Type Verification, RIPORT, Comments — matches
    QUICKIX HTML's LTE E-UTRAN Parameters card (Link column is built by
    the caller via build_link_map(), since Link needs the SAME node+RRU
    map both LTE and 5G share).

    rbb_results: checks_sector.check_rbb_tx_isdlonly_4g's own output
    (already the canonical RBB-vs-TX/RX/ISDLONLY/Radio-Port check, wired
    into Consolidated Report's CIQ Sanity Check and Checklist row 71) —
    passed in and matched by cell rather than re-implemented here, so
    this table and that check can never silently drift apart."""
    rows = cer.sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]) if "eUtran Parameters" in ciq_wb.sheetnames else []
    if not rows:
        return []
    node_by_enb, _ = _node_name_maps(ciq_wb)

    comments = {i: [] for i in range(len(rows))}

    def add(i, text):
        comments[i].append(text)

    # ── eNodeB prefix mismatch (Mixed Mode / eNB Info vs cell name prefix) ──
    enb_rows = {str(r.get("eNBId") or "").strip(): r for r in cer.enb_info_rows(ciq_wb)}
    mixed_by_enb = {}
    for m in cer.mixed_mode_rows(ciq_wb):
        enb = str(m.get("eNBId") or "").strip()
        if enb:
            mixed_by_enb[enb] = str(m.get("eNodeB Name") or "").strip()[:8].upper()
    enb_info_prefix = {enb: str(r.get("eNodeB Name") or "").strip()[:8].upper() for enb, r in enb_rows.items()}
    for i, r in enumerate(rows):
        enb = str(r.get("eNBId") or "").strip()
        cell = str(r.get("EutranCellFDDId") or "").strip()
        pfx8 = cell[:8].upper()
        if enb and mixed_by_enb.get(enb) and mixed_by_enb[enb] != pfx8:
            add(i, f'eNodeB Prefix Mismatch (Cell prefix "{pfx8}" vs Mixed Mode "{mixed_by_enb[enb]}")')
        if enb and enb_info_prefix.get(enb) and enb_info_prefix[enb] != pfx8:
            add(i, f'eNodeB Prefix Mismatch (Cell prefix "{pfx8}" vs eNB Info "{enb_info_prefix[enb]}")')

    def _group_and_flag(key_fn, label_fn, msg_fn):
        groups = {}
        for i, r in enumerate(rows):
            groups.setdefault(key_fn(r), []).append(i)
        for idxs in groups.values():
            if len(idxs) > 1:
                ref = rows[idxs[0]]
                node_name = node_by_enb.get(str(ref.get("eNBId") or "").strip(), str(ref.get("eNBId") or ""))
                cells = ", ".join(str(rows[i].get("EutranCellFDDId") or "") for i in idxs)
                msg = msg_fn(ref, node_name, cells)
                for i in idxs:
                    add(i, msg)

    # ── PCI Clash: same eNBId + band + PCI ──
    _group_and_flag(
        lambda r: (r.get("eNBId"), r.get("eUTRA operating band"), r.get("PCI")),
        None,
        lambda ref, node_name, cells: f'PCI Clash (PCI={ref.get("PCI")} on Node {node_name} : [{cells}])',
    )
    # ── CellID Clash: same eNBId + cellId ──
    _group_and_flag(
        lambda r: (r.get("eNBId"), r.get("cellId")),
        None,
        lambda ref, node_name, cells: f'CellID Clash (CellID={ref.get("cellId")} on Node {node_name}: {cells})',
    )
    # ── Sector Clash: same eNBId + sectorId ──
    _group_and_flag(
        lambda r: (r.get("eNBId"), r.get("sectorId")),
        None,
        lambda ref, node_name, cells: f'Sector Clash (SectorID={ref.get("sectorId")} on Node {node_name}: {cells})',
    )

    # ── EARFCN Mismatch: same eNBId + band, different earfcnDl/Ul ──
    earfcn_groups = {}
    for i, r in enumerate(rows):
        key = (r.get("eNBId"), r.get("eUTRA operating band"))
        earfcn_groups.setdefault(key, []).append(i)
    for idxs in earfcn_groups.values():
        if len(idxs) < 2:
            continue
        ref_dl = str(rows[idxs[0]].get("earfcnDl") if rows[idxs[0]].get("earfcnDl") is not None else "").strip()
        ref_ul = str(rows[idxs[0]].get("earfcnUl") if rows[idxs[0]].get("earfcnUl") is not None else "").strip()
        for i in idxs:
            dl = str(rows[i].get("earfcnDl") if rows[i].get("earfcnDl") is not None else "").strip()
            ul = str(rows[i].get("earfcnUl") if rows[i].get("earfcnUl") is not None else "").strip()
            if dl != ref_dl or ul != ref_ul:
                if not any(c.startswith("EARFCN Mismatch") for c in comments[i]):
                    add(i, "EARFCN Mismatch")

    # ── Carrier Reused Across Bands: same eNBId + Carrier, multiple bands ──
    carrier_bands, carrier_idxs = {}, {}
    for i, r in enumerate(rows):
        carrier = str(r.get("Carrier") or "").strip()
        if not carrier:
            continue
        key = (r.get("eNBId"), carrier)
        carrier_bands.setdefault(key, set()).add(r.get("eUTRA operating band"))
        carrier_idxs.setdefault(key, []).append(i)
    for key, bands in carrier_bands.items():
        if len(bands) > 1:
            # Same underlying band split only by bandwidth/sub-block text
            # (e.g. WCS Band 30 at 5 MHz vs 10 MHz) is not a real reuse
            # across bands - see band_labels.same_underlying_band().
            if bl.same_underlying_band(bands):
                continue
            idxs = carrier_idxs[key]
            ref = rows[idxs[0]]
            node_name = node_by_enb.get(str(ref.get("eNBId") or "").strip(), str(ref.get("eNBId") or ""))
            cells = ", ".join(str(rows[i].get("EutranCellFDDId") or "") for i in idxs)
            add(idxs[0], f'Carrier Reused Across Bands (Carrier={key[1]} on Node {node_name}: Bands {", ".join(str(b) for b in bands)})')

    # ── Shared Radio Port without Co-Located declaration ──
    port_groups = {}
    for i, r in enumerate(rows):
        port = str(r.get("DUS / XMU Port") or "").strip()
        if not port or port.upper() == "N/A":
            continue
        key = (r.get("eNBId"), port)
        port_groups.setdefault(key, []).append(i)
    for idxs in port_groups.values():
        if len(idxs) < 2:
            continue
        cells = ", ".join(str(rows[i].get("EutranCellFDDId") or "") for i in idxs)
        for i in idxs:
            colo = str(rows[i].get("Co-Located Technology Cell") or "").strip().upper()
            if colo in ("", "NA", "N/A", "NOT USED"):
                port = str(rows[i].get("DUS / XMU Port") or "").strip()
                add(i, f"Shared Radio Port (Port={port} used by: {cells}) \u2014 Add to Co-Located field")

    # ── Cross-tech port reuse (this 4G cell vs a 5G cell on the SAME
    # physical board) and XMU-reserved port reuse — the eNBId-based check
    # above only ever compares 4G cells against other 4G cells, so a 5G
    # cell on the same DU/BBU board sharing this cell's port (confirmed
    # real gap: a TMBB node's 4G cell and 5G cell legitimately sharing one
    # physical antenna port, or illegitimately clashing on one) was
    # invisible here. Board id is the same physical unit across techs
    # (confirmed elsewhere: DU type must agree across eNB Info/gNB Info/
    # 5G Info for one TMBB/MMBB node), so (board, port) - not eNBId - is
    # the right cross-tech key.
    fiveg_rows_all = cer.sheet_rows_as_dicts(ciq_wb["5G Info"]) if "5G Info" in ciq_wb.sheetnames else []
    board_port_5g = {}
    for r5 in fiveg_rows_all:
        board = str(r5.get("BB/XMU") or "").strip()
        cell5 = str(r5.get("NRCellDU") or "").strip()
        colo5 = {c.strip().upper() for c in str(r5.get("Co-Located Technology Cell") or "").split(",") if c.strip()}
        for pc in ("Port 1", "Port 2", "Port 3", "Port 4"):
            v = str(r5.get(pc) or "").strip().upper()
            if v and v not in ("N/A", "NOT USED"):
                board_port_5g.setdefault((board, v), []).append((cell5, colo5))

    xmu_ports_by_enb = {}
    for r_enb in cer.enb_info_rows(ciq_wb):
        enb = str(r_enb.get("eNBId") or "").strip()
        for which in ("1st", "2nd"):
            if str(r_enb.get(f"{which} XMU", "")).strip().upper() != "YES":
                continue
            for k in (1, 2, 3):
                v = str(r_enb.get(f"{which} XMU Port {k}") or "").strip().upper()
                if v and v not in ("", "N/A", "NOT USED"):
                    xmu_ports_by_enb.setdefault(enb, set()).add(v)

    for i, r in enumerate(rows):
        board = str(r.get("DUS / XMU") or "").strip()
        cell = str(r.get("EutranCellFDDId") or "").strip()
        colo = {c.strip().upper() for c in str(r.get("Co-Located Technology Cell") or "").split(",") if c.strip()}
        enb = str(r.get("eNBId") or "").strip()
        node_xmu_ports = xmu_ports_by_enb.get(enb, set())
        for pc in ("DUS / XMU Port", "DUS / XMU Port Expansion", "DUS / XMU Port #2"):
            v = str(r.get(pc) or "").strip().upper()
            if not v or v in ("N/A", "NOT USED"):
                continue
            if v in node_xmu_ports:
                add(i, f"Port Clash (Port={v} on this node's XMU but also assigned to: {cell})")
                continue
            other5g = [c for c, _ in board_port_5g.get((board, v), []) if c and c != cell]
            if other5g and not (colo & {c.upper() for c in other5g}):
                add(i, f"Port Clash (Port={v} on board {board} shared with 5G cell(s): {', '.join(sorted(set(other5g)))}) \u2014 Add to Co-Located field")

    # ── PCI calculation: PCI == PhysicalLayerCellIdGroup*3 + physicalLayerSubCellId ──
    for i, r in enumerate(rows):
        grp, sub, pci = r.get("PhysicalLayerCellIdGroup"), r.get("physicalLayerSubCellId"), r.get("PCI")
        if grp in (None, "") or sub in (None, "") or pci in (None, ""):
            continue
        try:
            g, s, p = int(float(grp)), int(float(sub)), int(float(pci))
        except (TypeError, ValueError):
            continue
        expected = g * 3 + s
        if expected != p:
            add(i, f'Incorrect PCI calculation on: {r.get("EutranCellFDDId")} '
                   f'(PCI={p}, expected PhysicalLayerCellIdGroup*3+physicalLayerSubCellId={expected})')

    # ── Electrical tilt must be an integer ──
    for i, r in enumerate(rows):
        tilt = r.get("electricalAntennaTilt")
        if tilt in (None, ""):
            continue
        try:
            t = float(tilt)
        except (TypeError, ValueError):
            continue
        if not t.is_integer():
            add(i, f'Electrical tilt is not integer for {r.get("EutranCellFDDId")} (value={tilt})')

    # ── RBB type vs noOfTxAntennas/noOfRxAntennas/Radio Port — reuse the
    # canonical checks_sector.check_rbb_tx_isdlonly_4g result (Checklist
    # row 71 / Consolidated Report's CIQ Sanity Check), matched by cell,
    # rather than a second independent implementation here. An earlier
    # version re-derived this inline, which duplicated that check's exact
    # logic in a second place with no guarantee the two would stay in
    # sync. ──
    rbb_note_by_cell = {r.get("cell"): r.get("note") for r in (rbb_results or [])
                         if r.get("status") == "MISMATCH" and r.get("cell")}
    for i, r in enumerate(rows):
        cell = r.get("EutranCellFDDId")
        note = rbb_note_by_cell.get(cell)
        if note:
            add(i, note)

    # ── Antenna uniqueness — reuse this project's own confirmed check.
    # MISMATCH only: MATCH means the pairing is correctly configured
    # (shared where it should share, or unique where the 4890/8843
    # exception requires it), which is not a warning and should not appear
    # in the Comments/Warning column — confirmed real cases where the
    # unconditional version below was flagging correctly-configured pairs
    # ('shared', 'Unique - 4890 Radio') as if they were problems. ──
    antenna_notes = {}
    for res in cs.check_antenna_uniqueness(node_id="__all__", ciq_wb=ciq_wb):
        if res.get("status") != "MISMATCH":
            continue
        for cell in str(res.get("cell", "")).split(" / "):
            cell = cell.strip()
            if cell:
                antenna_notes.setdefault(cell, []).append(
                    f'[AUG/AU/ASU] {res.get("verdict", res.get("note", ""))}'
                    f' (paired with {" / ".join(c for c in str(res.get("cell", "")).split(" / ") if c.strip() != cell)})'
                )
    for i, r in enumerate(rows):
        cell = r.get("EutranCellFDDId")
        for note in antenna_notes.get(cell, []):
            add(i, note)

    out = []
    for i, r in enumerate(rows):
        node_name = node_by_enb.get(str(r.get("eNBId") or "").strip(), "")
        # "#2" is sheet_rows_as_dicts()'s key for a genuine SECOND
        # "DUS / XMU Port" column on CIQs that literally typed the header
        # twice instead of using "DUS / XMU Port Expansion" - confirmed
        # real dual-RIport cells (e.g. HXL04468_9A_1: D,K) live there.
        # Including both never double-counts: a file with a real
        # "Expansion" column has no "#2" key (None, dropped by
        # _clean_ports), and a file with a genuine duplicate-named column
        # has no "Expansion" key.
        riport = _clean_ports(r.get("DUS / XMU Port"), r.get("DUS / XMU Port Expansion"),
                               r.get("DUS / XMU Port #2")) or "-"
        riport_warn = _riport_format_warning(riport)
        if riport_warn:
            add(i, riport_warn)
        cell_comments = comments[i]
        out.append({
            "node": node_name, "cell": r.get("EutranCellFDDId"), "pci": r.get("PCI"),
            "cell_id": r.get("cellId"),
            "electrical_tilt": r.get("electricalAntennaTilt"), "rbb_type": r.get("RBB type"),
            "tx": r.get("noOfTxAntennas"), "rx": r.get("noOfRxAntennas"),
            "riport": riport, "link": "-",  # filled in by build_link_map()
            "link_name": _clean_link_name(r.get("Radio Port")),
            # NSB-layout raw fields (checks_node/rc.classify_site_type ==
            # "NSB" swaps to a different column set in Streamlit app.py) -
            # straight off the eUtran Parameters row, no derived logic.
            "cell_range": r.get("cellRange"), "isdlonly": r.get("ISDLONLY"),
            "earfcn_dl": r.get("earfcnDl"), "earfcn_ul": r.get("earfcnUl"),
            "dl_bw": r.get("dlChannelBandwidth"), "ul_bw": r.get("ulChannelBandwidth"),
            "output_power": r.get("configuredOutputPower"), "rru_type": r.get("RRU type"),
            "sector_id": r.get("sectorId"), "dus_xmu": r.get("DUS / XMU"),
            "dus_xmu_port": r.get("DUS / XMU Port"), "dus_xmu_port_exp": r.get("DUS / XMU Port Expansion"),
            "high_capacity_site": r.get("High Capacity Site"),
            "comments": cell_comments,
            "comments_html": _format_warnings(cell_comments),
            "status": "MISMATCH" if cell_comments else "MATCH",
            "_enb_id": r.get("eNBId"), "_rru_type": r.get("RRU type"), "_radio_port": r.get("Radio Port"),
            # RIport (DUS/XMU + Port + Port Expansion) is the real per-
            # physical-radio identifier on this sheet, NOT "RRU type" (a
            # shared model name). Confirmed on a real CIQ: HXL00147_7A_1/
            # 7B_1/7C_1 all report RRU type "RRUS 4449" but are three
            # DIFFERENT physical radios on ports A/B/C respectively — using
            # RRU type as the sharing/link key falsely flagged every
            # same-model cross-sector trio as "sharing radio", which is
            # normal, not a fault. HXL04147_2A_1/2A_3/9A_1 correctly share
            # port "D,G" and ARE the same physical radio on the same sector
            # carrying multiple carriers — a genuine, non-faulty case RIport
            # still gets right where RRU type also happened to get it right
            # by coincidence (same model, but here it's also the same radio).
            "_fru": riport if riport != "-" else None,
        })
    return out


def build_nr_ciq_rows(ciq_wb):
    """One row per 5G Info entry: Node, Cell, SEF, FRU, NR PCI, Electrical
    Tilt, RBB Type Verification, RIPORT, Comments — matches QUICKIX HTML's
    5G NR Parameters card. Also NOT rendered as a visible column in the
    original HTML (it only sets tr.title, a hover tooltip) — this project
    shows it as its own Comments column instead, per instruction to make it
    visible the same way the LTE card already does."""
    rows = cer.sheet_rows_as_dicts(ciq_wb["5G Info"]) if "5G Info" in ciq_wb.sheetnames else []
    if not rows:
        return []
    _, node_by_gnb = _node_name_maps(ciq_wb)

    mixed_gnb_name = {}
    for m in cer.mixed_mode_rows(ciq_wb):
        gnb = str(m.get("gNBId") or "").strip()
        if gnb:
            mixed_gnb_name[gnb] = str(m.get("gNodeB Name") or "").strip()
    gnb_info_name = {}
    if "gNB Info" in ciq_wb.sheetnames:
        for r in cer.sheet_rows_as_dicts(ciq_wb["gNB Info"]):
            gnb = str(r.get("gNBId") or "").strip()
            if gnb:
                gnb_info_name[gnb] = str(r.get("gNodeB Name") or "").strip()

    comments = {i: [] for i in range(len(rows))}

    def add(i, text):
        comments[i].append(text)

    # ── CellID Clash: same gNBId + cellLocalId ──
    cellid_groups = {}
    for i, r in enumerate(rows):
        key = (r.get("gNBId"), r.get("cellLocalId"))
        cellid_groups.setdefault(key, []).append(i)
    for idxs in cellid_groups.values():
        if len(idxs) > 1:
            ref = rows[idxs[0]]
            node_name = node_by_gnb.get(str(ref.get("gNBId") or "").strip(), str(ref.get("gNBId") or ""))
            cells = ", ".join(str(rows[i].get("NRCellDU") or "") for i in idxs)
            for i in idxs:
                add(i, f'CellID Clash (CellID={ref.get("cellLocalId")} on Node {node_name}: {cells})')

    # ── Carrier Reused Across Bands ──
    carrier_bands, carrier_idxs = {}, {}
    for i, r in enumerate(rows):
        carrier = str(r.get("Carrier") or "").strip()
        if not carrier:
            continue
        key = (r.get("gNBId"), carrier)
        carrier_bands.setdefault(key, set()).add(r.get("Operating Band"))
        carrier_idxs.setdefault(key, []).append(i)
    for key, bands in carrier_bands.items():
        if len(bands) > 1:
            if bl.same_underlying_band(bands):
                continue
            idxs = carrier_idxs[key]
            ref = rows[idxs[0]]
            node_name = node_by_gnb.get(str(ref.get("gNBId") or "").strip(), str(ref.get("gNBId") or ""))
            add(idxs[0], f'Carrier Reused Across Bands (Carrier={key[1]} on Node {node_name} Bands: {", ".join(str(b) for b in bands)}')

    # ── Band Config / PCI Clash / RACH Clash within same gNBId + arfcnDL group ──
    arfcn_groups = {}
    for i, r in enumerate(rows):
        key = (r.get("gNBId"), r.get("arfcnDL"))
        arfcn_groups.setdefault(key, []).append(i)
    for idxs in arfcn_groups.values():
        ref = rows[idxs[0]]
        node_name = node_by_gnb.get(str(ref.get("gNBId") or "").strip(), str(ref.get("gNBId") or ""))
        arfcn = ref.get("arfcnDL")
        for i in idxs:
            r = rows[i]
            if (r.get("bSChannelBwDL") != ref.get("bSChannelBwDL") or r.get("bSChannelBwUL") != ref.get("bSChannelBwUL")
                    or r.get("configuredMaxTxPower") != ref.get("configuredMaxTxPower")
                    or r.get("ssbFrequency") != ref.get("ssbFrequency") or r.get("ssbOffset") != ref.get("ssbOffset")):
                add(i, f"Band Config Mismatch (arfcnDL={arfcn})")
        pci_groups, rach_groups = {}, {}
        for i in idxs:
            pci = str(rows[i].get("nRPCI") or rows[i].get("PCI") or "")
            pci_groups.setdefault(pci, []).append(i)
            rach = str(rows[i].get("rachRootSequence") or "")
            rach_groups.setdefault(rach, []).append(i)
        for pci, pidxs in pci_groups.items():
            if len(pidxs) > 1:
                cells = ", ".join(str(rows[i].get("NRCellDU") or "") for i in pidxs)
                for i in pidxs:
                    add(i, f'PCI Clash (PCI={pci} on Node {node_name} arfcnDL={arfcn}: {cells})')
        for rach, ridxs in rach_groups.items():
            if len(ridxs) > 1 and rach:
                cells = ", ".join(str(rows[i].get("NRCellDU") or "") for i in ridxs)
                for i in ridxs:
                    add(i, f'RACH Clash (RACH={rach} on Node {node_name} arfcnDL={arfcn}: {cells})')

    # ── NRCellCU / NRSectorCarrier / gNB Name consistency ──
    for i, r in enumerate(rows):
        du = str(r.get("NRCellDU") or "").strip()
        cu = str(r.get("NRCellCU") or "").strip()
        sc = str(r.get("NRSectorCarrier") or "").strip()
        if cu and cu != du:
            add(i, f'NRCellCU Mismatch (CU:"{cu}" vs DU:"{du}")')
        if sc and sc != du:
            add(i, f'NRSectorCarrier Mismatch (SC:"{sc}" vs DU:"{du}")')
        gnb = str(r.get("gNBId") or "").strip()
        g_name = str(r.get("gNB Name") or "").strip()
        if gnb and mixed_gnb_name.get(gnb) and mixed_gnb_name[gnb] != g_name:
            add(i, f'gNB Name Mismatch (5G Info:"{g_name}" vs Mixed:"{mixed_gnb_name[gnb]}")')
        if gnb and gnb_info_name.get(gnb) and gnb_info_name[gnb] != g_name:
            add(i, f'gNB Name Mismatch (5G Info:"{g_name}" vs gNB Info:"{gnb_info_name[gnb]}")')

    out = []
    for i, r in enumerate(rows):
        node_name = node_by_gnb.get(str(r.get("gNBId") or "").strip(), "")
        riport = _clean_ports(r.get("Port 1"), r.get("Port 2")) or "-"
        riport_warn = _riport_format_warning(riport)
        if riport_warn:
            add(i, riport_warn)
        cell_comments = comments[i]
        out.append({
            "node": node_name, "cell": r.get("NRCellDU"), "sef": r.get("SectorEquipmentFunction"),
            "fru": r.get("RRU FieldReplaceableUnit"), "nr_pci": r.get("nRPCI"),
            "cell_id": r.get("cellLocalId"),
            "electrical_tilt": r.get("Electrical Tilt"), "rbb_type": r.get("RBB Type"),
            "riport": riport, "link": "-",
            "link_name": _clean_link_name(r.get("Radio Port")),
            # NSB-layout raw fields, straight off the 5G Info row.
            "nrtac": r.get("nRTAC"), "rru_type": r.get("RRU Type"),
            "arfcn_dl": r.get("arfcnDL"), "arfcn_ul": r.get("arfcnUL"),
            "dl_bw": r.get("bSChannelBwDL"), "ul_bw": r.get("bSChannelBwUL"),
            "output_power": r.get("configuredMaxTxPower"), "rach": r.get("rachRootSequence"),
            "dss": r.get("DSS"), "ssb_freq": r.get("ssbFrequency"), "ssb_offset": r.get("ssbOffset"),
            "ssb_duration": r.get("ssbDuration"), "nsa_sa": r.get("NSA/SA"), "vonr": r.get("VoNR"),
            "comments": cell_comments,
            "comments_html": _format_warnings(cell_comments),
            "status": "MISMATCH" if cell_comments else "MATCH",
            "_gnb_id": r.get("gNBId"), "_rru_type": r.get("RRU Type"), "_radio_port": r.get("Radio Port"),
            "_fru": r.get("RRU FieldReplaceableUnit"),  # the actual physical unit id (e.g. 'RRU-N005A') —
                                                          # 'RRU Type' here is a shared MODEL name across
                                                          # physically distinct radios (confirmed: 3 different
                                                          # sectors' RRU-N005A/B/C all report RRU Type='RRUS 4449'),
                                                          # so Sharing Radio / Link must key on FRU, not RRU Type.
        })
    return out


def _format_warnings(comment_list):
    if not comment_list:
        return "-"
    return " | ".join(comment_list)


def apply_link_and_sharing(lte_rows, nr_rows, ciq_wb=None):
    """Fills 'link' (Single/Double, same node+RRU+RadioPort dual-carrier
    rule the HTML uses) and adds a Sharing Radio comment (cross-sector, same
    RRU+band — new logic, ported from amos_view.build_lte_cell_rows()'s Pre
    checks rule) directly onto the row dicts build_lte_ciq_rows() /
    build_nr_ciq_rows() already produced. Mutates and returns both lists.

    ciq_wb is optional (defaults to None, a no-op for the Nokia vs Ericsson
    pass) so existing callers that don't pass it keep working; Streamlit
    app.py's CIQ Checks tab passes the workbook so N2E sites get the Nokia
    vs Ericsson comparison folded in."""
    # ── Link (Single/Doublelink): per QUICKIX's ciqBuildRadioMap() Pass 1 —
    # aggregate RadioPort values per (node, RRU type). A single physical RRU
    # exposes DATA1/DATA2 (or more) as SEPARATE Radio Port values across its
    # rows; Double Link means that (node, RRU) pair has 2+ DISTINCT
    # RadioPort values, not just 2+ rows/sectors sharing the same RRU
    # model+node (three different sectors on the same RRUS 4449 model, all
    # on RadioPort=DATA1, is still Single Link — confirmed against a real
    # CIQ where HXL00147_7A_1/7B_1/7C_1 share node+RRU type but all report
    # DATA1 only, and the tool's own Link column shows Single Link for all
    # three).
    rru_ports = {}
    for r in lte_rows:
        node, rru = r.get("node") or r.get("_enb_id"), r.get("_fru") or r.get("_rru_type")
        rp_raw = str(r.get("_radio_port") or "").strip().upper()
        for rp in re.split(r'[/,]', rp_raw):
            rp = rp.strip()
            if node and rru and rp and rp != "N/A":
                rru_ports.setdefault((node, rru), set()).add(rp)
    for r in nr_rows:
        node, rru = r.get("node") or r.get("_gnb_id"), r.get("_fru") or r.get("_rru_type")
        rp_raw = str(r.get("_radio_port") or "").strip().upper()
        for rp in re.split(r'[/,]', rp_raw):
            rp = rp.strip()
            if node and rru and rp and rp != "N/A":
                rru_ports.setdefault((node, rru), set()).add(rp)
    for r in lte_rows + nr_rows:
        node = r.get("node") or r.get("_enb_id") or r.get("_gnb_id")
        rru = r.get("_fru") or r.get("_rru_type")
        ports = rru_ports.get((node, rru), set())
        r["link"] = "Double Link" if len(ports) > 1 else "Single Link"

    # ── Sharing Radio: same RRU + same band, DIFFERENT sector letters.
    # Exposed as its OWN column (r["sharing_radio"]) as well as folded into
    # comments, matching Pre checks' Sharing Radio column convention. ──
    def _sharing_pass(rows, cell_key):
        for r in rows:
            r["sharing_radio"] = "No"
        radio_band_map = {}
        for r in rows:
            cell = r.get(cell_key)
            band, sector = bl.band_label(cell) if cell else (None, None)
            # _fru ONLY - no "or r.get('_rru_type')" fallback. _rru_type is
            # a shared MODEL NAME (e.g. "RRUS 4449"), not a physical radio
            # identity - every normal 3-sector site uses the same model on
            # all 3 sectors, so falling back to it here re-introduces the
            # exact false positive build_lte_ciq_rows()'s own "_fru" comment
            # says it fixed (confirmed real: HXL04468_7A_1/7B_1/7C_1, three
            # SEPARATE physical RRUs sharing model "RRUS 4449" with RIPORT
            # "-"/blank on all three - fell back to _rru_type and was
            # flagged as cross-sector sharing on every row, when nothing is
            # actually shared). A row with no usable physical identifier
            # (_fru is None) has nothing reliable to compare and is
            # skipped, not assumed-shared.
            rru = r.get("_fru")
            node = r.get("node")
            if not (band and sector and rru and node):
                continue
            key = (node, rru, band)
            radio_band_map.setdefault(key, {}).setdefault(sector, set()).add(cell)
        for r in rows:
            cell = r.get(cell_key)
            band, sector = bl.band_label(cell) if cell else (None, None)
            rru = r.get("_fru")
            node = r.get("node")
            if not (band and sector and rru and node):
                continue
            by_sector = radio_band_map.get((node, rru, band), {})
            shared = {c for sec, cs_ in by_sector.items() if sec != sector for c in cs_}
            if shared:
                shared_list = ", ".join(sorted(shared))
                r["sharing_radio"] = shared_list
                r["comments"].append(f'Sharing Radio (RRU shared cross-sector with: {shared_list})')
                r["comments_html"] = _format_warnings(r["comments"])

    _sharing_pass(lte_rows, "cell")
    _sharing_pass(nr_rows, "cell")

    # ── Nokia vs Ericsson (N2E only) — no-op when ciq_wb wasn't passed, or
    # Nokia_Info is absent/empty (Legacy/NSB), see apply_nokia_vs_ericsson()'s
    # own docstring. ──
    if ciq_wb is not None:
        apply_nokia_vs_ericsson(ciq_wb, lte_rows, nr_rows)

    # 'status' drives the CIQ Checks tab's row highlighting (render_table's
    # status_key) — recomputed HERE, after every comment source (including
    # the Sharing Radio pass just above) has had its say, so a row that
    # only picked up a late Sharing Radio comment still highlights red
    # rather than showing stale MATCH from before this pass ran.
    for r in lte_rows + nr_rows:
        r["status"] = "MISMATCH" if r.get("comments") else "MATCH"

    return lte_rows, nr_rows


def _nokia_bw_mhz(raw):
    """'10 MHz' / '5MHz' -> 10.0 / 5.0. Returns None if unparseable."""
    if raw is None:
        return None
    m = re.search(r'([\d.]+)\s*MHZ', str(raw).upper())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _nz_eq(a, b):
    """String-normalized equality, treating None/''/'N/A' as all equal to
    each other (both blank -> not a mismatch, one blank -> real mismatch,
    handled by the caller checking truthiness first)."""
    return str(a).strip().upper() == str(b).strip().upper()


def _enb_tac(ciq_wb):
    """Single site-wide TAC value off the 'eNB Info' sheet (one row per
    CIQ). Used as the cross-check target for Nokia_Info's per-row 'tac'
    column - confirmed on real data that Nokia_Info's tac is the same
    constant on every row (4G AND 5G) and matches eNB Info's tac exactly
    (both 18698 on NDL94872), so one lookup covers both techs. Returns None
    if the sheet or a usable row isn't there."""
    if "eNB Info" not in ciq_wb.sheetnames:
        return None
    for r in cer.sheet_rows_as_dicts(ciq_wb["eNB Info"]):
        if str(r.get("eNBId") or "").strip():
            return r.get("tac")
    return None


def apply_nokia_vs_ericsson(ciq_wb, lte_rows, nr_rows):
    """N2E only: cross-checks each Nokia_Info row's recorded Nokia-side
    values against BOTH the same row's own Ericsson-side Nokia_Info columns
    AND the ACTUAL Ericsson-side CIQ (eUtran Parameters / 5G Info / eNB
    Info) for the cell it was migrated to, folding any mismatch into that
    cell's existing comments/status - same mechanism as every other CIQ
    Checks comment. Per exact scope given (2026-09-25): only these fields
    are COMPARED - everything else Nokia_Info carries (PCI, nRPCI,
    expectedCellSize, qrxlevmin, Pmax) is intentionally NOT compared.
    Nokia crsGain has no Ericsson-side equivalent anywhere in the CIQ, so
    it's carried through as display-only ('nokia_crs_gain'), never flagged.

      - Nokia Cell Id      vs Nokia_Info's 'Ericsson Cell Id' AND the real
                            eUtran Parameters 'cellId' / 5G Info
                            'cellLocalId' on the matched row.
      - Nokia channelNumberDL vs Nokia_Info's 'Ericsson channelNumberDL' AND
                            the real 'earfcnDl' (4G) / 'arfcnDL' (5G).
      - Nokia Bandwidth    ('10 MHz' style) vs Nokia_Info's 'Ericsson
                            Bandwidth' AND the real 'dlChannelBandwidth' /
                            'bSChannelBwDL' - all normalized to MHz first,
                            since confirmed on real files that Nokia_Info's
                            own 'Ericsson Bandwidth' column (and the real
                            CIQ field) is in kHz for 4G rows but MHz for 5G
                            rows.
      - ssbfrequency (5G rows only) vs the real 5G Info 'ssbFrequency' -
        Nokia_Info has only one column for this (no Ericsson-side pair), so
        this only ever compares against the real CIQ value.
      - tac vs the CIQ's 'eNB Info' sheet tac (single site-wide value) -
        applies to both 4G and 5G rows, confirmed both carry the same tac
        on Nokia_Info.
      - 'Nokia FDD/TDD' / 'Ericsson FDD/TDD' are mislabeled in this CIQ
        template - they actually hold the Nokia/Ericsson CELL NAME
        (confirmed: 'AZL01006_7A_1' / 'AZL91006_7A_1'), not a duplex value,
        and by design never match (different vendor naming), so this pair
        is carried as informational context only ('nokia_cell' field) and
        never flagged.

    No-op (both row lists unchanged) when Nokia_Info is missing or empty -
    Legacy and NSB sites never reach the comparison loop."""
    nokia_rows = cer.sheet_rows_as_dicts(ciq_wb["Nokia_Info"]) if "Nokia_Info" in ciq_wb.sheetnames else []
    nokia_rows = [r for r in nokia_rows if str(r.get("Nokia Cell Id") or "").strip()]
    if not nokia_rows:
        return

    lte_by_cell = {r.get("cell"): r for r in lte_rows if r.get("cell")}
    nr_by_cell = {r.get("cell"): r for r in nr_rows if r.get("cell")}
    enb_tac = _enb_tac(ciq_wb)

    def _check(nk_val, sources, label, mismatches):
        """sources: list of (tag, value) pairs to compare nk_val against.
        Blank/N-A on either side skips that pair (nothing to compare)."""
        if str(nk_val or "").strip().upper() in ("", "N/A"):
            return
        diffs = []
        for tag, val in sources:
            if val in (None, "") or str(val).strip().upper() == "N/A":
                continue
            if not _nz_eq(nk_val, val):
                diffs.append(f"{tag} {val}")
        if diffs:
            mismatches.append(f"{label} (Nokia {nk_val} vs {', '.join(diffs)})")

    for nk in nokia_rows:
        tech = str(nk.get("Technology") or "").strip().upper()
        is_5g = tech in ("5G", "NR")
        ericsson_cell = str(nk.get("Ericsson FDD/TDD") or "").strip()
        nokia_cell = str(nk.get("Nokia FDD/TDD") or "").strip()
        target = (nr_by_cell if is_5g else lte_by_cell).get(ericsson_cell)
        if not target:
            continue  # cell not in this CIQ's eUtran Parameters/5G Info - nothing to attach the flag to

        mismatches = []

        _check(nk.get("Nokia Cell Id"), [
            ("Ericsson (Nokia_Info)", nk.get("Ericsson Cell Id")),
            ("Ericsson (CIQ)", target.get("cell_id")),
        ], "Cell Id", mismatches)

        er_chan_ciq = target.get("arfcn_dl") if is_5g else target.get("earfcn_dl")
        _check(nk.get("Nokia channelNumberDL"), [
            ("Ericsson (Nokia_Info)", nk.get("Ericsson channelNumberDL")),
            ("Ericsson (CIQ)", er_chan_ciq),
        ], "channelNumberDL", mismatches)

        # Bandwidth: normalize every side to MHz before comparing - Nokia
        # Bandwidth is '10 MHz' style; Nokia_Info's own 'Ericsson Bandwidth'
        # column AND the real CIQ dl_bw field are both in kHz for 4G rows
        # but MHz for 5G rows (confirmed on all 6 real N2E files).
        nk_bw_mhz = _nokia_bw_mhz(nk.get("Nokia Bandwidth"))
        if nk_bw_mhz is not None:
            bw_diffs = []
            for tag, raw in (("Ericsson (Nokia_Info)", nk.get("Ericsson Bandwidth")),
                              ("Ericsson (CIQ)", target.get("dl_bw"))):
                if raw in (None, ""):
                    continue
                try:
                    raw_f = float(raw)
                except ValueError:
                    continue
                raw_mhz = raw_f if is_5g else raw_f / 1000.0
                if abs(nk_bw_mhz - raw_mhz) > 0.01:
                    bw_diffs.append(f"{tag} {raw}")
            if bw_diffs:
                mismatches.append(f"Bandwidth (Nokia {nk.get('Nokia Bandwidth')} vs {', '.join(bw_diffs)})")

        if is_5g:
            _check(nk.get("ssbfrequency"), [
                ("Ericsson (CIQ)", target.get("ssb_freq")),
            ], "ssbFrequency", mismatches)

        _check(nk.get("tac"), [
            ("eNB Info", enb_tac),
        ], "tac", mismatches)

        target["nokia_cell"] = nokia_cell
        target["nokia_crs_gain"] = nk.get("Nokia crsGain")
        if mismatches:
            note = "Nokia vs Ericsson mismatch: " + "; ".join(mismatches)
            target["nokia_vs_ericsson"] = note
            target["comments"].append(note)
            target["comments_html"] = _format_warnings(target["comments"])
        else:
            target["nokia_vs_ericsson"] = "Match"
