"""
RFDS Verification summary — one row per (mismatch type, band), each row
naming the affected sector(s) and a fixed corrective-action line. This is
the Python port of QUICKIX_Pre-Script_Validation.html's dashGetSectorLetter
/ dashSectorPhrase / RFDS_VERIFICATION_TEMPLATES / buildRfdsVerificationSummary,
so the Consolidated Report's "Warnings & Comments" section shows the same
grouped, banded findings the HTML tool shows — not a flat per-cell list.

Input: the per-cell rows already produced by build_rfds_grouped_rows() in
"Streamlit app.py" (cell_status / rru_status / ant_status / cellid_status /
ant_info_status / losses_status, each MATCH/MISMATCH/MANUAL/SKIPPED, plus
cell_ciq / cell_rfds as the cell name). Nothing here re-derives RFDS/CIQ
data — it only groups and labels what build_rfds_grouped_rows already
computed, exactly like the HTML's summary sits on top of its own per-cell
report array.
"""
from band_labels import band_label, SECTOR_NAME


# ── Sector-letter / phrase helpers (mirrors dashGetSectorLetter / dashSectorPhrase) ──

def _sector_letter(cell_name):
    """band_labels.band_label() already resolves the sector *name* (Alpha,
    Beta, ...) for a cell; invert SECTOR_NAME to get back the single-letter
    key the grouping logic below keys on."""
    _, sector_name = band_label(cell_name)
    if not sector_name:
        return ""
    for letter, name in SECTOR_NAME.items():
        if name == sector_name:
            return letter
    return ""


def _sector_phrase(band, group_cells, universe_cells):
    """Builds the "<band> Sectors" / "<band> Alpha Sector" / "<band> Alpha &
    Beta Sectors" phrase: when every sector that exists for this band (per
    the full RFDS-vs-CIQ report, matched or not) is present in the mismatch
    group, the phrase stays generic ("<band> Sectors"); when only some
    sector(s) are affected, those sector name(s) are called out."""
    group_letters = {l for l in (_sector_letter(c) for c in group_cells) if l}
    universe_letters = {l for l in (_sector_letter(c) for c in universe_cells) if l}
    all_affected = (
        group_letters and universe_letters
        and universe_letters == group_letters
    )
    if not group_letters or all_affected:
        return f"{band} Sectors"
    names = [SECTOR_NAME.get(l, l) for l in sorted(group_letters)]
    unit = "Sectors" if len(names) > 1 else "Sector"
    return f"{band} {' & '.join(names)} {unit}"


# ── Fixed Finding/Corrective-Action templates (mirrors RFDS_VERIFICATION_TEMPLATES) ──
# Ordered the same way the HTML iterates RFDS_VERIFICATION_ORDER, so rows
# render in the same sequence: Cell Not Found, RRH, Antenna, CellID.

_TEMPLATES = {
    "Cell Not Found": {
        "label": lambda phrase: f"{phrase} missing in the CIQ.",
        "action": "Verify the Scope in revision history.",
    },
    "RRH": {
        "label": lambda phrase: f"RRU mismatch on the: {phrase}",
        "action": "Raise a pre integration issue mail.",
    },
    "Antenna": {
        "label": lambda phrase: f"Antenna mismatch on: {phrase}",
        "action": "Raise a pre integration issue mail.",
    },
    "CellID": {
        "label": lambda phrase: f"cell id mismatch on the: {phrase}",
        "action": "Raise a pre integration issue mail.",
    },
}
_ORDER = ["Cell Not Found", "RRH", "Antenna", "CellID"]

# Which grouped-row status field identifies each mismatch category, and
# what counts as "affected" for that field.
_STATUS_FIELD = {
    "Cell Not Found": "cell_status",
    "RRH": "rru_status",
    "Antenna": "ant_status",
    "CellID": "cellid_status",
}


def build_rfds_verification_summary(grouped_rows):
    """grouped_rows: the list produced by build_rfds_grouped_rows() in
    "Streamlit app.py". Returns a list of {"Finding": ..., "Corrective
    Action": ...} dicts, one per (mismatch type, band) that has at least
    one MISMATCH row — ready for render_table()."""
    if not grouped_rows:
        return []

    # Universe of cells per band, from every row build_rfds_grouped_rows
    # produced (matched + mismatched) — used to tell whether a mismatch
    # group covers every sector of that band.
    def _cell_name(row):
        """The real cell name for banding/sector purposes — cell_ciq is
        "NOT FOUND" precisely on Cell Not Found rows, so fall back to
        cell_rfds (which still holds the actual cell name) in that case."""
        for v in (row.get("cell_ciq"), row.get("cell_rfds")):
            if v and v not in ("—", "NOT FOUND"):
                return v
        return None

    universe_by_band = {}
    for row in grouped_rows:
        cell = _cell_name(row)
        if not cell:
            continue
        band, _ = band_label(cell)
        if not band:
            continue
        universe_by_band.setdefault(band, set()).add(cell)

    # Group mismatched cells by (param, band) -> set of cell names.
    groups = {}
    for param in _ORDER:
        status_field = _STATUS_FIELD[param]
        for row in grouped_rows:
            if row.get(status_field) != "MISMATCH":
                continue
            cell = _cell_name(row)
            if not cell:
                continue
            band, _ = band_label(cell)
            if not band:
                continue
            key = (param, band)
            groups.setdefault(key, set()).add(cell)

    out = []
    for param in _ORDER:
        tpl = _TEMPLATES[param]
        matching_keys = sorted((k for k in groups if k[0] == param), key=lambda k: k[1])
        for _, band in matching_keys:
            cells = groups[(param, band)]
            universe = universe_by_band.get(band, cells)
            phrase = _sector_phrase(band, cells, universe)
            out.append({"Finding": tpl["label"](phrase), "Corrective Action": tpl["action"]})
    return out
