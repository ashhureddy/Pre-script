"""
Sector-level checks - Rules #4, #7/#8, #9, #10, #11/#26/#27, #19, #29 from the
Pre checks validation blueprint. Rules #6, #18, #25 need RFDS (Non RF
Inventory / RF Inventory data) which isn't wired in yet - they're stubbed
here with the same optional-parameter pattern used in checks_node.py.

Each check function returns a list of result dicts (one row per affected
cell/sector), same status vocabulary as checks_node.py: MATCH / MISMATCH /
INFO / SKIPPED.
"""
import re

import pre_extract as pe
import ciq_edp_reader as cer
from band_labels import band_label, is_5g_cell, is_mmwave_cell, is_cband_cell, is_dod_cell


def _rows(ciq_wb, sheet_name):
    return cer.sheet_rows_as_dicts(ciq_wb[sheet_name]) if sheet_name in ciq_wb.sheetnames else []


def check_radio_type(node_id, log_text, ciq_wb, rfds_pages, e_name, g_name):
    """Blueprint section 13 'Radio Type verification' (#6). Cells | Pre |
    CIQ | RFDS | Match. Pre is best-effort via the Cell->SectorCarrier->SEF
    chain (pre_extract.extract_cell_to_sef) - this resolves to a SEF number,
    not a specific RRU product name (no confirmed SEF->RRU link exists in
    Pre kget-all data), so Pre shows 'NOT AVAILABLE' rather than a guess."""
    results = []
    cell_details = {}
    if rfds_pages is not None:
        import rfds_extract as rf
        cell_details = rf.extract_cell_details(rfds_pages, final=True)
    cell_to_sef = pe.extract_cell_to_sef(log_text) if log_text else {}

    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if not (cell and e_name and str(cell).startswith(e_name)):
            continue
        ciq_rru = str(row.get('RRU type', '')).strip()
        rfds_rrh = cell_details.get(cell, {}).get('rrh_and_secpos', 'NOT FOUND' if rfds_pages is not None else 'NOT CHECKED')
        pre_val = 'NOT AVAILABLE' if not cell_to_sef.get(cell) else f"(SEF {cell_to_sef[cell]})"
        rru_token = ciq_rru.split()[-1] if ciq_rru else ''
        match = bool(rru_token) and rru_token in rfds_rrh
        results.append({'rule': '#6', 'node': node_id, 'cell': cell, 'pre': pre_val,
                         'ciq': ciq_rru, 'rfds': rfds_rrh, 'status': 'MATCH' if match else 'MISMATCH',
                         'note': 'Confirmed.' if match else 'Radio swap pending / RFDS does not confirm CIQ RRU type.'})

    for row in _rows(ciq_wb, '5G Info'):
        cell = row.get('NRCellDU')
        if not (cell and g_name and str(cell).startswith(g_name)):
            continue
        ciq_rru = str(row.get('RRU Type', '')).strip()
        rfds_rrh = cell_details.get(cell, {}).get('rrh_and_secpos', 'NOT FOUND' if rfds_pages is not None else 'NOT CHECKED')
        rru_token = ciq_rru.split()[-1] if ciq_rru else ''
        match = bool(rru_token) and rru_token in rfds_rrh
        results.append({'rule': '#6', 'node': node_id, 'cell': cell, 'pre': 'NA',
                         'ciq': ciq_rru, 'rfds': rfds_rrh, 'status': 'MATCH' if match else 'MISMATCH',
                         'note': 'Confirmed.' if match else 'RFDS does not confirm CIQ RRU type.'})
    return results


def check_radio_sharing_pairs(node_id, ciq_wb):
    """Blueprint section 13, rule #28 - flags cell pairs sharing the same
    physical radio (same SectorEquipmentFunction / RRU FieldReplaceableUnit)
    for a plain informational note, e.g. 'FCL05583_2A_1/FCL05583_2A_2 radios
    are shared'."""
    fiveg_rows = _rows(ciq_wb, '5G Info')
    by_fru = {}
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        fru = row.get('RRU FieldReplaceableUnit')
        if cell and fru:
            by_fru.setdefault(str(fru).strip(), []).append(cell)
    results = []
    seen = set()
    for fru, cells in by_fru.items():
        if len(cells) < 2:
            continue
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                pair = tuple(sorted((cells[i], cells[j])))
                if pair in seen:
                    continue
                seen.add(pair)
                results.append({'rule': '#28', 'node': node_id, 'cell': '/'.join(pair),
                                 'status': 'INFO', 'note': 'radios are shared'})
    return results


def check_sector_swap_config(node_id, log_text, ciq_wb, e_name):
    """Blueprint section 13, rules #21/#22/#32 - sector ID rename, TX/RX
    antenna count, and configured power, Pre vs CIQ (per cell). 'Link'
    (DATA1/DATA2 port designation) has no confirmed CIQ column and is
    NOT AVAILABLE rather than guessed."""
    if not log_text:
        return []
    import log_parser as lp
    parsed = lp.parse_log(log_text)
    block = lp.get_command_block(log_text, 'Sectorstate|configuredMaxTxPower')
    pre_by_sef = {}
    if block:
        for m in re.finditer(r'^(SectorEquipmentFunction=\S+)\s+.*?(?:UNLOCKED|LOCKED)\s+(\S+)\s+(\S+)\s+.*?(\d+)\s+(\d+)\s+\d+\s+\d+\s+(?:ENABLED|DISABLED)',
                              block, re.M):
            pre_by_sef[m.group(1)] = {'tx': m.group(4), 'rx': m.group(5)}
    cell_to_sef = pe.extract_cell_to_sef(log_text)

    results = []
    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if not (cell and e_name and str(cell).startswith(e_name)):
            continue
        sef = cell_to_sef.get(cell)
        pre_txrx = 'NOT AVAILABLE'
        if sef and sef in pre_by_sef:
            pre_txrx = f"{pre_by_sef[sef]['tx']}x{pre_by_sef[sef]['rx']}"
        ciq_tx, ciq_rx = row.get('noOfTxAntennas'), row.get('noOfRxAntennas')
        ciq_txrx = f"{ciq_tx}x{ciq_rx}" if ciq_tx and ciq_rx else 'NOT FOUND'
        ciq_power = str(row.get('configuredOutputPower', '')).strip()
        sec_id = str(row.get('sectorId', '')).strip()
        results.append({'rule': '#21/#22/#32', 'node': node_id, 'cell': cell,
                         'sec_id': sec_id, 'pre_txrx': pre_txrx, 'ciq_txrx': ciq_txrx,
                         'ciq_power': ciq_power, 'status': 'INFO',
                         'note': 'Pre TX/RX/power not fully resolvable without RFDS radio-swap confirmation - review manually.'})
    return results


def check_nbiot(node_id, log_text, ciq_wb=None):
    """Blueprint section 16 'NBIOT Cells check' (#4). nbIotCellName |
    NBIoT Cell ID [pre] | NBIoT Cell ID [post/CIQ] | Match. Matched by
    physicalLayerCellId, which both sides expose. Only triggers if NBIoT
    cells are present (per confirmed condition)."""
    pre_cells = pe.extract_nbiot_cells(__import__('log_parser').parse_log(log_text)) if log_text else []
    ciq_rows = _rows(ciq_wb, 'NBIoT Parameters') if ciq_wb is not None else []
    if not pre_cells and not ciq_rows:
        return [{'rule': '#4', 'node': node_id, 'cell': None, 'status': 'SKIPPED',
                  'note': 'No NBIoT cells present on this node - check does not trigger.'}]

    pre_by_pcid = {c.get('physicalLayerCellId'): c for c in pre_cells}
    results = []
    matched_pre = set()
    for row in ciq_rows:
        name = row.get('nbIotCellName')
        ciq_id = row.get('nbIotCellId')
        pcid = row.get('physicalLayerCellId')
        pre_c = pre_by_pcid.get(str(pcid)) if pcid is not None else None
        pre_id = pre_c.get('cellid') if pre_c else 'NA'
        if pre_c:
            matched_pre.add(pcid)
        match = pre_id in ('NA', None) or str(pre_id) == str(ciq_id)
        results.append({'rule': '#4', 'node': node_id, 'cell': name,
                         'pre_id': pre_id, 'ciq_id': ciq_id,
                         'status': 'MATCH' if match else 'MISMATCH',
                         'note': 'Confirmed.' if match else f'Pre={pre_id} vs CIQ={ciq_id}.'})
    # Any Pre NBIoT cell with no CIQ counterpart at all
    for c in pre_cells:
        if c.get('physicalLayerCellId') not in matched_pre:
            results.append({'rule': '#4', 'node': node_id, 'cell': c.get('cell'),
                             'pre_id': c.get('cellid'), 'ciq_id': 'NOT FOUND',
                             'status': 'MISMATCH', 'note': 'Pre NBIoT cell not found in CIQ.'})
    return results


def check_nr_tac(node_id, log_text, ciq_wb, has_pre_log, has_amf):
    """Rule #7/#8 - NR TAC. Pre kget-all vs CIQ 5G Info nRTAC, per 5G cell.
    #8's NSA(0)/SA(7-digit) branch: cells present in NR_SA tab are expected
    7-digit (SA); others expected '0' (NSA). #7's warning: if AMF is present
    (has_amf, from 'st amf' in the Pre kget-all - caller determines this) and
    nRTAC is 7-digit in Pre, warn of SA configuration on this node."""
    fiveg_rows = _rows(ciq_wb, '5G Info')
    nr_sa_rows = _rows(ciq_wb, 'NR_SA')
    sa_nodes = {str(r.get('Node Name', '')).strip().upper() for r in nr_sa_rows if r.get('Node Name')}

    pre_nr_tac = pe.extract_nr_tac(log_text) if (has_pre_log and log_text) else {}

    results = []
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        if not cell:
            continue
        ciq_nrtac = str(row.get('nRTAC', '')).strip()
        gnb_name = str(row.get('gNB Name', '')).strip().upper()
        expects_sa = gnb_name in sa_nodes
        notes = []
        status = 'MATCH'

        if expects_sa and not (ciq_nrtac.isdigit() and len(ciq_nrtac) == 7):
            status = 'MISMATCH'
            notes.append(f"Node in NR_SA (SA conversion) but nRTAC='{ciq_nrtac}' is not 7-digit.")
        elif not expects_sa and ciq_nrtac not in ('0', ''):
            status = 'MISMATCH'
            notes.append(f"Not an NR_SA node (expect NSA, nRTAC=0) but nRTAC='{ciq_nrtac}'.")

        pre_val = pre_nr_tac.get(cell)
        if pre_val is not None and pre_val != ciq_nrtac:
            status = 'MISMATCH'
            notes.append(f'Pre nRTAC={pre_val} vs CIQ nRTAC={ciq_nrtac}.')
        if has_amf and pre_val and pre_val.isdigit() and len(pre_val) == 7:
            notes.append(f'WARNING: SA configuration on : {node_id}')

        results.append({'rule': '#7/#8', 'node': node_id, 'cell': cell, 'status': status,
                         'pre_nrtac': pre_val, 'ciq_nrtac': ciq_nrtac, 'expects_sa': expects_sa,
                         'note': ' '.join(notes) if notes else 'NR TAC confirmed.'})
    return results


def check_mmwave_rach(node_id, ciq_wb):
    """Rule #10 - mmWave (N260) rachRootSequence must not exceed 137.
    CIQ-only check, no Pre comparison (per blueprint)."""
    fiveg_rows = _rows(ciq_wb, '5G Info')
    results = []
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        if not cell or not is_mmwave_cell(cell):
            continue
        rach = row.get('rachRootSequence')
        try:
            rach_val = int(str(rach).strip())
        except (TypeError, ValueError):
            results.append({'rule': '#10', 'node': node_id, 'cell': cell, 'status': 'MISMATCH',
                             'rachRootSequence': rach, 'note': 'rachRootSequence not numeric.'})
            continue
        exceeded = rach_val > 137
        results.append({'rule': '#10', 'node': node_id, 'cell': cell,
                         'status': 'MISMATCH' if exceeded else 'MATCH',
                         'rachRootSequence': rach_val,
                         'note': f'rachRootSequence exceeded 137 for the MMWave sector : {cell}' if exceeded else 'Within limit.'})
    return results


def check_sef_fru(node_id, ciq_wb):
    """Rule #9 - SEF/FRU sharing vs uniqueness, radio-type dependent:
      - 6472 present + both CBAND and DOD (or DOD_BWE) cells -> SEF/FRU
        SHARING is expected/correct (sharing radio by default).
      - 6419/6449 (single-band radios) -> SEF/FRU must be UNIQUE per
        CBAND|DOD cell.
      - 8863/4461/4467 -> RRU FieldReplaceableUnit must start with 'RRU'."""
    fiveg_rows = _rows(ciq_wb, '5G Info')
    cband_dod_rows = [r for r in fiveg_rows if r.get('NRCellDU') and
                       (is_cband_cell(r['NRCellDU']) or is_dod_cell(r['NRCellDU']))]

    results = []
    # group by RRU Type to determine which radio family governs each cell
    for row in cband_dod_rows:
        cell = row.get('NRCellDU')
        rru_type = str(row.get('RRU Type', '')).strip()
        sef = row.get('SectorEquipmentFunction')
        fru = row.get('RRU FieldReplaceableUnit')

        if '6472' in rru_type or 'AIR' in rru_type.upper():
            # sharing expected - just record for cross-cell dup check below, not an error by itself
            results.append({'rule': '#9', 'node': node_id, 'cell': cell, 'status': 'INFO',
                             'rru_type': rru_type, 'sef': sef, 'fru': fru,
                             'note': '6472/AIR radio - CBAND/DOD/DOD_BWE sharing this radio is expected.'})
        elif any(m in rru_type for m in ('6419', '6449')):
            dup = any(other is not row and other.get('SectorEquipmentFunction') == sef
                      for other in cband_dod_rows if is_cband_cell(other.get('NRCellDU')) or is_dod_cell(other.get('NRCellDU')))
            results.append({'rule': '#9', 'node': node_id, 'cell': cell,
                             'status': 'MISMATCH' if dup else 'MATCH',
                             'rru_type': rru_type, 'sef': sef, 'fru': fru,
                             'note': 'SEF/FRU must be unique for single-band radio but is shared.' if dup else 'Unique, as required.'})
        elif any(m in rru_type for m in ('8863', '4461', '4467')):
            ok = str(fru or '').strip().upper().startswith('RRU')
            results.append({'rule': '#9', 'node': node_id, 'cell': cell,
                             'status': 'MATCH' if ok else 'MISMATCH',
                             'rru_type': rru_type, 'sef': sef, 'fru': fru,
                             'note': 'OK.' if ok else f"RRU FieldReplaceableUnit '{fru}' does not start with 'RRU'."})
    return results


def check_port_uniqueness(node_id, ciq_wb):
    """Rule #11/#26/#27 - RI Port uniqueness within same BBU/XMU + colocated
    group (5G Info 'BB/XMU' + 'Port 1'-'Port 4'), plus XMU port exclusivity
    (if XMU is present, its ports must not be reused by any other sector on
    that node - per confirmed addition).

    Exemption: cells sharing a 6472/AIR radio (per rule #9's confirmed
    sharing-is-expected logic) legitimately share the same physical ports too
    - e.g. a CBAND+DOD pair on one 6472 radio. Without this exemption, every
    such pair would false-positive here even though check_sef_fru() already
    confirms the sharing is correct. Cells are grouped into a shared-radio set
    by (SectorEquipmentFunction, RRU Type) when RRU Type contains 6472/AIR."""
    fiveg_rows = _rows(ciq_wb, '5G Info')
    port_cols = ['Port 1', 'Port 2', 'Port 3', 'Port 4']

    shared_radio_group = {}  # cell -> group key, for 6472/AIR-sharing cells only
    for row in fiveg_rows:
        rru_type = str(row.get('RRU Type', '')).strip()
        if '6472' in rru_type or 'AIR' in rru_type.upper():
            cell = row.get('NRCellDU')
            sef = row.get('SectorEquipmentFunction')
            if cell and sef:
                shared_radio_group[cell] = sef

    usage = {}
    xmu_ports = set()
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        bbu = str(row.get('BB/XMU', '')).strip()
        is_xmu = 'XMU' in bbu.upper()
        for pc in port_cols:
            val = row.get(pc)
            if val is None or str(val).strip() == '':
                continue
            key = (bbu, str(val).strip())
            usage.setdefault(key, []).append(cell)
            if is_xmu:
                xmu_ports.add(str(val).strip())

    results = []
    for (bbu, port), cells in usage.items():
        # if every cell sharing this port belongs to the same 6472/AIR shared-radio
        # group, the sharing is expected (rule #9) - not a violation.
        groups = {shared_radio_group.get(c) for c in cells}
        exempt = len(cells) > 1 and len(groups) == 1 and None not in groups
        if len(cells) > 1 and not exempt:
            for cell in cells:
                results.append({'rule': '#11/#26/#27', 'node': node_id, 'cell': cell, 'status': 'MISMATCH',
                                 'bbu': bbu, 'port': port,
                                 'note': f"Port {port} on {bbu} shared by multiple sectors: {cells}"})
        else:
            note = 'Port shared as expected (6472/AIR sharing radio, per rule #9).' if exempt else 'Port unique.'
            for cell in cells:
                results.append({'rule': '#11/#26/#27', 'node': node_id, 'cell': cell, 'status': 'MATCH',
                                 'bbu': bbu, 'port': port, 'note': note})

    # XMU port exclusivity: any non-XMU row using a port also used by an XMU row -> violation
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        bbu = str(row.get('BB/XMU', '')).strip()
        if 'XMU' in bbu.upper():
            continue
        for pc in port_cols:
            val = row.get(pc)
            if val is not None and str(val).strip() in xmu_ports:
                results.append({'rule': '#11/#26/#27', 'node': node_id, 'cell': cell, 'status': 'MISMATCH',
                                 'bbu': bbu, 'port': val,
                                 'note': f"Port {val} is assigned to an XMU elsewhere on this node but reused here."})
    return results


def check_rf_params(node_id, log_text, ciq_wb, has_pre_log, retuned_cells=None):
    """Rule #19 - LTE/5G RF params, Pre vs CIQ. One row per (cell, field) so
    each parameter renders as its own comparable table row: cell | field |
    Pre value | CIQ value | Match.

    retuned_cells: optional set of cell names the SOW analysis already
    identified as deliberately retuned (from Sector Del_Movement). Per
    confirmed direction these stay HIGHLIGHTED (red) even though the change
    is expected - a frequency change is always worth an engineer's eyes -
    but the note names it as a planned retune so it reads as "verify this"
    rather than "something is broken". Differences on cells NOT in that set
    carry no SOW explanation at all and are genuine unexplained faults."""
    import log_parser as lp
    retuned_cells = retuned_cells or set()
    results = []

    def _status_for(cell, match):
        # Both planned retunes and unexplained differences highlight red -
        # the note distinguishes them.
        return 'MATCH' if match else 'MISMATCH'

    def _note_for(cell, match):
        if match:
            return 'Confirmed.'
        if cell in retuned_cells:
            return 'Planned retune (see Scope of Work) — verify values.'
        return 'No SOW context available - if unexpected, check CIQ Revision History tab.'

    def _is_planned(cell):
        return cell in retuned_cells

    eutran_rows = _rows(ciq_wb, 'eUtran Parameters')
    pre_lte = pe.extract_lte_sector_params(log_text) if (has_pre_log and log_text) else {}
    for row in eutran_rows:
        cell = row.get('EutranCellFDDId')
        if not cell:
            continue
        pre_vals = pre_lte.get(cell)
        if not pre_vals:
            continue
        for field, pre_key in (('earfcnDl', 'earfcndl'), ('earfcnUl', 'earfcnul'), ('dlChannelBandwidth', 'dlChannelBandwidth')):
            ciq_v = str(row.get(field, '')).strip()
            pre_v = pre_vals.get(pre_key)
            if not (pre_v and ciq_v):
                continue
            match = pre_v == ciq_v
            results.append({'rule': '#19', 'node': node_id, 'cell': cell, 'field': field,
                             'pre_value': pre_v, 'ciq_value': ciq_v,
                             'status': _status_for(cell, match), 'planned': _is_planned(cell) and not match,
                             'note': _note_for(cell, match)})

    fiveg_rows = _rows(ciq_wb, '5G Info')
    parsed = lp.parse_log(log_text) if (has_pre_log and log_text) else []
    pre_5g = pe.extract_5g_sector_params(parsed, log_text) if (has_pre_log and log_text) else {}
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        if not cell:
            continue
        pre_vals = pre_5g.get(cell)
        if not pre_vals:
            continue
        for field in ('arfcnDL', 'arfcnUL', 'bSChannelBwDL', 'bSChannelBwUL'):
            ciq_v = str(row.get(field, '')).strip()
            pre_v = pre_vals.get(field)
            if not (pre_v and ciq_v):
                continue
            match = pre_v == ciq_v
            results.append({'rule': '#19', 'node': node_id, 'cell': cell, 'field': field,
                             'pre_value': pre_v, 'ciq_value': ciq_v,
                             'status': _status_for(cell, match), 'planned': _is_planned(cell) and not match,
                             'note': _note_for(cell, match)})
        ciq_ssb = str(row.get('ssbFrequency', '')).strip()
        pre_ssb = pre_vals.get('ssbFrequency')
        if pre_ssb and ciq_ssb:
            match = pre_ssb == ciq_ssb
            results.append({'rule': '#19', 'node': node_id, 'cell': cell, 'field': 'ssbFrequency',
                             'pre_value': pre_ssb, 'ciq_value': ciq_ssb,
                             'status': _status_for(cell, match), 'planned': _is_planned(cell) and not match,
                             'note': _note_for(cell, match)})
    return results


def check_rfds_cell_presence(node_id, ciq_wb, rfds_pages):
    """Rule #18 - EutranCellFDDId presence match: CIQ cell exists in RFDS's
    Non RF Inventory page (per confirmed decision - presence only, no
    beamDirection/azimuth needed)."""
    if rfds_pages is None:
        return [{'rule': '#18', 'node': node_id, 'cell': None, 'status': 'SKIPPED',
                  'note': 'No RFDS provided.'}]
    import rfds_extract as rf
    rfds_cells = rf.extract_non_rf_inventory_cells(rfds_pages, final=True)
    mm_row = None
    for r in _rows(ciq_wb, 'Mixed Mode Info'):
        if str(r.get('Node to be built as', '')).strip().upper() == str(node_id).strip().upper():
            mm_row = r
            break
    e_name = str(mm_row.get('eNodeB Name', '')).strip() if mm_row else node_id

    results = []
    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if not cell or not str(cell).startswith(e_name):
            continue
        present = cell in rfds_cells
        results.append({'rule': '#18', 'node': node_id, 'cell': cell,
                         'status': 'MATCH' if present else 'MISMATCH',
                         'note': 'Present in RFDS.' if present else 'Cell not found in RFDS Non RF Inventory.'})
    return results


def check_rfds_cell_id(node_id, ciq_wb, rfds_pages):
    """Rule #25 - cellId (LTE eUtran Parameters / 5G's cellLocalId - see
    check_rfds_nrcelldu for the 5G side) must match RFDS 'Cell Details
    (Final)' RCN column."""
    if rfds_pages is None:
        return [{'rule': '#25', 'node': node_id, 'cell': None, 'status': 'SKIPPED',
                  'note': 'No RFDS provided.'}]
    import rfds_extract as rf
    cell_details = rf.extract_cell_details(rfds_pages, final=True)

    results = []
    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if not cell or cell not in cell_details:
            continue
        ciq_cell_id = str(row.get('cellId', '')).strip()
        rfds_rcn = cell_details[cell]['rcn']
        match = ciq_cell_id == rfds_rcn
        results.append({'rule': '#25', 'node': node_id, 'cell': cell,
                         'status': 'MATCH' if match else 'MISMATCH',
                         'ciq_cellId': ciq_cell_id, 'rfds_rcn': rfds_rcn,
                         'note': 'Confirmed.' if match else f'CIQ cellId={ciq_cell_id} vs RFDS RCN={rfds_rcn}.'})
    return results


def check_rfds_nrcelldu(node_id, ciq_wb, rfds_pages):
    """Rule #6 - NRCellDU/cellLocalId/RRU Type vs RFDS: cellLocalId vs RFDS
    RCN (5G side of rule #25's logic) + RRU Type vs RFDS RRH text
    (substring match, since RFDS's RRH field isn't cleanly split from
    Sector-Position - see rfds_extract.extract_cell_details's docstring)."""
    if rfds_pages is None:
        return [{'rule': '#6', 'node': node_id, 'cell': None, 'status': 'SKIPPED',
                  'note': 'No RFDS provided.'}]
    import rfds_extract as rf
    cell_details = rf.extract_cell_details(rfds_pages, final=True)

    results = []
    for row in _rows(ciq_wb, '5G Info'):
        cell = row.get('NRCellDU')
        if not cell or cell not in cell_details:
            continue
        ciq_local_id = str(row.get('cellLocalId', '')).strip()
        rfds_rcn = cell_details[cell]['rcn']
        ciq_rru = str(row.get('RRU Type', '')).strip()
        rfds_rrh = cell_details[cell]['rrh_and_secpos']

        mismatches = []
        if ciq_local_id != rfds_rcn:
            mismatches.append(f'cellLocalId={ciq_local_id} vs RFDS RCN={rfds_rcn}')
        # RRU Type model number (last-ish token convention, e.g. '4449') should
        # appear somewhere in RFDS's combined RRH text - substring check only,
        # per the field's known ambiguity.
        rru_token = ciq_rru.split()[-1] if ciq_rru else ''
        if rru_token and rru_token not in rfds_rrh:
            mismatches.append(f"RRU Type '{ciq_rru}' not found in RFDS RRH text '{rfds_rrh}'")

        results.append({'rule': '#6', 'node': node_id, 'cell': cell,
                         'ciq_values': f'{ciq_local_id} / {ciq_rru}',
                         'rfds_values': f'{rfds_rcn} / {rfds_rrh}',
                         'status': 'MISMATCH' if mismatches else 'MATCH',
                         'note': '; '.join(mismatches) if mismatches else 'Confirmed.'})
    return results


def check_cells_vs_rfds(node_id, ciq_wb, rfds_pages, e_name, g_name):
    """Blueprint section 8 'Cells verification' (#6, #18) - every CIQ cell
    (LTE + 5G combined) checked for presence in RFDS. CIQ | RFDS | Match."""
    if rfds_pages is None:
        return [{'rule': '#6/#18', 'node': node_id, 'cell': None, 'status': 'SKIPPED', 'note': 'No RFDS provided.'}]
    import rfds_extract as rf
    rfds_cells = rf.extract_non_rf_inventory_cells(rfds_pages, final=True)
    results = []
    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if cell and e_name and str(cell).startswith(e_name):
            present = cell in rfds_cells
            results.append({'rule': '#6/#18', 'node': node_id, 'cell': cell,
                             'ciq_cell': cell, 'rfds_cell': cell if present else 'NOT FOUND',
                             'status': 'MATCH' if present else 'MISMATCH',
                             'note': 'Match.' if present else 'Not found in RFDS.'})
    for row in _rows(ciq_wb, '5G Info'):
        cell = row.get('NRCellDU')
        if cell and g_name and str(cell).startswith(g_name):
            present = cell in rfds_cells
            results.append({'rule': '#6/#18', 'node': node_id, 'cell': cell,
                             'ciq_cell': cell, 'rfds_cell': cell if present else 'NOT FOUND',
                             'status': 'MATCH' if present else 'MISMATCH',
                             'note': 'Match.' if present else 'Not found in RFDS.'})
    return results


def check_cell_id_vs_rfds(node_id, log_text, ciq_wb, rfds_pages, e_name, g_name):
    """Blueprint section 9 'Cell ID verification' (#6, #24) - Cells | Pre |
    CIQ [cellId/cellLocalId] | RFDS [RCN] | Match, LTE + 5G combined. If a
    cell is newly adding, Pre shows 'NA' per the blueprint's own example."""
    results = []
    cell_details = {}
    if rfds_pages is not None:
        import rfds_extract as rf
        cell_details = rf.extract_cell_details(rfds_pages, final=True)

    pre_lte = pe.extract_lte_sector_params(log_text) if log_text else {}
    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if not (cell and e_name and str(cell).startswith(e_name)):
            continue
        ciq_id = str(row.get('cellId', '')).strip()
        pre_id = (pre_lte.get(cell) or {}).get('cellId') or 'NA'
        rfds_rcn = cell_details.get(cell, {}).get('rcn', 'NOT FOUND' if rfds_pages is not None else 'NOT CHECKED')
        match = (ciq_id == rfds_rcn) and (pre_id in ('NA',) or pre_id == ciq_id)
        results.append({'rule': '#6/#24', 'node': node_id, 'cell': cell,
                         'pre': pre_id, 'ciq': ciq_id, 'rfds_rcn': rfds_rcn,
                         'status': 'MATCH' if match else 'MISMATCH',
                         'note': 'Match.' if match else f'Pre={pre_id}, CIQ={ciq_id}, RFDS={rfds_rcn}.'})

    parsed = __import__('log_parser').parse_log(log_text) if log_text else []
    pre_5g = pe.extract_5g_sector_params(parsed, log_text) if log_text else {}
    for row in _rows(ciq_wb, '5G Info'):
        cell = row.get('NRCellDU')
        if not (cell and g_name and str(cell).startswith(g_name)):
            continue
        ciq_id = str(row.get('cellLocalId', '')).strip()
        pre_id = (pre_5g.get(cell) or {}).get('cellLocalId') or 'NA'
        rfds_rcn = cell_details.get(cell, {}).get('rcn', 'NOT FOUND' if rfds_pages is not None else 'NOT CHECKED')
        match = (ciq_id == rfds_rcn) and (pre_id in ('NA',) or pre_id == ciq_id)
        results.append({'rule': '#6/#24', 'node': node_id, 'cell': cell,
                         'pre': pre_id, 'ciq': ciq_id, 'rfds_rcn': rfds_rcn,
                         'status': 'MATCH' if match else 'MISMATCH',
                         'note': 'Match.' if match else f'Pre={pre_id}, CIQ={ciq_id}, RFDS={rfds_rcn}.'})
    return results


def check_rf_params_4g(node_id, log_text, ciq_wb, has_pre_log, retuned_cells=None):
    """Blueprint section 10 'Parameters Verification - 4G' (#19). One row
    per cell, each field shown as a single 'Pre | CIQ' string per the
    confirmed compact format. Retuned cells (from SOW) still highlight red -
    the note marks them as planned so the engineer knows why, but doesn't
    downgrade the color (per confirmed decision)."""
    retuned_cells = retuned_cells or set()
    pre_lte = pe.extract_lte_sector_params(log_text) if (has_pre_log and log_text) else {}
    pre_ul = pe.extract_ul_channel_bandwidth(log_text) if (has_pre_log and log_text) else {}
    results = []
    for row in _rows(ciq_wb, 'eUtran Parameters'):
        cell = row.get('EutranCellFDDId')
        if not cell:
            continue
        pre_vals = pre_lte.get(cell)
        if not pre_vals:
            continue
        fields = {}
        mismatched = []
        for out_key, ciq_col, pre_key in (('earfcndl', 'earfcnDl', 'earfcndl'), ('earfcnul', 'earfcnUl', 'earfcnul'),
                                            ('dlChannelBandwidth', 'dlChannelBandwidth', 'dlChannelBandwidth')):
            ciq_v = str(row.get(ciq_col, '')).strip()
            pre_v = pre_vals.get(pre_key) or 'NA'
            fields[out_key] = f'{pre_v} | {ciq_v}'
            if pre_v != 'NA' and pre_v != ciq_v:
                mismatched.append(out_key)
        ciq_ul = str(row.get('ulChannelBandwidth', '')).strip()
        pre_ul_v = pre_ul.get(cell) or 'NA'
        fields['ulChannelBandwidth'] = f'{pre_ul_v} | {ciq_ul}'
        if pre_ul_v != 'NA' and pre_ul_v != ciq_ul:
            mismatched.append('ulChannelBandwidth')

        planned = bool(mismatched) and cell in retuned_cells
        status = 'MATCH' if not mismatched else 'MISMATCH'
        note = 'Confirmed.' if not mismatched else \
            (f"Planned retune ({', '.join(mismatched)}) - verify." if planned else
             f"Mismatch on {', '.join(mismatched)} - no SOW context, check Revision History.")
        results.append({'rule': '#19', 'node': node_id, 'cell': cell, 'status': status, 'note': note, **fields})
    return results


def check_rf_params_5g(node_id, parsed, log_text, ciq_wb, has_pre_log, retuned_cells=None):
    """Blueprint section 11 'Parameters Verification - 5G' (#19). Same
    compact 'Pre | CIQ' format as the 4G table."""
    retuned_cells = retuned_cells or set()
    pre_5g = pe.extract_5g_sector_params(parsed, log_text) if (has_pre_log and log_text) else {}
    results = []
    for row in _rows(ciq_wb, '5G Info'):
        cell = row.get('NRCellDU')
        if not cell:
            continue
        pre_vals = pre_5g.get(cell)
        if not pre_vals:
            continue
        fields = {}
        mismatched = []
        for field in ('arfcnDL', 'arfcnUL', 'bSChannelBwDL', 'bSChannelBwUL'):
            ciq_v = str(row.get(field, '')).strip()
            pre_v = pre_vals.get(field) or 'NA'
            fields[field] = f'{pre_v} | {ciq_v}'
            if pre_v != 'NA' and pre_v != ciq_v:
                mismatched.append(field)
        ciq_ssb = str(row.get('ssbFrequency', '')).strip()
        pre_ssb = pre_vals.get('ssbFrequency') or 'NA'
        fields['ssbfrequency'] = f'{pre_ssb} | {ciq_ssb}'
        if pre_ssb != 'NA' and pre_ssb != ciq_ssb:
            mismatched.append('ssbfrequency')

        planned = bool(mismatched) and cell in retuned_cells
        status = 'MATCH' if not mismatched else 'MISMATCH'
        note = 'Confirmed.' if not mismatched else \
            (f"Planned retune ({', '.join(mismatched)}) - verify." if planned else
             f"Mismatch on {', '.join(mismatched)} - no SOW context, check Revision History.")
        results.append({'rule': '#19', 'node': node_id, 'cell': cell, 'status': status, 'note': note, **fields})
    return results


def check_xmu_port_overlap(node_id, enb_row, gnb_row, ciq_wb):
    """Blueprint section 14 second table: Node id | 1st DU type | 1st XMU |
    1st XMU Port 1/2/3 | Port Uniqueness - do the XMU's own designated ports
    get reused by any other sector's RI port on this node?"""
    row = enb_row or gnb_row
    if row is None or str(row.get('1st XMU', '')).strip().upper() != 'YES':
        return []
    du_type = str(row.get('DU type') or row.get('1st DU type') or '').strip()
    xmu_ports = {str(row.get(f'1st XMU Port {i}', '')).strip() for i in (1, 2, 3)}
    xmu_ports.discard('')
    xmu_ports.discard('NOT USED')
    xmu_ports = {p for p in xmu_ports if p.upper() not in ('NOT USED', 'N/A', 'NA')}

    used_elsewhere = set()
    for row5g in _rows(ciq_wb, '5G Info'):
        bbu = str(row5g.get('BB/XMU', '')).strip()
        if 'XMU' in bbu.upper():
            continue
        for pc in ('Port 1', 'Port 2', 'Port 3', 'Port 4'):
            v = row5g.get(pc)
            if v is not None and str(v).strip() in xmu_ports:
                used_elsewhere.add(str(v).strip())

    unique = not used_elsewhere
    return [{'rule': '#11/#25', 'node': node_id, 'cell': node_id,
              'du_type': du_type, 'xmu': 'Yes', 'xmu_ports': ', '.join(sorted(xmu_ports)) or 'NOT USED',
              'status': 'MATCH' if unique else 'MISMATCH',
              'note': 'Unique.' if unique else f'XMU ports reused elsewhere: {sorted(used_elsewhere)}'}]


def check_pci_uniqueness(node_id, ciq_wb, e_name=None):
    """Rule #23 - PCI uniqueness within same band. PCI = PhysicalLayerCellIdGroup*3
    + physicalLayerSubCellId (verified against CIQ's own 'PCI' column); flags
    any two same-band-labeled sectors on this node sharing a PCI value.
    e_name: eNodeB Name prefix for this node's cells (defaults to node_id)."""
    prefix = e_name or node_id
    eutran_rows = _rows(ciq_wb, 'eUtran Parameters')
    results = []
    by_band = {}
    for row in eutran_rows:
        cell = row.get('EutranCellFDDId')
        if not cell or not str(cell).startswith(prefix):
            continue
        try:
            group = int(str(row.get('PhysicalLayerCellIdGroup', '')).strip())
            sub = int(str(row.get('physicalLayerSubCellId', '')).strip())
        except (TypeError, ValueError):
            continue
        pci = group * 3 + sub
        ciq_pci = str(row.get('PCI', '')).strip()
        label, _ = band_label(cell)
        by_band.setdefault(label, []).append({'cell': cell, 'group': group, 'sub': sub, 'pci': pci, 'ciq_pci': ciq_pci})

    for label, cells in by_band.items():
        pci_counts = {}
        for c in cells:
            pci_counts.setdefault(c['pci'], []).append(c['cell'])
        for c in cells:
            dup = len(pci_counts[c['pci']]) > 1
            mismatch_calc = str(c['pci']) != c['ciq_pci']
            status = 'MISMATCH' if (dup or mismatch_calc) else 'MATCH'
            notes = []
            if dup:
                notes.append(f"PCI {c['pci']} shared with {[x for x in pci_counts[c['pci']] if x != c['cell']]}")
            if mismatch_calc:
                notes.append(f"Computed PCI {c['pci']} != CIQ PCI {c['ciq_pci']}")
            results.append({'rule': '#23', 'node': node_id, 'cell': c['cell'],
                             'group': c['group'], 'sub': c['sub'], 'pci': c['pci'],
                             'status': status, 'note': '; '.join(notes) if notes else 'Unique.'})
    return results


def check_nr_pci_uniqueness(node_id, ciq_wb, g_name=None):
    """Rule #23 (5G side) - nRPCI uniqueness within same band on this node.
    g_name: gNodeB Name prefix for this node's 5G cells (skips if None -
    node has no 5G identity)."""
    if not g_name:
        return []
    fiveg_rows = _rows(ciq_wb, '5G Info')
    results = []
    by_band = {}
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        if not cell or not str(cell).startswith(g_name):
            continue
        nrpci = row.get('nRPCI')
        if nrpci is None or str(nrpci).strip() == '':
            continue
        label, _ = band_label(cell)
        by_band.setdefault(label, []).append({'cell': cell, 'nrpci': str(nrpci).strip()})

    for label, cells in by_band.items():
        counts = {}
        for c in cells:
            counts.setdefault(c['nrpci'], []).append(c['cell'])
        for c in cells:
            dup = len(counts[c['nrpci']]) > 1
            results.append({'rule': '#23', 'node': node_id, 'cell': c['cell'], 'nrpci': c['nrpci'],
                             'status': 'MISMATCH' if dup else 'MATCH',
                             'note': f"nRPCI {c['nrpci']} shared with {[x for x in counts[c['nrpci']] if x != c['cell']]}" if dup else 'Unique.'})
    return results


def check_antenna_uniqueness(node_id, ciq_wb):
    """Blueprint section 15 'Antenna Uniqueness' (#27, #34). Sharing sectors
    normally share the same AUG/AU/ASU; the 4890-vs-8843 exception requires
    them to DIFFER for PCS/AWS bands when both radio types are present in the
    shared group. CBAND/DOD/DOD_BWE cells are explicitly excluded per the
    blueprint comment ('For CBAND/DOD dont not trigger this check')."""
    antenna_rows = _rows(ciq_wb, 'Antenna Information')
    fiveg_rows = _rows(ciq_wb, '5G Info')
    eutran_rows = _rows(ciq_wb, 'eUtran Parameters')

    rru_by_cell = {}
    for row in eutran_rows:
        cell = row.get('EutranCellFDDId')
        if cell:
            rru_by_cell[cell] = str(row.get('RRU type', '')).strip()
    for row in fiveg_rows:
        cell = row.get('NRCellDU')
        if cell:
            rru_by_cell[cell] = str(row.get('RRU Type', '')).strip()

    colocation = {}
    for row in (fiveg_rows + eutran_rows):
        cell = row.get('NRCellDU') or row.get('EutranCellFDDId')
        colo = row.get('Co-Located Technology Cell')
        if not cell or is_cband_cell(cell) or is_dod_cell(cell):
            continue  # excluded per blueprint comment
        if cell and colo and str(colo).strip() and str(colo).strip().upper() != 'N/A':
            for other in str(colo).split(','):
                other = other.strip()
                if other and not (is_cband_cell(other) or is_dod_cell(other)):
                    colocation.setdefault(cell, set()).add(other)
                    colocation.setdefault(other, set()).add(cell)

    aug_by_cell = {r.get('EutranCellFDDId'): (r.get('AntennaUnitGroup'), r.get('AntennaUnit'), r.get('AntennaSubunit'))
                   for r in antenna_rows if r.get('EutranCellFDDId')}

    results = []
    checked_pairs = set()
    for cell, group in colocation.items():
        for other in group:
            pair = tuple(sorted((cell, other)))
            if pair in checked_pairs or cell == other:
                continue
            checked_pairs.add(pair)
            a1, a2 = aug_by_cell.get(cell), aug_by_cell.get(other)
            if not a1 or not a2:
                continue
            same = a1 == a2
            band1, _ = band_label(cell)
            band2, _ = band_label(other)
            fam1 = re.sub(r'_\d+$', '', band1) if band1 else band1
            fam2 = re.sub(r'_\d+$', '', band2) if band2 else band2
            aws_pcs_pair = (fam1 != fam2) and any(f and ('AWS' in f or 'PCS' in f) for f in (fam1, fam2))
            rrus = {rru_by_cell.get(cell, ''), rru_by_cell.get(other, '')}
            uses_4890_or_8843 = any('4890' in r or '8843' in r for r in rrus)
            # Exception (confirmed): whenever a pair spans DIFFERENT AWS/PCS
            # bands and involves EITHER a 4890 or 8843 radio on either cell,
            # the two sectors must use DIFFERENT antenna ports. Same-band
            # multi-carrier pairs (e.g. AWS_1/AWS_1 on 2A_1 vs 2A_2) are not
            # this case - they're expected to share, like everything else.
            exception_applies = aws_pcs_pair and uses_4890_or_8843

            if exception_applies:
                status = 'MATCH' if not same else 'MISMATCH'
                verdict = 'Unique - 4890/8843 Radio' if not same else 'Not Unique - 4890/8843 Radio'
            else:
                status = 'MATCH' if same else 'MISMATCH'
                verdict = 'shared' if same else 'Not shared'

            results.append({'rule': '#27/#34', 'node': node_id, 'cell': f'{cell} / {other}',
                             'status': status, 'verdict': verdict,
                             'aug_au_asu_1': a1, 'aug_au_asu_2': a2,
                             'note': verdict})
    return results
