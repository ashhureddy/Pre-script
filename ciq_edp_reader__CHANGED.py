# ciq_edp_reader.py — ADDED at end of file (after edp_primary_secondary()).
# Nothing else in this file was touched.

def find_revision_history_sheet(ciq_wb):
    """Same fuzzy match as QUICKIX's findRevisionHistorySheet(): exact name
    'Revision History' (any case/whitespace) first, else any sheet whose
    name contains 'revision'."""
    for name in ciq_wb.sheetnames:
        if name.strip().lower() == 'revision history':
            return name
    for name in ciq_wb.sheetnames:
        if 'revision' in name.lower():
            return name
    return None


def read_revision_history(ciq_wb):
    """Pulls columns A-E as a raw grid (the sheet mixes two stacked mini-
    tables, so no single header row is forced) — same approach as QUICKIX's
    extractRevisionHistory(). Returns (sheet_name, rows) where rows is a
    list of 5-value lists, blank rows dropped; (None, []) if no such sheet."""
    sheet_name = find_revision_history_sheet(ciq_wb)
    if not sheet_name:
        return None, []
    ws = ciq_wb[sheet_name]
    rows = []
    for row in ws.iter_rows(values_only=True):
        five = [row[i] if i < len(row) and row[i] is not None else '' for i in range(5)]
        if any(str(v).strip() != '' for v in five):
            rows.append(five)
    return sheet_name, rows
