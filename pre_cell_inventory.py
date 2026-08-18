"""
Pre-side cell inventory, derived from Pre kget-all logs' 'st cell' (LTE) and
'st nrcell' (5G) output — this project has no Pre-checks PDF at all, so this
replaces QUICKIX's extract_precheck_sectors() (which parsed the PDF's
'Summary Status' table) with an equivalent derived from live kget-all data.
Produces the same (pre_pairs, pre_nodes) shape so classify_carriers() in
sow_analysis.py can be reused with minimal changes.
"""
from log_parser import parse_log, find_command, all_rows


def extract_pre_cells_for_node(text):
    """Returns the set of cell names physically present on this node
    pre-scripting, from 'st cell' + 'st nrcell'. Presence in either table
    means the cell exists, regardless of lock state — mirrors QUICKIX's
    original PDF-based behavior of accepting both UNLOCKED and LOCKED rows."""
    parsed = parse_log(text)
    cells = set()
    for cmd_substr in ('st cell', 'st nrcell'):
        entry = find_command(parsed, cmd_substr)
        for row in all_rows(entry):
            mo = row.get('MO', '')
            if '=' in mo:
                cells.add(mo.rsplit('=', 1)[-1])
    return cells


def build_pre_inventory(node_logs):
    """node_logs: {node_id: log_text or None}. Returns (pre_pairs, pre_nodes):
    pre_pairs = set of (node_id, cell) tuples
    pre_nodes = set of node_ids that had a Pre kget-all log provided
    Nodes with no log (new builds) are simply absent from both — same
    "presence in Pre data = pre-existing" signal used throughout this tool."""
    pre_pairs = set()
    pre_nodes = set()
    for node_id, text in node_logs.items():
        if not text:
            continue
        pre_nodes.add(node_id)
        for cell in extract_pre_cells_for_node(text):
            pre_pairs.add((node_id, cell))
    return pre_pairs, pre_nodes
