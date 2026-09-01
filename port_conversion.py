"""
Port speed 1G -> 10G conversion detection - QUICKIX logic, re-sourced.

QUICKIX reads OpMode from the Pre-checks PDF's "Transport Fiber link Status"
table (TRANSPORT_FIBER_ROW_RE). This project has no Pre-checks PDF, so the
equivalent comes from the Pre kget-all's `get Ethernetport=` output, where
each EthernetPort MO block carries an `admOperatingMode  9 (10G_FULL)` /
`(1G_FULL)` line. Same decision inputs, same rule:

  - Board generation from the Pre board model (DU_TYPE_TO_GEN), G4 excluded
    outright (newest board, template has no G4 content).
  - Only the port labels relevant to that generation are considered
    (PORT_BY_GEN), e.g. G2 -> TN_A/TN_B.
  - Conversion is pending when that port is currently 1G AND the EDP's
    SIAD_PORT_SIZE_BBU calls for 10G.
"""
import re

import pre_extract as pe
import ciq_edp_reader as cer

DU_TYPE_TO_GEN = {"6630": "G2", "5216": "G2", "6648": "G3", "6651": "G3", "6672": "G4"}
PORT_BY_GEN = {"G2": ["TN_A", "TN_B"], "G3": ["TN_IDL_B"], "G4": ["TN_IDL_C"]}

# Each EthernetPort MO block in `get Ethernetport=` output starts with a
# separator-wrapped "<proxy>  Transport=1,EthernetPort=<id>" line, then lists
# attributes one per line. admOperatingMode is the current negotiated speed.
_ETH_PORT_RE = re.compile(
    r'EthernetPort=(?P<port>[^\s,]+)\s*\r?\n=+\s*\r?\n(?:.*\r?\n)*?admOperatingMode\s+\d+\s*\((?P<mode>[^)]+)\)',
    re.M
)


def extract_port_opmodes(log_text):
    """Returns {port_id: admOperatingMode}, e.g. {'TN_B': '1G_FULL'}.
    First occurrence per port wins (later duplicates are sub-MOs like
    'TN_B,QueueSystem=1', already excluded by the port pattern)."""
    result = {}
    if not log_text:
        return result
    for m in _ETH_PORT_RE.finditer(log_text):
        port = m.group('port')
        if port not in result:
            result[port] = m.group('mode')
    return result


def check_port_conversion(node_id, log_text, edp_rows, has_pre_log):
    """Returns a result dict for the Port Conversion check, including the
    values that drove it so the report can show them rather than a bare
    verdict."""
    if not has_pre_log:
        return {'rule': 'PORT_CONV', 'node': node_id, 'status': 'SKIPPED',
                'note': 'No Pre kget-all log — new build.'}

    import log_parser as lp
    parsed = lp.parse_log(log_text)
    hw = pe.extract_hardware(parsed)
    boards = [pe.model_token(b['model']) for b in hw['boards']]
    pre_model = boards[0] if boards else None
    gen = DU_TYPE_TO_GEN.get(str(pre_model).strip()) if pre_model else None

    edp_site_rows = cer.edp_rows_for_site(edp_rows, node_id)
    siad_size = str(edp_site_rows[0].get('SIAD_PORT_SIZE_BBU', '')).strip() if edp_site_rows else ''

    if gen not in ('G2', 'G3'):
        return {'rule': 'PORT_CONV', 'node': node_id, 'status': 'SKIPPED',
                'pre_board': pre_model, 'generation': gen or 'UNKNOWN',
                'port': '-', 'pre_speed': '-', 'edp_port_size': siad_size or '-',
                'note': f'Board generation {gen or "unknown"} — conversion template does not apply.'}

    opmodes = extract_port_opmodes(log_text)
    port_labels = PORT_BY_GEN[gen]
    found_port, found_mode = None, None
    for label in port_labels:
        if label in opmodes:
            found_port, found_mode = label, opmodes[label]
            break

    if not found_port:
        return {'rule': 'PORT_CONV', 'node': node_id, 'status': 'SKIPPED',
                'pre_board': pre_model, 'generation': gen,
                'port': '-', 'pre_speed': 'NOT FOUND', 'edp_port_size': siad_size or '-',
                'note': f'No {"/".join(port_labels)} EthernetPort found in Pre kget-all.'}

    is_1g = '1G' in found_mode.upper()
    edp_wants_10g = '10G' in siad_size.upper()
    pending = is_1g and edp_wants_10g

    if pending:
        note = f'Conversion pending — Pre {found_port} is {found_mode}, EDP calls for {siad_size}.'
    elif not is_1g:
        note = f'Already {found_mode} — no conversion needed.'
    else:
        note = f'Pre {found_port} is {found_mode} but EDP SIAD_PORT_SIZE_BBU is {siad_size or "not set"} — no conversion called for.'

    return {'rule': 'PORT_CONV', 'node': node_id,
            'status': 'MISMATCH' if pending else 'MATCH',
            'pre_board': pre_model, 'generation': gen, 'port': found_port,
            'pre_speed': found_mode, 'edp_port_size': siad_size or '-',
            'pending': pending, 'note': note}
