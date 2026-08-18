"""
Scope of Work summary as prose lines - ported from QUICKIX's
format_scope_of_work() / scope_lines_to_table() / scope_lines_to_readable_text()
in app.py, adapted to this tool's classify_carriers() output (sow_analysis.py).

Not ported: the "Port speed 1G to 10G conversion with MPST" line. QUICKIX
derives it from the Pre-checks PDF's Transport Fiber link Status table
(TRANSPORT_FIBER_ROW_RE, opmode per TN_A/TN_B/TN_IDL_B/TN_IDL_C port) - no
Pre kget-all equivalent has been confirmed yet, so this line is left out
rather than guessed at. Flagged here, not silently dropped.
"""
import re

from band_labels import band_label, dedupe_labels, SECTOR_ORDER
from ciq_edp_reader import sheet_rows_as_dicts

DU_TYPE_TO_GEN = {"6630": "G2", "5216": "G2", "6648": "G3", "6651": "G3", "6672": "G4"}


def build_target_band_sectors(ciq_wb, mm_objs):
    """(node, band_label) -> set of sector letters present in the CIQ target
    (post-scripting) cell list - used to tell 'whole band added' from
    'partial band added' in the Integration line, same distinction QUICKIX
    makes via its own target_band_sectors."""
    result = {}
    eutran_objs = sheet_rows_as_dicts(ciq_wb['eUtran Parameters']) if 'eUtran Parameters' in ciq_wb.sheetnames else []
    fiveg_objs = sheet_rows_as_dicts(ciq_wb['5G Info']) if '5G Info' in ciq_wb.sheetnames else []
    node_by_e = {r.get('eNodeB Name'): r.get('Node to be built as') for r in mm_objs if r.get('eNodeB Name')}
    node_by_g = {r.get('gNodeB Name'): r.get('Node to be built as') for r in mm_objs if r.get('gNodeB Name')}

    for row in eutran_objs:
        cell = row.get('EutranCellFDDId')
        if not cell:
            continue
        for e_name, node in node_by_e.items():
            if str(cell).startswith(str(e_name)):
                label, sector = band_label(cell)
                if label and sector:
                    result.setdefault((node, label), set()).add(sector)
                break
    for row in fiveg_objs:
        cell = row.get('NRCellDU')
        if not cell:
            continue
        for g_name, node in node_by_g.items():
            if str(cell).startswith(str(g_name)):
                label, sector = band_label(cell)
                if label and sector:
                    result.setdefault((node, label), set()).add(sector)
                break
    return result


def get_controller_id(ciq_wb):
    """First non-blank 6610 Controller ID in Controller Info, or None."""
    if 'Controller Info' not in ciq_wb.sheetnames:
        return None
    for r in sheet_rows_as_dicts(ciq_wb['Controller Info']):
        if str(r.get('Controller', '')).strip() == '6610':
            cid = r.get('Controller ID')
            if cid and str(cid).strip():
                return str(cid).strip()
    return None


def format_scope_of_work(classification, ciq_wb):
    """Returns a list of tab-separated lines, same shape as QUICKIX's
    format_scope_of_work() output, ready for scope_lines_to_readable_text()."""
    lines = []
    target_band_sectors = classification.get('target_band_sectors', {})

    for node, cells in classification.get('added', {}).items():
        labels = dedupe_labels(cells)
        added_by_label = {}
        for c in cells:
            label, sector = band_label(c)
            if label and sector:
                added_by_label.setdefault(label, set()).add(sector)
        parts = []
        for label in labels:
            added_sectors = added_by_label.get(label, set())
            target_sectors = target_band_sectors.get((node, label))
            if not target_sectors or added_sectors >= target_sectors:
                parts.append(label)
            else:
                sector_names = sorted(added_sectors, key=lambda s: SECTOR_ORDER.index(s) if s in SECTOR_ORDER else 99)
                parts.append(f"{label} {', '.join(sector_names)}")
        lines.append(f"Integration:\t{'/'.join(parts)}\t{node}")

    ctrl_id = get_controller_id(ciq_wb)
    if ctrl_id:
        lines.append(f"6610 Controller Integration:\t{ctrl_id}")

    moved_by_pair = {}
    for m in classification.get('moved', []):
        key = (m['from_node'], m['to_node'])
        moved_by_pair.setdefault(key, []).append(m['cell'])
    WHOLE_BAND_SET = {"Alpha", "Beta", "Gamma"}
    for (from_node, to_node), cells in moved_by_pair.items():
        if not to_node or not any(cells):
            lines.append(f"Moved Sectors:\tCHECK CIQ — incomplete Sector Del_Movement row\tFrom:\t{from_node or 'NOT FOUND'}\tTo:\t{to_node or 'NOT FOUND'}")
            continue
        labels = dedupe_labels(cells)
        label_str = labels[0] if len(labels) == 1 else f"[{'/'.join(labels)}]"
        per_label_moved = {}
        for c in cells:
            label, sector = band_label(c)
            if label and sector:
                per_label_moved.setdefault(label, set()).add(sector)
        is_whole = bool(per_label_moved) and all(WHOLE_BAND_SET <= sset for sset in per_label_moved.values())
        sector_names = sorted({s for sset in per_label_moved.values() for s in sset},
                               key=lambda s: SECTOR_ORDER.index(s) if s in SECTOR_ORDER else 99)
        sectors_str = "" if is_whole else (f" {', '.join(sector_names)}" if sector_names else "")
        lines.append(f"Moved Sectors:\t{label_str}{sectors_str}\tFrom:\t{from_node}\tTo:\t{to_node}")

    deleted_nodes = classification.get('deleted_nodes', [])
    if deleted_nodes:
        lines.append(f"Deleted Node from ENM:\t{'|'.join(deleted_nodes)}")

    for node, cells in classification.get('deleted_sectors', {}).items():
        labels = dedupe_labels(cells)
        lines.append(f"Deleted Sector:\t{'/'.join(labels)}\t{node}")

    retune_seen = set()
    for r in classification.get('retuned', []):
        sig = (r['label'], r['from'], r['to'])
        if sig in retune_seen:
            continue
        retune_seen.add(sig)
        lines.append(f"Retune on:\t{r['label']}\tFrom:\t{r['from']}\tTo:\t{r['to']}")

    return lines


def scope_lines_to_readable_text(scope_lines):
    """Ported verbatim from QUICKIX: compact readable sentences, no raw tabs."""
    out = []
    for line in scope_lines:
        parts = line.split("\t")
        category = parts[0].rstrip(":")
        if "From:" in parts:
            fi = parts.index("From:")
            details = " ".join(p for p in parts[1:fi] if p)
            from_val = parts[fi + 1] if fi + 1 < len(parts) else ""
            ti = parts.index("To:") if "To:" in parts else None
            to_val = parts[ti + 1] if ti is not None and ti + 1 < len(parts) else ""
            out.append(f"{category}: {details}  From: {from_val}  To: {to_val}")
        else:
            details = " — ".join(p for p in parts[1:] if p)
            out.append(f"{category}: {details}" if details else category)
    return out
