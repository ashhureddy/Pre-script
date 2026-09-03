"""
Engineer Comments — the auto-generated narrative QUICKIX_Pre-Script_Validation.html
builds in its Audit tab (generateFinalComments()) and reuses inside CR Desc
(extractBandsFromComments() / extractNodesFromAudit()). This is the Python
port for the Streamlit app, built on top of this project's OWN pre-computed
diff — sow_analysis.classify_carriers() (which reads the CIQ's own "Sector
Del_Movement" sheet directly) plus results['board_type'] — rather than
re-deriving Pre-vs-CIQ cell matching from scratch the way the HTML tool
does with fuzzy suffix matching against Pre kget-all logs.

Categories (same as the HTML, same order):
  1. General note              (PCI/delay/attenuation/... should match PRE)
  2. Additions                 (new node / new band-sectors in existing node)
  3. Deletions                 (deleted node)
  4. Board Swaps                (board_type EXPECTED rows)
  5. Sector Movements           (sow['moved'], grouped by from/to node + band)
  6. Radio Swaps / Dual-Link    (AMOS RRU vs CIQ RRU differs; AMOS Dual-Link
                                 but CIQ Single-Link)

Each comment is {"text": str, "cls": str} exactly like the HTML's {text,cls}
pairs, so the same rendering/grouping/CR-Desc logic can reuse the "cls" tag.
"""
from band_labels import band_label, SECTOR_NAME


def _band_only(cell_name):
    band, _ = band_label(cell_name)
    return band


def build_engineer_comments(sow, results, checked_nodes, amos_lte_rows=None, amos_nr_rows=None,
                             ciq_lte_rows=None, ciq_nr_rows=None):
    """sow: sow_analysis.classify_carriers() output.
    results: run_validation's results dict (uses results['board_type']).
    checked_nodes: list of node ids in scope for this run.
    amos_lte_rows/amos_nr_rows: amos_view.build_lte_cell_rows()/build_nr_cell_rows()
        output (Pre side) — used only for the Radio Swap / Dual-Link comparison.
    ciq_lte_rows/ciq_nr_rows: ciq_view.build_param_table() output for
        "eUtran Parameters"/"5G Info" (Post side) — same purpose.
    Returns a list of {"text": str, "cls": str} dicts, in the same category
    order the HTML tool uses.
    """
    comments = []

    comments.append({
        "text": "PCI, delay, attenuation, RACH, power, BW, EARFCN DL/UL for existing sectors "
                "should be as per PRE configuration.",
        "cls": "",
    })

    # ── Additions: new nodes get their own line, then their bands; nodes
    # that already existed just get a per-band "Adding <band> sector(s)". ──
    added = sow.get("added", {}) or {}
    new_node_set = {n for n in added if n not in checked_nodes or n not in (sow.get("deleted_nodes") or [])}
    # A node counts as genuinely NEW (not just gaining sectors) when it has
    # no board_type row at all in results — i.e. this run's node list came
    # entirely from CIQ Mixed Mode Info with nothing to compare against.
    board_nodes = {r.get("node") for r in results.get("board_type", [])}
    for node, cells in added.items():
        bands = sorted({b for b in (_band_only(c) for c in cells) if b}, key=str)
        is_new_node = node not in board_nodes
        if is_new_node:
            comments.append({"text": f"Adding {node} node.", "cls": "add-comment"})
        if bands:
            noun = "sector" if len(bands) == 1 else "sectors"
            comments.append({
                "text": f"Adding {' / '.join(bands)} {noun} in {node} node.",
                "cls": "add-comment",
            })

    # ── Deletions ──
    deleted_nodes = sow.get("deleted_nodes") or []
    if deleted_nodes:
        noun = "node" if len(deleted_nodes) == 1 else "nodes"
        comments.append({
            "text": f"Deleted the {' / '.join(deleted_nodes)} {noun}.",
            "cls": "del-comment",
        })
    for node, cells in (sow.get("deleted_sectors") or {}).items():
        bands = sorted({b for b in (_band_only(c) for c in cells) if b}, key=str)
        if bands:
            comments.append({
                "text": f"Deleting {' / '.join(bands)} sector(s) from {node} node.",
                "cls": "del-comment",
            })

    # ── Board Swaps (planned/EXPECTED only — a real MISMATCH is a fault,
    # reported elsewhere, not a scope-of-work comment) ──
    for r in results.get("board_type", []):
        if r.get("status") == "EXPECTED":
            comments.append({
                "text": f"Board Swap on {r.get('node')} — {r.get('note', '').split('RFDS')[0].strip()}",
                "cls": "board-comment",
            })
    for r in results.get("board_type", []):
        if r.get("status") == "MATCH":
            comments.append({"text": f"No Board Swap on {r.get('node')}", "cls": "board-comment"})

    # ── Sector Movements — group by (from_node, to_node, band), same as the
    # HTML's lteMoveMap/nrMoveMap grouping (sector letter isn't tracked by
    # classify_carriers()'s "moved" entries, so bands are grouped without a
    # named sector — still says "delete <from> node" as a hint like the HTML). ──
    move_groups = {}
    for m in sow.get("moved", []):
        band = _band_only(m.get("cell"))
        if not band:
            continue
        key = (m.get("from_node"), m.get("to_node"), band)
        move_groups.setdefault(key, 0)
        move_groups[key] += 1
    for (from_node, to_node, band), _count in move_groups.items():
        comments.append({
            "text": f"{band} sectors moving from {from_node} to {to_node} node "
                    f"(Sector Movement — delete {from_node} node).",
            "cls": "move-comment",
        })

    # ── Retunes (this project's own extra signal — not in the HTML tool,
    # but genuinely useful scope-of-work info the CIQ's Sector Del_Movement
    # sheet already gives us for free) ──
    for r in sow.get("retuned", []):
        label = r.get("label") or "Unknown band"
        comments.append({
            "text": f"{label} sector retuned: {r.get('from')} -> {r.get('to')}.",
            "cls": "",
        })

    # ── Radio Swap / Dual-Link mismatch: compare Pre (AMOS) RRU model per
    # cell against CIQ RRU model for the same cell suffix. Only meaningful
    # when both AMOS and CIQ rows were supplied. ──
    def _suffix(cell):
        parts = str(cell or "").split("_")
        return "_".join(parts[1:]).upper() if len(parts) > 1 else ""

    def _radio_swap_pass(amos_rows, ciq_rows, ciq_cell_key, ciq_rru_key, nr=False):
        if not amos_rows or not ciq_rows:
            return
        ciq_by_suffix = {}
        for r in ciq_rows:
            cell = r.get(ciq_cell_key)
            if cell:
                ciq_by_suffix.setdefault(_suffix(cell), []).append(r)
        swap_bands = {}
        for a in amos_rows:
            cell_name = a.get("cell")
            sfx = _suffix(cell_name)
            matches = ciq_by_suffix.get(sfx)
            if not matches:
                continue
            c = matches[0]
            # amos rows use "radio_type" (resolved model, e.g. "RRUS 32") for
            # LTE — comparable to CIQ's "RRU type"/"RRU Type" model column;
            # NR rows only carry "rru" (the raw FRU id, e.g. "RRU-7"), since
            # build_nr_cell_rows() doesn't resolve a short model name.
            a_rru = str((a.get("radio_type") if not nr else a.get("rru")) or "").strip().upper()
            c_rru = str(c.get(ciq_rru_key) or "").strip().upper()
            if a_rru and c_rru and a_rru != c_rru:
                band = _band_only(cell_name) or sfx
                key = (a_rru, c_rru)
                swap_bands.setdefault(key, set()).add(band)
        for (pre_rru, post_rru), bands in swap_bands.items():
            bl = " / ".join(sorted(bands))
            prefix = "NR " if nr else ""
            comments.append({
                "text": f"{prefix}Radio swap pending for {bl} ({pre_rru} \u2192 {post_rru}).",
                "cls": "swap-comment",
            })

    _radio_swap_pass(amos_lte_rows, ciq_lte_rows, "EutranCellFDDId", "RRU type", nr=False)
    # NR radio-swap comparison is skipped: build_nr_cell_rows() only exposes
    # the raw FRU id (e.g. "RRU-7"), not a resolved model name, so comparing
    # it against CIQ's "RRU Type" (a model name like "RRUS 4890") would
    # mismatch on every row and produce false "radio swap" comments.

    return comments


def extract_bands_from_comments(comments):
    """Mirrors the HTML's extractBandsFromComments(): bands named in
    add-comment lines go into CONFIG_UPDATE, EXCEPT when that same band also
    appears in a move-comment or swap-comment (a relocation/swap isn't new
    scope). Very small, deliberately dumb band-name matcher — same
    known-bands list the HTML hardcodes."""
    known_bands = ["5G_AWS", "5G_PCS", "5G_850", "C-BAND", "DOD", "DOD_3", "AWS", "AWS_2", "AWS_3",
                   "PCS", "PCS_2", "PCS_3", "700", "WCS", "F-NET", "B-29", "LTE_850", "LTE_850_2",
                   "5G_850", "MMWAVE", "DOD_BWE", "FNET", "LTE_700", "LTE_700_E"]
    band_set, exclude_set = set(), set()
    for c in comments:
        text = (c.get("text") or "").upper()
        if not text:
            continue
        found = [b for b in known_bands if b.upper() in text]
        if c.get("cls") == "add-comment":
            band_set.update(found)
        elif c.get("cls") in ("move-comment", "swap-comment"):
            exclude_set.update(found)
    return sorted(band_set - exclude_set)


def extract_nodes_from_audit(sow, checked_nodes):
    """Mirrors the HTML's extractNodesFromAudit(): every node in scope,
    deleted ones prefixed 'Delete_' and listed after the regular nodes."""
    deleted = sow.get("deleted_nodes") or []
    regular = [n for n in checked_nodes if n not in deleted]
    return regular + [f"Delete_{n}" for n in deleted], deleted, regular
