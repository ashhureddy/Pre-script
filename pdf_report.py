"""
PDF report generator - follows the Blueprint's 'Pre checks validation format'
sheet section-by-section (numbering below matches that sheet exactly).
Header on every page: MasTec logo top-right, FA Code/Site ID/USID top-left.
Concise by design: tight header rows, compact tables, no filler text -
matches the reference QuadGen Post-Checks PDF's plain 'Summary Status'-style
heading weight rather than oversized banners.
"""
import os
import re

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak)

BRAND_NAVY = colors.HexColor('#101F90')
HEADER_BG = colors.HexColor('#dde3f7')
MATCH_BG = colors.HexColor('#c8ecc8')
MISMATCH_BG = colors.HexColor('#f7c5c5')
EXPECTED_BG = colors.HexColor('#ffe3ad')
NEUTRAL_BG = colors.HexColor('#f2f2f2')

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mastec_logo_trim.png')


def _row_bg(status):
    return {'MATCH': MATCH_BG, 'MISMATCH': MISMATCH_BG, 'EXPECTED': EXPECTED_BG}.get(status, NEUTRAL_BG)


def _styled_table(header, body_rows, row_statuses, col_widths):
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle('TCell', parent=styles['Normal'], fontSize=7.6, alignment=1, leading=9)
    head_style = ParagraphStyle('THead', parent=styles['Normal'], fontSize=7.2, alignment=1,
                                 fontName='Helvetica-Bold', leading=8.5, textColor=BRAND_NAVY)
    data = [[Paragraph(str(h), head_style) for h in header]]
    for row in body_rows:
        data.append([Paragraph(str(v), cell_style) for v in row])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.white),
        ('TOPPADDING', (0, 0), (-1, 0), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 2.5),
        ('TOPPADDING', (0, 1), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]
    for i, status in enumerate(row_statuses, start=1):
        style.append(('BACKGROUND', (0, i), (-1, i), _row_bg(status)))
    table.setStyle(TableStyle(style))
    return table


def _sec(n, title, styles):
    """Section heading, sized like the reference PDF's plain headings - not
    oversized banners."""
    return Paragraph(f'{n}) {title}', ParagraphStyle('SecH', parent=styles['Heading3'], fontSize=10.5,
                                                       spaceBefore=10, spaceAfter=4, textColor=BRAND_NAVY))


def _mono(text, styles, size=8.5):
    return Paragraph(text, ParagraphStyle('Mono', parent=styles['Normal'], fontSize=size, leading=size + 3.5, spaceAfter=1))


def _verdict(status, ok='Match', bad='Mismatch', skip='N/A', expected='Planned'):
    return {'MATCH': ok, 'MISMATCH': bad, 'EXPECTED': expected}.get(status, skip)


def _table_if_rows(n, title, rows, columns, styles, story, widths=None):
    if not rows:
        return
    story.append(_sec(n, title, styles))
    header = [c[0] for c in columns]
    body, statuses = [], []
    for r in rows:
        line = []
        for _, key in columns:
            v = key(r) if callable(key) else r.get(key)
            line.append('—' if v in (None, '') else str(v))
        body.append(line)
        statuses.append(r['status'])
    if widths is None:
        widths = [7.0 * inch / len(columns)] * len(columns)
    story.append(_styled_table(header, body, statuses, widths))
    story.append(Spacer(1, 3))


def _make_header_footer(site_header_text):
    def _draw(canvas, doc):
        canvas.saveState()
        page_w, page_h = letter
        if os.path.exists(LOGO_PATH):
            logo_w = 1.0 * inch
            logo_h = logo_w * (185.0 / 1003.0)
            canvas.drawImage(LOGO_PATH, page_w - 0.6 * inch - logo_w, page_h - 0.5 * inch - logo_h,
                              width=logo_w, height=logo_h, mask='auto')
        canvas.setFont('Helvetica-Bold', 8)
        canvas.setFillColor(BRAND_NAVY)
        canvas.drawString(0.55 * inch, page_h - 0.45 * inch, site_header_text)
        canvas.setStrokeColor(colors.HexColor('#cccccc'))
        canvas.setLineWidth(0.5)
        canvas.line(0.55 * inch, page_h - 0.56 * inch, page_w - 0.55 * inch, page_h - 0.56 * inch)
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(page_w - 0.55 * inch, 0.35 * inch, f'Page {canvas.getPageNumber()}')
        canvas.restoreState()
    return _draw


def build_report(output_path, site_details, pre_config_text, post_config_text, scope_lines,
                  results, skipped_deleted=None):
    """results: dict keyed by section name, each value a list of result rows
    - see run_validation.py for exactly what's passed. Kept as one dict
    (rather than a long positional arg list) since the blueprint has 19
    distinct sections."""
    styles = getSampleStyleSheet()
    fa = site_details.get('fa_code', 'N/A')
    sid = site_details.get('site_id', 'N/A')
    atoll = site_details.get('atoll_site_name', '')
    usid = site_details.get('usid', '')
    header_line = f'FA Code: {fa}    |    Site ID: {sid}' + (f'    |    USID: {usid}' if usid else '')

    story = []
    story.append(Paragraph(atoll or sid, ParagraphStyle('Title', parent=styles['Title'], fontSize=15,
                                                          spaceBefore=4, spaceAfter=1, textColor=BRAND_NAVY)))
    story.append(Paragraph('Pre-Scripting Validation Report',
                            ParagraphStyle('Sub', parent=styles['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=4)))

    legend = Table([['', 'Passed', '', 'Planned change', '', 'Needs attention']],
                    colWidths=[0.14 * inch, 0.55 * inch, 0.14 * inch, 1.1 * inch, 0.14 * inch, 1.1 * inch])
    legend.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), MATCH_BG), ('BACKGROUND', (2, 0), (2, 0), EXPECTED_BG),
        ('BACKGROUND', (4, 0), (4, 0), MISMATCH_BG),
        ('BOX', (0, 0), (0, 0), 0.4, colors.grey), ('BOX', (2, 0), (2, 0), 0.4, colors.grey),
        ('BOX', (4, 0), (4, 0), 0.4, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 7), ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#555555')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    story.append(legend)
    story.append(Spacer(1, 6))

    # ---- 1) Pre/Post configuration ----
    story.append(_sec(1, 'Pre/post configuration', styles))
    story.append(_mono(f'<b>Pre Configuration:</b>&nbsp;&nbsp;{pre_config_text or "—"}', styles))
    story.append(_mono(f'<b>Post Configuration:</b>&nbsp;&nbsp;{post_config_text or "—"}', styles))
    if results.get('sa_note'):
        story.append(_mono(f'<i>Note: SA Configuration on: {results["sa_note"]}, in pre</i>', styles, size=7.5))
    if skipped_deleted:
        story.append(_mono('<i>Note: ' + ', '.join(skipped_deleted) +
                            ' being deleted — excluded from checks below.</i>', styles, size=7.5))
    story.append(Spacer(1, 4))

    # ---- 2) SOW analysis ----
    story.append(_sec(2, 'SOW analysis', styles))
    if scope_lines:
        for line in scope_lines:
            story.append(_mono(line, styles))
    else:
        story.append(_mono('No carrier changes detected.', styles))
    story.append(PageBreak())

    # ---- 3) Software version ----
    sw_rows = results.get('sw_version', [])
    if sw_rows:
        story.append(_sec(3, 'Software version', styles))
        body = [[r['node'], r.get('sw_version', '—'), r.get('sw_package', '—')] for r in sw_rows]
        story.append(_styled_table(['Node', 'SW version', 'SW package'], body, ['NEUTRAL'] * len(body),
                                    [1.6 * inch, 2.7 * inch, 2.7 * inch]))
        story.append(Spacer(1, 4))

    # ---- 4) eNBId/gNBId validation ----
    ident_rows = results.get('identity', [])
    if ident_rows:
        story.append(_sec(4, 'Enbid/Gnbid validation - Pre vs CIQ', styles))
        body, statuses = [], []
        for r in ident_rows:
            body.append([r['node'],
                         r.get('pre_eNBId') or 'NA', r.get('ciq_eNBId') or 'NA',
                         r.get('pre_gNBId') or 'NA', r.get('ciq_gNBId') or 'NA'])
            statuses.append(r['status'])
        story.append(_styled_table(['Node', 'eNBId [PRE]', 'eNBId [CIQ]', 'gNBId [PRE]', 'gNBId [CIQ]'],
                                    body, statuses, [1.4 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch]))
        story.append(Spacer(1, 4))

    # ---- 5) Primary & secondary node Validation ----
    ps_rows = results.get('primary_secondary', [])
    _table_if_rows(5, 'Primary & secondary node Validation', ps_rows,
                    [('CIQ', 'ciq'), ('EDP', 'edp'), ('RFDS', 'rfds'), ('Match', lambda r: _verdict(r['status']))],
                    styles, story, [2.1 * inch, 2.1 * inch, 2.1 * inch, 0.7 * inch])

    # ---- 6) Board type ----
    board_rows = results.get('board_type', [])
    _table_if_rows(6, 'Board type', board_rows,
                    [('Node', 'node'), ('CIQ', 'ciq_du_type'), ('EDP', 'edp_model'),
                     ('RFDS', lambda r: {True: r['ciq_du_type'], False: 'DIFFERS'}.get(r.get('rfds_agrees'), 'N/C')),
                     ('Match', lambda r: _verdict(r['status'], expected='Board swap'))],
                    styles, story, [1.6 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch, 1.5 * inch])

    # ---- 7) XMU Validation ----
    xmu_rows = results.get('xmu', [])
    _table_if_rows(7, 'XMU Validation', xmu_rows,
                    [('Node', 'node'), ('CIQ', lambda r: 'XMU present' if r.get('ciq_xmu') else 'No XMU'),
                     ('RFDS', lambda r: 'XMU present' if r.get('rfds_xmu') else 'No XMU'),
                     ('Match', lambda r: _verdict(r['status']))],
                    styles, story, [1.7 * inch, 1.7 * inch, 1.7 * inch, 1.9 * inch])
    story.append(PageBreak())

    # ---- 8) Cells verification ----
    _table_if_rows(8, 'Cells verification', results.get('cells_vs_rfds', []),
                    [('CIQ', 'ciq_cell'), ('RFDS', 'rfds_cell'), ('Match', lambda r: _verdict(r['status']))],
                    styles, story, [2.8 * inch, 2.8 * inch, 1.4 * inch])

    # ---- 9) Cell ID verification ----
    _table_if_rows(9, 'Cell ID verification', results.get('cell_id_vs_rfds', []),
                    [('Cells', 'cell'), ('Pre', 'pre'), ('CIQ [Cellid/celllocalid]', 'ciq'),
                     ('RFDS [RCN]', 'rfds_rcn'), ('Match', lambda r: _verdict(r['status']))],
                    styles, story, [2.2 * inch, 1.0 * inch, 1.7 * inch, 1.2 * inch, 0.9 * inch])
    story.append(PageBreak())

    # ---- 10) Parameters Verification - 4G ----
    _table_if_rows(10, 'Parameters Verification - 4G (Pre vs CIQ)', results.get('params_4g', []),
                    [('Cells', 'cell'), ('earfcndl', 'earfcndl'), ('earfcnul', 'earfcnul'),
                     ('dlChannelBandwidth', 'dlChannelBandwidth'), ('ulChannelBandwidth', 'ulChannelBandwidth')],
                    styles, story, [1.7 * inch, 1.3 * inch, 1.3 * inch, 1.35 * inch, 1.35 * inch])

    # ---- 11) Parameters Verification - 5G ----
    _table_if_rows(11, 'Parameters Verification - 5G (Pre vs CIQ)', results.get('params_5g', []),
                    [('Cells', 'cell'), ('arfcnDL', 'arfcnDL'), ('arfcnUL', 'arfcnUL'),
                     ('bSChannelBwDL', 'bSChannelBwDL'), ('bSChannelBwUL', 'bSChannelBwUL'), ('ssbfrequency', 'ssbfrequency')],
                    styles, story, [1.4 * inch, 1.1 * inch, 1.1 * inch, 1.1 * inch, 1.1 * inch, 1.2 * inch])
    story.append(PageBreak())

    # ---- 12) PCI verification ----
    pci4g = results.get('pci_4g', [])
    if pci4g:
        story.append(_sec(12, 'PCI verification', styles))
        body = [[r['cell'], r['group'], r['sub'], r['pci'], _verdict(r['status'])] for r in pci4g]
        story.append(_styled_table(['Cells - 4G', 'PhysicalLayerCellIdGroup', 'physicalLayerSubCellId', 'PCI', 'Match'],
                                    body, [r['status'] for r in pci4g],
                                    [1.7 * inch, 1.7 * inch, 1.7 * inch, 0.9 * inch, 1.0 * inch]))
        story.append(Spacer(1, 3))
    pci5g = results.get('pci_5g', [])
    if pci5g:
        body = [[r['cell'], r['nrpci'], _verdict(r['status'])] for r in pci5g]
        story.append(_styled_table(['Cells - 5G', 'nRPCI', 'Match'], body, [r['status'] for r in pci5g],
                                    [3.5 * inch, 2.0 * inch, 1.5 * inch]))
        story.append(Spacer(1, 4))

    # ---- 13) Radio Type verification (+ sub-tables) ----
    radio_rows = results.get('radio_type', [])
    _table_if_rows(13, 'Radio Type verification', radio_rows,
                    [('Cells', 'cell'), ('Pre', 'pre'), ('CIQ', 'ciq'), ('RFDS', 'rfds'),
                     ('Match', lambda r: _verdict(r['status'], bad='No Match'))],
                    styles, story, [1.6 * inch, 1.7 * inch, 1.2 * inch, 1.6 * inch, 0.9 * inch])

    swap_rows = results.get('sector_swap', [])
    if swap_rows:
        story.append(_sec('13a', 'Sector/TX-RX/Power (Pre vs CIQ, best-effort)', styles))
        body = [[r['cell'], r['sec_id'], r['pre_txrx'], r['ciq_txrx'], r['ciq_power']] for r in swap_rows]
        story.append(_styled_table(['Cells', 'sec_id', 'TX/RX [Pre]', 'TX/RX [CIQ]', 'Power [CIQ]'], body,
                                    ['NEUTRAL'] * len(body), [1.6 * inch, 1.0 * inch, 1.5 * inch, 1.5 * inch, 1.4 * inch]))
        story.append(Spacer(1, 3))

    share_rows = results.get('radio_sharing', [])
    if share_rows:
        story.append(_sec('13b', 'Shared radios', styles))
        body = [[r['cell'], r['note']] for r in share_rows]
        story.append(_styled_table(['Cells', 'Comment'], body, ['NEUTRAL'] * len(body), [4.0 * inch, 3.0 * inch]))
        story.append(Spacer(1, 4))
    story.append(PageBreak())

    # ---- 14) RI port Verification ----
    port_rows = results.get('port_uniqueness', [])
    _table_if_rows(14, 'RI port Verification', port_rows,
                    [('Cells', 'cell'), ('BBU/XMU', 'bbu'), ('Port', 'port'),
                     ('Port Uniqueness', lambda r: 'Unique' if r['status'] == 'MATCH' else 'Not Unique')],
                    styles, story, [2.2 * inch, 1.6 * inch, 1.2 * inch, 2.0 * inch])
    xmu_overlap = results.get('xmu_port_overlap', [])
    if xmu_overlap:
        body = [[r['node'], r['du_type'], r['xmu'], r['xmu_ports'],
                 'Unique' if r['status'] == 'MATCH' else 'Not Unique'] for r in xmu_overlap]
        story.append(_styled_table(['Node id', '1st DU type', '1st XMU', 'XMU Ports', 'Port Uniqueness'],
                                    body, [r['status'] for r in xmu_overlap],
                                    [1.4 * inch, 1.3 * inch, 1.1 * inch, 1.8 * inch, 1.4 * inch]))
        story.append(Spacer(1, 4))

    # ---- 15) Antenna Uniqueness ----
    _table_if_rows(15, 'Antenna Uniqueness', results.get('antenna', []),
                    [('Cells', 'cell'), ('AUG/AU/ASU (1)', 'aug_au_asu_1'), ('AUG/AU/ASU (2)', 'aug_au_asu_2'),
                     ('Match', 'verdict')],
                    styles, story, [2.6 * inch, 1.5 * inch, 1.5 * inch, 1.4 * inch])
    story.append(PageBreak())

    # ---- 16) NBIoT ----
    _table_if_rows(16, 'NBIOT Cells check', results.get('nbiot', []),
                    [('nbIotCellName', 'cell'), ('NBIoT Cell ID [pre]', 'pre_id'), ('NBIoT Cell ID [post]', 'ciq_id'),
                     ('Match', lambda r: _verdict(r['status'], skip='No NBIoT'))],
                    styles, story, [2.2 * inch, 1.7 * inch, 1.7 * inch, 1.4 * inch])

    # ---- 17) NR TAC ----
    _table_if_rows(17, 'NR TAC Verification', results.get('nr_tac', []),
                    [('Cell', 'cell'), ('Pre nRTAC', 'pre_nrtac'), ('CIQ nRTAC', 'ciq_nrtac'),
                     ('Match', lambda r: _verdict(r['status']))],
                    styles, story, [2.5 * inch, 1.7 * inch, 1.7 * inch, 1.1 * inch])

    # ---- 18) Pre-existing node TAC ----
    tac_rows = results.get('tac', [])
    if tac_rows:
        story.append(_sec(18, 'Pre-existing node (TAC)', styles))
        body = [[r['node'], r.get('pre_tac') or 'NA', r.get('ciq_tac') or 'NA'] for r in tac_rows]
        story.append(_styled_table(['Node ID', 'Pre TAC', 'CIQ TAC'], body, [r['status'] for r in tac_rows],
                                    [2.3 * inch, 2.3 * inch, 2.4 * inch]))
        story.append(Spacer(1, 4))

    # ---- 19) AIR RADIO CHECKS ----
    _table_if_rows(19, 'AIR RADIO CHECKS', results.get('sef_fru', []),
                    [('Cell', 'cell'), ('RRU Type', 'rru_type'), ('SEF', 'sef'), ('RRU FieldReplaceableUnit', 'fru')],
                    styles, story, [1.9 * inch, 1.7 * inch, 1.9 * inch, 1.5 * inch])

    if results.get('unavailable_notes'):
        story.append(_sec('—', 'Not available in this run', styles))
        for n in results['unavailable_notes']:
            story.append(_mono('• ' + n, styles, size=7.5))

    doc = SimpleDocTemplate(output_path, pagesize=letter,
                             topMargin=0.75 * inch, bottomMargin=0.55 * inch,
                             leftMargin=0.55 * inch, rightMargin=0.55 * inch)
    hf = _make_header_footer(header_line)
    doc.build(story, onFirstPage=hf, onLaterPages=hf)
