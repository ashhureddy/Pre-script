"""
Pre/Post Configuration - prose summary format, matching QUICKIX's
generate_generic_pre_post() display style:

    Pre Configuration:  NodeA(hw) + NodeB(hw) + NodeC(hw)
    Post Configuration: Primary(P)/Secondary(S)(BBUMode)(hw)

Pre side: every node with a Pre kget-all log, shown flat (no P/S pairing -
per confirmed example, unlike QUICKIX's PDF-sourced pre_node_label which
does pair dual-tech Pre nodes; kget-all-sourced Pre here lists each node
that was queried, since a rehome/move scenario can have 3+ separate Pre
nodes feeding into one Post node).
Post side: one entry per CIQ Mixed Mode Info row, Primary(P)/Secondary(S)
pairing when both eNodeB Name and gNodeB Name are populated, else just the
primary name - ported from QUICKIX's node_label().
"""
import pre_extract as pe
import ciq_edp_reader as cer


def _is_populated(v):
    if v is None:
        return False
    s = str(v).strip().upper()
    return s not in ('', 'N/A')


def _post_node_label(row):
    primary = row.get('Node to be built as')
    e_name, g_name = row.get('eNodeB Name'), row.get('gNodeB Name')
    bbu_mode = row.get('BBU Mode')
    if _is_populated(e_name) and _is_populated(g_name):
        is_lte_primary = str(primary).strip().upper() == str(e_name).strip().upper()
        secondary = g_name if is_lte_primary else e_name
        return f"{primary}(P)/{secondary}(S)({bbu_mode})"
    return str(primary)


def build_pre_post_config_text(node_logs, ciq_wb):
    """node_logs: {node_id: log_text or None} for every node in scope (Pre
    kget-all-present or new-build). Returns (pre_text, post_text) strings."""
    enb_rows = cer.enb_info_rows(ciq_wb)
    gnb_rows = cer.sheet_rows_as_dicts(ciq_wb['gNB Info']) if 'gNB Info' in ciq_wb.sheetnames else []
    mm_rows = cer.mixed_mode_rows(ciq_wb)

    # ---- Pre: every node with a kget-all log, flat list ----
    pre_parts = []
    for node_id, text in node_logs.items():
        if not text:
            continue
        import log_parser as lp
        parsed = lp.parse_log(text)
        hw = pe.extract_hardware(parsed)
        boards = [pe.model_token(b['model']) for b in hw['boards']]
        model = boards[0] if boards else 'NOT FOUND'
        xmu_suffix = '' if not hw['xmus'] else (' + XMU' if len(hw['xmus']) == 1 else f" + {len(hw['xmus'])} XMU")
        pre_parts.append(f"{node_id}({model}{xmu_suffix})")

    # ---- Post: one per Mixed Mode Info row, Primary(P)/Secondary(S)(mode)(hw) ----
    post_parts = []
    for row in mm_rows:
        primary = row.get('Node to be built as')
        e_name, g_name = row.get('eNodeB Name'), row.get('gNodeB Name')
        label = _post_node_label(row)
        is_lte_primary = str(primary).strip().upper() == str(e_name or '').strip().upper()

        r = None
        if is_lte_primary:
            r = next((x for x in enb_rows if str(x.get('eNodeB Name', '')).strip().upper() == str(e_name).strip().upper()), None)
        else:
            r = next((x for x in gnb_rows if str(x.get('gNodeB Name', '')).strip().upper() == str(g_name).strip().upper()), None)
        if not r:
            r = (next((x for x in enb_rows if str(x.get('eNodeB Name', '')).strip().upper() == str(e_name).strip().upper()), None)
                 or next((x for x in gnb_rows if str(x.get('gNodeB Name', '')).strip().upper() == str(g_name).strip().upper()), None))
        du_type = str(r.get('DU type') or r.get('1st DU type') or '').strip() if r else ''
        hw = du_type or 'NOT FOUND'
        post_parts.append(f"{label}({hw})")

    return ' + '.join(pre_parts), ' + '.join(post_parts)
