"""
Carrier ADD / DELETE / MOVE / RETUNE classification — ported from QUICKIX's
classify_carriers() in app.py. The only real change: QUICKIX derived its Pre
cell inventory from the Pre-checks PDF via extract_precheck_sectors(); this
project has no Pre-checks PDF, so the caller passes in pre_pairs/pre_nodes
already built by pre_cell_inventory.build_pre_inventory() from the Pre
kget-all logs instead. The classification logic itself is otherwise
unchanged from the original.
"""
from band_labels import band_label, lte_band_label, nr_band_label
from ciq_edp_reader import sheet_rows_as_dicts


def build_node_alias_map(mm_objs):
    """A node's secondary identity (eNodeB or gNodeB name) can appear in
    Sector Del_Movement's Source/Target columns instead of its Primary ID -
    happens specifically when the moving cell's own technology matches the
    secondary identity (e.g. a 5G cell moving into a dual-identity node
    records the target using that node's gNodeB name, not its Primary
    'Node to be built as'). [ported unchanged from QUICKIX]"""
    alias = {}
    for row in mm_objs:
        primary = row.get("Node to be built as")
        if not primary:
            continue
        for secondary in (row.get("eNodeB Name"), row.get("gNodeB Name")):
            if secondary and str(secondary).strip() and str(secondary).strip() != str(primary).strip():
                alias[str(secondary).strip()] = str(primary).strip()
    return alias


def classify_carriers(ciq_wb, mm_objs, pre_pairs, pre_nodes):
    """Returns a dict: added (per node), moved, deleted_sectors, deleted_nodes,
    retuned, node_band_sectors. Same output shape as QUICKIX's original -
    pre_pairs/pre_nodes now come from the Pre kget-all logs (via
    pre_cell_inventory.build_pre_inventory) instead of a Pre-checks PDF."""
    result = {"added": {}, "moved": [], "deleted_sectors": {}, "deleted_nodes": [],
              "retuned": [], "node_band_sectors": {}}
    alias_map = build_node_alias_map(mm_objs)

    def normalize(name):
        return alias_map.get(str(name).strip(), name) if name else name

    pre_cells = {cell for (_, cell) in pre_pairs}

    # per (node, band label) sector inventory - used to tell "whole band moved"
    # from "partial move"
    node_band_sectors = {}
    for (node, cell) in pre_pairs:
        label, sector = band_label(cell)
        if label and sector:
            node_band_sectors.setdefault((node, label), set()).add(sector)

    ciq_nodes = {str(r.get("Node to be built as", "")).strip() for r in mm_objs if r.get("Node to be built as")}
    if pre_nodes:
        result["deleted_nodes"] = sorted(pre_nodes - ciq_nodes)

    delmove_objs = sheet_rows_as_dicts(ciq_wb["Sector Del_Movement"]) if "Sector Del_Movement" in ciq_wb.sheetnames else []
    handled_cells = set()

    for r in delmove_objs:
        src_node, src_sector = normalize(r.get("Source Node name")), r.get("Source Sector")
        tgt_node_raw, tgt_sector = r.get("Target Node name"), r.get("Target Sector")
        tgt_node = tgt_node_raw if str(tgt_node_raw).strip().upper() == "DELETE" else normalize(tgt_node_raw)
        handled_cells.add(src_sector)
        if str(tgt_node).strip().upper() == "DELETE":
            result["deleted_sectors"].setdefault(src_node, []).append(src_sector)
            continue
        handled_cells.add(tgt_sector)
        src_dl, tgt_dl = str(r.get("Source channelNumberDL", "")).strip(), str(r.get("Target channelNumberDL", "")).strip()
        src_bw, tgt_bw = str(r.get("Source Bandwidth", "")).strip(), str(r.get("Target Bandwidth", "")).strip()
        retuned = (src_dl != tgt_dl) or (src_bw != tgt_bw)
        if str(src_node).strip().upper() == str(tgt_node).strip().upper():
            if retuned:
                label, _ = lte_band_label(src_sector)
                if not label:
                    label, _ = nr_band_label(src_sector)
                result["retuned"].append({"label": label, "from": f"{src_dl}/{src_bw}", "to": f"{tgt_dl}/{tgt_bw}"})
        else:
            result["moved"].append({"cell": src_sector, "from_node": src_node, "to_node": tgt_node})
            if retuned:
                label, _ = lte_band_label(tgt_sector)
                if not label:
                    label, _ = nr_band_label(tgt_sector)
                result["retuned"].append({"label": label, "from": f"{src_dl}/{src_bw}", "to": f"{tgt_dl}/{tgt_bw}"})

    # ADD: any CIQ cell (LTE or 5G) not present in Pre kget-all inventory and
    # not already accounted for as moved/deleted
    eutran_objs = sheet_rows_as_dicts(ciq_wb["eUtran Parameters"]) if "eUtran Parameters" in ciq_wb.sheetnames else []
    fiveg_objs = sheet_rows_as_dicts(ciq_wb["5G Info"]) if "5G Info" in ciq_wb.sheetnames else []
    for r in mm_objs:
        node = r.get("Node to be built as")
        e_name, g_name = r.get("eNodeB Name"), r.get("gNodeB Name")
        added_here = []
        for row in eutran_objs:
            cell = row.get("EutranCellFDDId")
            if not cell or cell in handled_cells or cell in pre_cells:
                continue
            if e_name and str(cell).startswith(str(e_name)):
                added_here.append(cell)
        for row in fiveg_objs:
            cell = row.get("NRCellDU")
            if not cell or cell in handled_cells or cell in pre_cells:
                continue
            if g_name and str(cell).startswith(str(g_name)):
                added_here.append(cell)
        if added_here:
            result["added"][node] = added_here

    result["node_band_sectors"] = node_band_sectors
    return result
