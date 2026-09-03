"""
Node-level field extraction from a parsed Pre kget-all / hget log
(see log_parser.py for the underlying table parser).

Each function takes `parsed` = log_parser.parse_log(text) and returns either
a value, or None if the node's log doesn't contain that data (e.g. a newly
built node has no Pre kget-all at all, so callers should treat "no log
provided for this node" as a distinct case from "log present but field
missing").
"""
import re

import ciq_edp_reader as cer

from log_parser import find_command, all_rows, get_command_block

_SW_VERSION_RE = re.compile(
    r'Current SwVersion:\s*(?P<package>\S+)\s*\(\s*(?P<version>[^)]+?)\s*\)'
)

# The 'nrsectorcarrier|nrcelldu ^arfcn|ssbfrequency|...|cellLocalId|nRTAC' combo
# command doesn't pad its output to fixed-width columns (blank AutoSelected
# fields emit no token at all, rather than a padded blank one), so the generic
# table parser misaligns every column after the first. Confirmed against real
# output — 'true'/'false' (nRTACInSib1Enabled) is the one field guaranteed
# present, so it's used as a sync point; the optional non-capturing group
# transparently absorbs ssbDurationAutoSelected whether or not it's blank.
# The 'nrsectorcarrier|nrcelldu ...' combo command's column SET varies between
# nodes - some include nRTACInSib1Enabled, some don't (confirmed on one site:
# HXL04147 has it, HXIN090147F doesn't). A positional regex that hardcodes the
# boolean silently misaligns every later field on the other variant, which
# showed up as ssbOffset ('0') being reported as ssbFrequency. So the header
# line is parsed to find each attribute's index, and values are read by name.
# Blank AutoSelected fields emit no token at all, so a plain split() is used
# and short rows are read defensively rather than assuming full width.
_NR_CELLDU_LINE_RE = re.compile(r'^NRCellDU=(\S+)\s+(.*)$', re.M)

# Same root cause as _NR_CELLDU_RE: moshell sizes each column to the widest
# value across ALL rows in the result (here, mixed EUtranCellFDD=... and
# SectorCarrier=... MO instances of very different lengths), so shorter rows'
# data lands shifted left of where the header claims that column starts.
# Confirmed against real output — anchored regex, not position, is the only
# reliable way to read this table. pimAggressorCellId/pimVictimCellId are each
# optionally blank (no token at all when empty); sectorCarrierRef's value is
# itself 3 whitespace-separated tokens ('[1]', '=', 'SectorCarrier=X').
_EUTRAN_CELL_RE = re.compile(
    r'EUtranCellFDD=(?P<cell>\S+)\s+(?P<cellId>\S+)\s+(?P<dlChannelBandwidth>\S+)\s+(?:true|false)\s+'
    r'(?P<earfcndl>\S+)\s+(?P<earfcnul>\S+)\s+(?P<physicalLayerCellId>\S+)\s+(?P<physicalLayerCellIdGroup>\S+)\s+'
    r'(?P<physicalLayerSubCellId>\S+)\s+(?:\S+\s+)?(?:\S+\s+)?(?P<rachRootSequence>\S+)\s+'
    r'(?:\[\d+\]\s*=\s*\S+\s+)?(?P<tac>\S+)\s+(?:true|false)',
    re.I
)


def extract_ul_channel_bandwidth(text):
    """ulChannelBandwidth per cell - only available via 'kget all' full dump
    (not a targeted hget block), so best-effort: returns {} if kget all
    wasn't captured or timed out for this node (confirmed: happens on real
    sites, e.g. FSL00877's kget all timed out server-side).

    Scoped to each 'EUtranCellFDD=X' MO block individually (split first,
    then search each block) rather than one regex sweep over the whole
    multi-megabyte log - the naive whole-file version is catastrophically
    slow (confirmed: multi-minute hang on a 25MB log)."""
    result = {}
    if not text:
        return result
    headers = list(re.finditer(r'^MO\s+\S*(?<!External)EUtranCellFDD=([^\s,]+)\s*$', text, re.M))
    for i, block_m in enumerate(headers):
        cell = block_m.group(1)
        if cell in result:
            continue
        window_end = headers[i + 1].start() if i + 1 < len(headers) else block_m.end() + 50000
        window = text[block_m.end():window_end]
        m = re.search(r'ulChannelBandwidth\s+(\S+)', window)
        if m:
            result[cell] = m.group(1)
    return result


def extract_cell_to_sef(text):
    """Cell -> SectorEquipmentFunction number, via the
    SectorCarrier=|SectorEquipmentFunction hget block's reservedBy
    cross-references (Cell -> SectorCarrier -> SEF chain). No confirmed
    link from SEF number to a specific RRU product name exists in Pre
    kget-all data, so this stops at the SEF number - callers needing the
    Pre-side radio product should treat it as NOT AVAILABLE rather than
    guess further down this chain."""
    block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction')
    if not block:
        return {}
    cell_to_sc = {}
    for m in re.finditer(r'^(SectorCarrier=\S+)\s.*?EUtranCellFDD=(\S+)', block, re.M):
        cell_to_sc[m.group(2)] = m.group(1)
    sc_to_sef = {}
    for m in re.finditer(r'^(SectorEquipmentFunction=\S+)\s.*?SectorCarrier=(\S+)', block, re.M):
        sc_to_sef[f'SectorCarrier={m.group(2)}'] = m.group(1)
    return {cell: sc_to_sef.get(sc) for cell, sc in cell_to_sc.items() if sc_to_sef.get(sc)}


def extract_cell_to_radio(text):
    """Cell -> Pre-side radio model, resolved through the full MO chain:

        EUtranCellFDD -> SectorCarrier   ('hget SectorCarrier=|SectorEquipmentFunction ...')
        SectorCarrier -> RfBranch refs   ('hget sector rfbranch')
        RfBranch      -> FieldReplaceableUnit=RRU-N  ('hget rfbranch auport|rfportref')
        RRU-N         -> product name    ('hget FieldReplaceableUnit product')

    An earlier version stopped at the SectorEquipmentFunction number because
    no SEF->RRU link was confirmed; the link does exist, just via RfBranch
    rather than SEF, so the Radio Type table showed '(SEF ...=2)' where the
    engineer needed the actual radio. Returns {cell: 'RRUS 4449'} style
    short model names, or {} when any command in the chain is absent."""
    if not text:
        return {}

    fru_product = {}
    for m in re.finditer(r'^FieldReplaceableUnit=(\S+)\s+(\S+(?:\s+\S+)*?)\s{2,}',
                          get_command_block(text, 'FieldReplaceableUnit product') or '', re.M):
        fru_product[m.group(1)] = m.group(2).strip()

    branch_to_fru = {}
    for m in re.finditer(r'^(AntennaUnitGroup=\d+,RfBranch=\d+)\s+.*?FieldReplaceableUnit=([^,\s]+)',
                          get_command_block(text, 'rfbranch auport|rfportref') or '', re.M):
        branch_to_fru[m.group(1)] = m.group(2)

    carrier_to_radio = {}
    for m in re.finditer(r'^(SectorCarrier=\S+)\s+(.*)$',
                          get_command_block(text, 'sector rfbranch') or '', re.M):
        refs = re.findall(r'AntennaUnitGroup=\d+,RfBranch=\d+', m.group(2))
        models = {fru_product.get(branch_to_fru[r]) for r in refs if r in branch_to_fru}
        models.discard(None)
        if models:
            carrier_to_radio[m.group(1)] = sorted(models)[0]

    # Cell -> SectorCarrier, from the reservedBy cross-reference block
    result = {}
    block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction')
    for m in re.finditer(r'^(SectorCarrier=\S+)\s+(.*)$', block or '', re.M):
        carrier, rest = m.group(1), m.group(2)
        radio = carrier_to_radio.get(carrier)
        if not radio:
            continue
        for cell in re.findall(r'EUtranCellFDD=(\S+)', rest):
            result[cell] = radio
        for cell in re.findall(r'NRCellDU=(\S+)', rest):
            result[cell] = radio
    return result


def _format_branch_refs(refs, sep=" | ", pair_sep=","):
    """['AntennaUnitGroup=1,RfBranch=9', 'AntennaUnitGroup=1,RfBranch=10', ...]
    -> '1,9 | 1,10 | ...' (or '1-9 | 1-10 | ...' when pair_sep='-'). Refs are
    sorted by (AUG, RfBranch) as integers for a stable, numerically-ordered
    display — confirmed against QUICKIX's own rendering convention."""
    pairs = []
    for ref in refs:
        m = re.match(r'AntennaUnitGroup=(\d+),RfBranch=(\d+)', ref)
        if m:
            pairs.append((int(m.group(1)), int(m.group(2))))
    pairs.sort()
    return sep.join(f"{aug}{pair_sep}{rb}" for aug, rb in pairs)


def extract_rf_branch_refs(text):
    """Cell -> {'tx_ref': str, 'rx_ref': str, 'sef_branches': str}, matching
    QUICKIX HTML's RFBRANCHTXREF / RFBRANCHRXREF / SEF RFBRANCHES columns.

    Confirmed against real Pre kget-all logs (HXL00147 / HXL04147 /
    HXIN090147F):
        'hget sector rfbranch' gives, per SectorCarrier (LTE) or
            NRSectorCarrier (5G): rfBranchRxRef / rfBranchTxRef, each a list
            of 'AntennaUnitGroup=N,RfBranch=M' refs -> TX/RX ref columns,
            joined with ',' inside each pair and ' | ' between pairs
            (e.g. SectorCarrier=10's rfBranchTxRef -> '1,9 | 1,10 | 1,11 | 1,12').
        The same command's second table gives SectorEquipmentFunction's own
            rfBranchRef (its own AntennaUnitGroup/RfBranch list, which can be
            a SUPERSET of any one SectorCarrier's refs when the SEF serves
            multiple SectorCarriers/carriers on the same sector) -> SEF
            RFBRANCHES column, joined with '-' inside each pair instead of
            ',' (matches QUICKIX's own formatting convention for this column).
        'SectorCarrier=|SectorEquipmentFunction ... reservedBy' gives the
            Cell -> SectorCarrier -> SectorEquipmentFunction chain needed to
            attach the right SEF's rfBranchRef list to each cell.

    Returns {} if the required commands aren't present in this log (older
    log captures / different hget command set)."""
    if not text:
        return {}

    # ── Cell -> SectorCarrier, and SectorCarrier -> SectorEquipmentFunction ──
    id_block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction') or ''
    cell_to_sc = {}
    for m in re.finditer(r'^((?:SectorCarrier|NRSectorCarrier)=\S+)\s+.*$', id_block, re.M):
        sc_mo, rest = m.group(1), m.group(0)
        for cell in re.findall(r'(?:EUtranCellFDD|NRCellDU)=(\S+)', rest):
            cell_to_sc[cell] = sc_mo
    sc_to_sef = {}
    for m in re.finditer(r'^(SectorEquipmentFunction=\S+)\s+.*$', id_block, re.M):
        sef_mo, rest = m.group(1), m.group(0)
        for sc in re.findall(r'(?:SectorCarrier|NRSectorCarrier)=\S+', rest):
            sc_to_sef[sc] = sef_mo

    # ── 'hget sector rfbranch': two tables in one block — SectorCarrier's
    # (and NRSectorCarrier's) rfBranchRxRef/rfBranchTxRef, then
    # SectorEquipmentFunction's rfBranchRef. Parsed as one combined block
    # since both use the same '[N] = <refs...>' cross-reference syntax and
    # a shared MO-name regex distinguishes which table a row belongs to. ──
    branch_block = get_command_block(text, 'sector rfbranch') or ''
    sc_tx, sc_rx, sef_refs = {}, {}, {}
    for m in re.finditer(
        r'^((?:SectorCarrier|NRSectorCarrier)=\S+)\s+\[\d+\]\s*=\s*([^\[]*?)\s+\[\d+\]\s*=\s*(.*)$',
        branch_block, re.M
    ):
        mo, rx_part, tx_part = m.group(1), m.group(2), m.group(3)
        sc_rx[mo] = re.findall(r'AntennaUnitGroup=\d+,RfBranch=\d+', rx_part)
        sc_tx[mo] = re.findall(r'AntennaUnitGroup=\d+,RfBranch=\d+', tx_part)
    for m in re.finditer(r'^(SectorEquipmentFunction=\S+)\s+\[\d+\]\s*=\s*(.*)$', branch_block, re.M):
        sef_refs[m.group(1)] = re.findall(r'AntennaUnitGroup=\d+,RfBranch=\d+', m.group(2))

    result = {}
    for cell, sc in cell_to_sc.items():
        tx_ref = _format_branch_refs(sc_tx.get(sc, []), pair_sep=",")
        rx_ref = _format_branch_refs(sc_rx.get(sc, []), pair_sep=",")
        sef = sc_to_sef.get(sc)
        sef_branches = _format_branch_refs(sef_refs.get(sef, []), pair_sep="-") if sef else ""
        result[cell] = {"tx_ref": tx_ref, "rx_ref": rx_ref, "sef_branches": sef_branches}
    return result


def extract_cell_to_fru(text):
    """Cell -> raw FieldReplaceableUnit id (e.g. 'RRU-10'), via the same
    Cell->SectorCarrier->RfBranch->FRU chain as extract_cell_to_radio(), but
    keeping the FRU id itself instead of resolving it to a product name —
    matches QUICKIX HTML's 'RRUs' column (distinct from 'Radio type', which
    shows the resolved model)."""
    if not text:
        return {}
    branch_to_fru = {}
    for m in re.finditer(r'^(AntennaUnitGroup=\d+,RfBranch=\d+)\s+.*?FieldReplaceableUnit=([^,\s]+)',
                          get_command_block(text, 'rfbranch auport|rfportref') or '', re.M):
        branch_to_fru[m.group(1)] = m.group(2)

    carrier_to_fru = {}
    for m in re.finditer(r'^((?:SectorCarrier|NRSectorCarrier)=\S+)\s+(.*)$',
                          get_command_block(text, 'sector rfbranch') or '', re.M):
        refs = re.findall(r'AntennaUnitGroup=\d+,RfBranch=\d+', m.group(2))
        frus = {branch_to_fru.get(r) for r in refs if r in branch_to_fru}
        frus.discard(None)
        if frus:
            carrier_to_fru[m.group(1)] = ", ".join(sorted(frus))

    result = {}
    id_block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction') or ''
    for m in re.finditer(r'^((?:SectorCarrier|NRSectorCarrier)=\S+)\s+.*$', id_block, re.M):
        carrier, rest = m.group(1), m.group(0)
        fru = carrier_to_fru.get(carrier)
        if not fru:
            continue
        for cell in re.findall(r'(?:EUtranCellFDD|NRCellDU)=(\S+)', rest):
            result[cell] = fru
    return result


def extract_nr_used_antennas(text):
    """NRSectorCarrier -> {'tx': str, 'rx': str}, from noOfUsedTxAntennas /
    noOfUsedRxAntennas in the 'SectorCarrier=|SectorEquipmentFunction ...
    reserved' hget block.

    Deliberately separate from checks_sector._extract_sector_config_5g()
    (which reads noOfTxAntennas/noOfRxAntennas — the CONFIGURED max, used
    for Pre-vs-CIQ comparison elsewhere) rather than changing that function:
    confirmed on a real AAS/massive-MIMO node (HXIN090147F) that
    noOfTxAntennas/noOfRxAntennas report 0 while noOfUsedTxAntennas/
    noOfUsedRxAntennas report the real active count (64) — QUICKIX HTML's
    own 5G NR Cells TX/RX display uses the USED count, not the configured
    max, so this is a different (display-only) reading of the same block,
    not a correction to the comparison logic."""
    if not text:
        return {}
    block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction') or ''
    result = {}
    # Locate the header to find noOfUsedRxAntennas/noOfUsedTxAntennas column
    # order, since the table also carries several other numeric columns
    # this project doesn't otherwise parse (massiveMimoSleepState, etc.) and
    # a purely positional regex would be fragile against column reordering.
    header_m = re.search(r'^MO\s+.*\bnoOfUsedRxAntennas\b.*\bnoOfUsedTxAntennas\b.*$', block, re.M)
    if not header_m:
        return result
    header_cols = header_m.group(0).split()
    try:
        rx_idx = header_cols.index('noOfUsedRxAntennas')
        tx_idx = header_cols.index('noOfUsedTxAntennas')
    except ValueError:
        return result
    for m in re.finditer(r'^(NRSectorCarrier=\S+)\s+(.*)$', block, re.M):
        mo, rest = m.group(1), m.group(2)
        tokens = rest.split()
        # header_cols[0] is 'MO' itself, so column i's value is tokens[i-1]
        if len(tokens) >= max(rx_idx, tx_idx):
            cell = mo.split('=', 1)[1]
            result[cell] = {'rx': tokens[rx_idx - 1], 'tx': tokens[tx_idx - 1]}
    return result


def _short_radio_name(product):
    """Normalise a Pre productName to the CIQ's 'RRUS <model>' shape so the
    two columns are visually comparable.

        'Radio 4449 B5 B12A KRC 161 752/1 ...' -> 'RRUS 4449'
        'Radio 4890HP 48B2/B25 48B66 M01 ...'  -> 'RRUS 4890'
        'RRUS 32 B30 KRC 161 423/1 ...'        -> 'RRUS 32'
        'Radio 6472 B77G ...'                  -> 'RRUS AIR6472'

    Takes the token immediately after the family word (Radio/RRUS/AIR)
    rather than the first 4-digit run anywhere in the string - the latter
    matched serial/part numbers on models like 'RRUS 32' (2 digits) and
    missed suffixed models like '4890HP'."""
    if not product:
        return None
    m = re.match(r'\s*(?:Radio|RRUS|AIR)\s+([A-Za-z]*\d+)', product, re.I)
    if not m:
        return product.split()[0]
    model = re.match(r'([A-Za-z]*\d+)', m.group(1)).group(1)
    if re.match(r'^(?:64|66|84)\d{2}$', model):
        return f'RRUS AIR{model}'
    return f'RRUS {model}'


def build_moved_cell_source_map(ciq_wb):
    """From Sector Del_Movement: {target_cell: (source_node, source_cell)}
    for every row that has both a Source and a Target (i.e. a real move,
    not a delete). Cells moving in from another physical node keep their
    real Pre-side history on the SOURCE node's kget-all log under the
    SOURCE cell name - the target node's own log has never seen them, since
    they haven't physically moved yet at Pre-scripting time (confirmed on a
    real rehome: FSL00452's own log has no 'FSL00452_2B_1' at all; the real
    Pre data sits on FSL02452's log as 'FSL02452_2B_1'). Every check that
    looks up Pre data by cell name needs this remap or it reports NA for
    every moved-in cell despite real data being available."""
    result = {}
    for r in cer.sheet_rows_as_dicts(ciq_wb['Sector Del_Movement']) if 'Sector Del_Movement' in ciq_wb.sheetnames else []:
        src_node, src_sector = r.get('Source Node name'), r.get('Source Sector')
        tgt_node, tgt_sector = r.get('Target Node name'), r.get('Target Sector')
        if src_node and src_sector and tgt_node and tgt_sector and str(tgt_node).strip().upper() != 'DELETE':
            result[str(tgt_sector).strip()] = (str(src_node).strip(), str(src_sector).strip())
    return result


def remap_pre_dict(source_dict, cell_rename_map):
    """cell_rename_map: {source_cell: target_cell}. Returns a new dict with
    keys renamed to the target side, for merging a source node's Pre
    extraction into a target node's results."""
    return {cell_rename_map[c]: v for c, v in source_dict.items() if c in cell_rename_map}


def merge_moved_in_pre(base_dict, node_logs, moved_map, extract_fn):
    """base_dict: {cell: value} already extracted from this node's own log.
    moved_map: {target_cell: (source_node, source_cell)} - typically the
    subset from build_moved_cell_source_map() relevant to this node (or the
    full map; entries not matching base_dict's node are simply not filled,
    since target_cell won't be looked up by an unrelated node's checks).
    node_logs: {node_id: text}. extract_fn: text -> {cell: value}, e.g.
    extract_lte_sector_params, extract_nr_tac, extract_cell_to_radio.

    For each target cell NOT already in base_dict, pulls the value from its
    source node's own log (extracted fresh via the same extract_fn) under
    the source cell name, and inserts it under the target name. This is
    what makes a moved-in cell show real Pre data instead of NA - the
    target node's own log has never seen it; the real history is on the
    source node's log (confirmed on a real rehome)."""
    merged = dict(base_dict)
    by_source = {}
    for target_cell, (source_node, source_cell) in moved_map.items():
        if target_cell in merged:
            continue
        by_source.setdefault(source_node, {})[source_cell] = target_cell
    for source_node, rename in by_source.items():
        source_text = node_logs.get(source_node)
        if not source_text:
            continue
        source_vals = extract_fn(source_text)
        for source_cell, target_cell in rename.items():
            if source_cell in source_vals:
                merged[target_cell] = source_vals[source_cell]
    return merged


def extract_cell_to_rilink(text):
    """Cell -> 'Single'/'Double' RILink count, from 'hget rilink=' riPortRef2
    (FieldReplaceableUnit=RRU-N,RiPort=...) counted per RRU-N, joined via the
    same RfBranch->RRU chain as extract_cell_to_radio."""
    if not text:
        return {}
    fru_by_branch = {}
    for m in re.finditer(r'^(AntennaUnitGroup=\d+,RfBranch=\d+)\s+.*?FieldReplaceableUnit=([^,\s]+)',
                          get_command_block(text, 'rfbranch auport|rfportref') or '', re.M):
        fru_by_branch[m.group(1)] = m.group(2)
    rru_links = {}
    for m in re.finditer(r'FieldReplaceableUnit=(RRU-\S+),RiPort=', get_command_block(text, 'rilink=') or ''):
        rru_links[m.group(1)] = rru_links.get(m.group(1), 0) + 1
    carrier_rru = {}
    for m in re.finditer(r'^(SectorCarrier=\S+)\s+(.*)$', get_command_block(text, 'sector rfbranch') or '', re.M):
        refs = re.findall(r'AntennaUnitGroup=\d+,RfBranch=\d+', m.group(2))
        rrus = {fru_by_branch[r] for r in refs if r in fru_by_branch}
        if rrus:
            carrier_rru[m.group(1)] = sorted(rrus)[0]
    result = {}
    block = get_command_block(text, 'SectorCarrier=|SectorEquipmentFunction')
    for m in re.finditer(r'^(SectorCarrier=\S+)\s+(.*)$', block or '', re.M):
        carrier, rest = m.group(1), m.group(2)
        rru = carrier_rru.get(carrier)
        if not rru:
            continue
        n = rru_links.get(rru, 0)
        label = 'Double' if n >= 2 else ('Single' if n == 1 else None)
        if not label:
            continue
        for cell in re.findall(r'EUtranCellFDD=(\S+)', rest):
            result[cell] = label
        for cell in re.findall(r'NRCellDU=(\S+)', rest):
            result[cell] = label
    return result


def parse_rbb_txrx(rbb_type):
    """'RBB44_1D' -> '4x4'. Returns None if not RBB44/42/22-style."""
    if not rbb_type:
        return None
    m = re.match(r'RBB(\d)(\d)', str(rbb_type))
    return f'{m.group(1)}x{m.group(2)}' if m else None


def node_id_from_log(text):
    """The node ID as it appears at the moshell prompt, e.g. 'SCL05020'."""
    m = re.search(r'^([A-Za-z0-9_]+)>\s', text, re.M)
    return m.group(1) if m else None


def extract_ptp_status(text):
    """NEW - not part of the confirmed Blueprint rule set. PTP presence/state,
    ported from the sibling HTML tool's regex (Transport=1...Ptp=1...
    operationalState). UNCONFIRMED against a real kget-all log captured by
    THIS project's own hget command set (no such log was available to test
    against) - the HANDOFF for this project explicitly flags PTP as 'no PTP
    signal found' in the confirmed hget commands, so treat this as a
    best-effort layer over the raw text, not a confirmed rule. Verify
    against a real log before trusting it on a live site.
    Returns 'ENABLED' / 'DISABLED' / 'NOT PRESENT'.
    """
    m = re.search(r'Transport\s*=\s*1[\s\S]{0,300}?Ptp\s*=\s*1[\s\S]{0,300}?operationalState\s*[:=]?\s*(\w+)', text, re.I)
    if not m:
        return 'NOT PRESENT'
    state = m.group(1).upper()
    return 'ENABLED' if state in ('ENABLED', 'UP', 'TRUE', '1') else 'DISABLED'


def extract_dss_status(text):
    """NEW - not part of the confirmed Blueprint rule set. Pre-existing DSS,
    ported from the sibling HTML tool's regex (non-zero essScPairId /
    essScLocalId per LTE cell). Same caveat as extract_ptp_status() - the
    HANDOFF explicitly flags DSS as unconfirmed in this project's hget
    command set; verify against a real log before trusting it.
    Returns {cell_name: True/False} for every EUtranCellFDD block found
    with an essScPairId/essScLocalId reference.
    """
    result = {}
    for m in re.finditer(r'EUtranCellFDD=(?P<cell>\S+?)[\s\S]{0,400}?essScPairId\s*[:=]?\s*(?P<pair>\d+)[\s\S]{0,200}?essScLocalId\s*[:=]?\s*(?P<local>\d+)', text):
        active = m.group('pair') not in ('0', '') and m.group('local') not in ('0', '')
        result[m.group('cell')] = active
    return result


def extract_sw_version(text):
    """Rule #1: SW Version / SW Package, from the 'cvcu'/'cvls' backup-version
    block's 'Current SwVersion: <package> (<version>)' line.
    Returns {'sw_package': str, 'sw_version': str} or None if not found."""
    m = _SW_VERSION_RE.search(text)
    if not m:
        return None
    return {'sw_package': m.group('package'), 'sw_version': m.group('version')}


def extract_identity(parsed):
    """Rule #2/12/14/17 (collapsed): eNBId (from ENodeBFunction=1) and gNBId
    (from GNBDUFunction=1, per the confirmed preference over CUCP/CUUP).
    Returns {'eNBId': str|None, 'gNBId': str|None, 'gNBIdLength': str|None}.
    None values mean that identity type genuinely isn't present on this node
    (e.g. an LTE-only node has no gNBId at all)."""
    entry = find_command(parsed, 'eNBId|gNBId')
    result = {'eNBId': None, 'gNBId': None, 'gNBIdLength': None}
    if not entry:
        return result
    for row in all_rows(entry):
        mo = row.get('MO', '')
        if mo.startswith('ENodeBFunction') and row.get('eNBId'):
            result['eNBId'] = row['eNBId']
        elif mo.startswith('GNBDUFunction') and row.get('gNBId'):
            result['gNBId'] = row['gNBId']
            result['gNBIdLength'] = row.get('gNBIdLength')
    return result


def extract_hardware(parsed):
    """Rule #5/#15 (board type) + XMU presence for #11/#26/#27.
    Returns {'boards': [{'mo':..,'model':..}], 'radios': [...], 'xmus': [...]}
    split by FieldReplaceableUnit MO prefix. 'model' is the productName with
    the leading family word (Baseband/Radio/SAU/SUP) kept, since RFDS text
    needs the same family word stripped/matched on the numeric token by the
    caller (mirrors QUICKIX's hw_string()/extract_pre_hw() approach of
    comparing the last whitespace token)."""
    entry = find_command(parsed, 'FieldReplaceableUnit product')
    boards, radios, xmus, other = [], [], [], []
    if not entry:
        return {'boards': boards, 'radios': radios, 'xmus': xmus, 'other': other}
    for row in all_rows(entry):
        mo = row.get('MO', '')
        item = {'mo': mo, 'model': row.get('productName', '').strip()}
        if mo.upper().startswith('FIELDREPLACEABLEUNIT=XMU'):
            xmus.append(item)
        elif 'RRU-' in mo.upper() or item['model'].upper().startswith('RADIO'):
            radios.append(item)
        elif (item['model'].upper().startswith('BASEBAND')
              or item['model'].upper().startswith('RAN PROCESSOR')) and 'XMU' not in mo.upper():
            # 'RAN Processor NNNN' is the same class of board as 'Baseband
            # NNNN' - Ericsson uses both names for the DU (confirmed: a real
            # node reports 'RAN Processor 6651', and the RFDS Non-RF
            # Inventory uses 'RAN PROCESSOR 6672' for the same field).
            # Matching only 'Baseband' left that node with no board at all,
            # which surfaced as 'NOT FOUND' in the Pre/Post configuration.
            boards.append(item)
        else:
            other.append(item)
    return {'boards': boards, 'radios': radios, 'xmus': xmus, 'other': other}


def model_token(product_name):
    """Last whitespace token of a productName, e.g. 'Baseband 6630' -> '6630',
    'Radio 4449 B5 B12A' -> 'B12A' (radios need a different join key upstream;
    for boards this reliably isolates the bare model number)."""
    if not product_name:
        return None
    tokens = product_name.strip().split()
    return tokens[-1] if tokens else None


def extract_tac(text):
    """Rule #16: LTE TAC per cell. Takes raw log TEXT (see _EUTRAN_CELL_RE's
    comment for why this table needs regex, not the fixed-width parser).
    Returns {cell_name: tac_str, ...}. Cells on one node normally share a
    single TAC; callers should flag internally-inconsistent TACs as their own
    anomaly rather than silently picking one."""
    block = get_command_block(text, 'EUtranCell.DD|Sector')
    result = {}
    if not block:
        return result
    for m in _EUTRAN_CELL_RE.finditer(block):
        result[m.group('cell')] = m.group('tac')
    return result


def _parse_nrcell_block(block, mo_prefix, header_key='cellLocalId'):
    """Parse NRCellCU=/NRCellDU= rows into {cell: {attr: value}}, reading
    each attribute's CHARACTER POSITION from the block's own header line.

    Two independent quirks make simpler approaches wrong here:
      - The column set varies between nodes (one node's NRCellDU table has
        nRTACInSib1Enabled, another's doesn't), so a positional regex with a
        hardcoded boolean misaligns everything after it.
      - Blank trailing/optional fields emit NO token at all, so splitting the
        row and zipping against the header list also misaligns - confirmed:
        a 9-attribute header with only 6 values mapped ssbFrequency onto
        ssbOffset's '0' instead of the real 395070.
    Slicing by the header's own column offsets handles both, since moshell
    pads values to their column start."""
    if not block:
        return {}
    result = {}
    spans = None
    row_re = re.compile(r'^' + mo_prefix + r'=(\S+)')
    for line in block.splitlines():
        stripped = line.rstrip()
        if stripped.startswith('MO ') and header_key in stripped:
            spans = [(m.group(0), m.start()) for m in re.finditer(r'\S+', stripped)][1:]
            continue
        if not spans:
            continue
        m = row_re.match(stripped)
        if not m:
            continue
        row = {}
        for i, (attr, start) in enumerate(spans):
            end = spans[i + 1][1] if i + 1 < len(spans) else len(stripped)
            val = stripped[start:end].strip() if start < len(stripped) else ''
            if val:
                row[attr] = val
        result[m.group(1)] = row
    return result


def extract_nr_tac(text):
    """Rule #7/#8: NR TAC per 5G cell.

    Sourced from the 'hget ^NRCell|syncsignal ...' command's NRCellCU table,
    which is a clean three-column block (cellLocalId/nCI/nRTAC) present on
    every node checked - rather than the 'nrsectorcarrier|nrcelldu' combo
    command, whose column set varies by node. A blank nRTAC there is a real
    value (NSA cells report nothing), so it maps to None, not a parse
    failure."""
    block = get_command_block(text, 'NRCell|syncsignal sectorCarrierRef')
    rows = _parse_nrcell_block(block, 'NRCellCU')
    return {cell: vals['nRTAC'] for cell, vals in rows.items() if vals.get('nRTAC')}


def extract_lte_sector_params(text):
    """Rule #19 (LTE half): earfcndl/earfcnul/dlChannelBandwidth/tac/
    rachRootSequence/cellId per cell, from the same combined EUtranCellFDD
    block used by extract_tac(). Returns {cell_name: {field: value}}."""
    block = get_command_block(text, 'EUtranCell.DD|Sector')
    result = {}
    if not block:
        return result
    fields = ('cellId', 'dlChannelBandwidth', 'earfcndl', 'earfcnul', 'rachRootSequence', 'tac')
    for m in _EUTRAN_CELL_RE.finditer(block):
        result[m.group('cell')] = {f: m.group(f) for f in fields}
    return result


def extract_5g_sector_params(parsed, text):
    """Rule #19 (5G half) + #25: arfcnDL/arfcnUL/bSChannelBwDL/bSChannelBwUL
    (from NRSectorCarrier, reliably fixed-width -> generic table parser) plus
    ssbFrequency/cellLocalId/nRTAC (from NRCellDU, needs regex -- see
    _NR_CELLDU_RE). Returns {cell_name: {field: value}}."""
    result = {}
    # NRSectorCarrier table: parsed by header character position for the same
    # reason as _parse_nrcell_block - the generic fixed-width table parser
    # merges these four columns into one field, because this header separates
    # them with single spaces where that parser expects 2+ (confirmed: it
    # returned a single 'arfcnDL arfcnUL bSChannelBwDL bSChannelBwUL' key).
    carrier_block = get_command_block(text, 'NRSector arfcn')
    for cell, vals in _parse_nrcell_block(carrier_block, 'NRSectorCarrier', 'arfcnDL').items():
        result.setdefault(cell, {}).update({
            'arfcnDL': vals.get('arfcnDL'),
            'arfcnUL': vals.get('arfcnUL'),
            'bSChannelBwDL': vals.get('bSChannelBwDL'),
            'bSChannelBwUL': vals.get('bSChannelBwUL'),
        })
    block = get_command_block(text, 'nrsectorcarrier|nrcelldu')
    du_rows = _parse_nrcell_block(block, 'NRCellDU')
    for cell, vals in du_rows.items():
        result.setdefault(cell, {}).update({
            'ssbFrequency': vals.get('ssbFrequency'),
            'cellLocalId': vals.get('cellLocalId'),
        })
    # nRTAC comes from the cleaner NRCellCU table (see extract_nr_tac)
    nr_tacs = extract_nr_tac(text)
    for cell, tac in nr_tacs.items():
        result.setdefault(cell, {})['nRTAC'] = tac
    return result


def extract_5g_sector_params_from_text(text):
    """Text-only adapter for extract_5g_sector_params, so it fits
    merge_moved_in_pre's extract_fn(text) -> dict interface (that function
    normally also needs a pre-parsed `parsed` from the caller's own log,
    but a source node's log needs its own separate parse anyway)."""
    import log_parser as lp
    return extract_5g_sector_params(lp.parse_log(text), text)


def extract_nbiot_cells(parsed):
    """Rule #4: NBIoT cell presence, from 'hget ^nbiotcell ...'. Returns a list
    of {'cell':.., 'cellid':.., 'physicalLayerCellId':.., 'tac':..} — empty
    list means no NBIoT cells on this node (check does not trigger)."""
    entry = find_command(parsed, 'nbiotcell')
    out = []
    if not entry:
        return out
    for row in all_rows(entry):
        out.append({
            'cell': row.get('MO', '').split('=', 1)[-1],
            'cellid': row.get('cellid'),
            'physicalLayerCellId': row.get('physicalLayerCellId'),
            'tac': row.get('tac'),
        })
    return out
