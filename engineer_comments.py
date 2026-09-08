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
  4. Board Swaps                (board_type EXPECTED rows; From:/To: read
                                 real Pre board vs CIQ target, not EDP vs CIQ)
  5. Sector Movements           (sow['moved'], grouped by from/to node +
                                 SECTOR, combining every band sharing that
                                 sector+node pair into one line)
  6. Radio Swaps / Dual-Link    (AMOS RRU vs CIQ RRU differs; AMOS Dual-Link
                                 but CIQ Single-Link)

Retune comments are deliberately NOT generated (removed per instruction) —
sow['retuned'] is left unused here even though the data is available.

Each comment is {"text": str, "cls": str} exactly like the HTML's {text,cls}
pairs, so the same rendering/grouping/CR-Desc logic can reuse the "cls" tag.
"""
from band_labels import band_label, SECTOR_NAME


def _band_only(cell_name):
    band, _ = band_label(cell_name)
    return band


def build_engineer_comments(sow, results, checked_nodes, amos_lte_rows=None, amos_nr_rows=None,
                             ciq_lte_rows=None, ciq_nr_rows=None, node_logs_text=None):
    """Public entry point. Wraps _build_engineer_comments_inner() in a
    top-level try/except: this function runs unconditionally on every
    validation run (its result feeds CR Desc's auto-detected Nodes/Bands
    even when the Audit tab is never opened), so an unhandled exception
    anywhere inside it previously crashed the ENTIRE app on every tab, not
    just Audit — confirmed by a real Streamlit Cloud TypeError whose
    message was redacted, at the call site (not inside any specific
    sub-block), meaning the failure could have originated in ANY part of
    this function, including the Additions/Deletions/Radio-swap sections
    that predate this session's changes. On any failure, returns just the
    general note plus a visible error line, rather than taking the app
    down."""
    try:
        return _build_engineer_comments_inner(
            sow, results, checked_nodes, amos_lte_rows=amos_lte_rows, amos_nr_rows=amos_nr_rows,
            ciq_lte_rows=ciq_lte_rows, ciq_nr_rows=ciq_nr_rows, node_logs_text=node_logs_text,
        )
    except Exception as e:
        return [
            {"text": "PCI, delay, attenuation, RACH, power, BW, EARFCN DL/UL for existing sectors "
                     "should be as per PRE configuration.", "cls": ""},
            {"text": f"Engineer Comments could not be fully generated ({type(e).__name__}: {e}). "
                     f"Some scope-of-work lines may be missing.", "cls": ""},
        ]


def _build_engineer_comments_inner(sow, results, checked_nodes, amos_lte_rows=None, amos_nr_rows=None,
                                    ciq_lte_rows=None, ciq_nr_rows=None, node_logs_text=None):
    """sow: sow_analysis.classify_carriers() output.
    results: run_validation's results dict (uses results['board_type']).
    checked_nodes: list of node ids in scope for this run.
    amos_lte_rows/amos_nr_rows: amos_view.build_lte_cell_rows()/build_nr_cell_rows()
        output (Pre side) — used only for the Radio Swap / Dual-Link comparison.
    ciq_lte_rows/ciq_nr_rows: ciq_view.build_param_table() output for
        "eUtran Parameters"/"5G Info" (Post side) — same purpose.
    node_logs_text: {node_id: raw Pre log text} — used for the Board Swap
        comment's "From:" value (the real Pre-side board), replacing the
        EDP-sourced value check_board_type() itself uses for its own
        MATCH/MISMATCH verdict (that verdict is unaffected; only this
        comment's displayed From:/To: values now read Pre vs CIQ instead
        of EDP vs CIQ, per instruction).
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
    # reported elsewhere, not a scope-of-work comment). From:/To: read the
    # real Pre-side board (via node_logs_text) and the CIQ target
    # (r['ciq_du_type']) directly — NOT the EDP value check_board_type()
    # uses for its own status, per instruction that this comment should
    # reflect Pre vs CIQ. ──
    import pre_extract as pe
    import log_parser as lp

    def _pre_board_model(node_id):
        text = (node_logs_text or {}).get(node_id)
        if not text:
            return None
        boards = pe.extract_hardware(lp.parse_log(text)).get("boards") or []
        return pe.model_token(boards[0]["model"]) if boards else None

    # ── Board Swaps: triggered by comparing Pre (actual current hardware)
    # against CIQ (planned target) DIRECTLY — NOT by check_board_type()'s
    # own status, which compares EDP vs CIQ and can say MATCH even when a
    # real swap is needed. Confirmed real case: FCL04120's EDP had already
    # been updated to CIQ's target (both show 6672) while the Pre log still
    # showed the physical board as 5216 — check_board_type() correctly
    # reported MATCH (EDP's paperwork agrees with CIQ) and this function
    # used to trust that status, so it said "No Board Swap on FCL04120"
    # even though the Audit tab's own Pre-vs-Post table (a genuine Pre-vs-
    # CIQ comparison) correctly showed "Board Changed: 5216 -> 6672" for
    # the same node. EDP-vs-CIQ and Pre-vs-CIQ are answering different
    # questions ("has the paperwork caught up?" vs "does the hardware
    # match the target?"); a scope-of-work comment about an upcoming swap
    # needs the second one. ──
    for r in results.get("board_type", []):
        node = r.get("node")
        ciq_model = r.get("ciq_du_type")
        if not node or not ciq_model or ciq_model == "NOT FOUND":
            continue
        try:
            pre_model = _pre_board_model(node)
        except Exception:
            pre_model = None
        if pre_model is None:
            continue  # no Pre log for this node - nothing to compare, stay silent rather than guess
        if pre_model != ciq_model:
            comments.append({
                "text": f"Board Swap on {node} — From: {pre_model} To: {ciq_model}.",
                "cls": "board-comment",
            })
        else:
            comments.append({"text": f"No Board Swap on {node}", "cls": "board-comment"})

    # ── Sector Movements — group by (from_node, to_node, SECTOR), combining
    # every band that moved with the same sector letter between the same
    # two nodes into ONE line, e.g. '850_1/PCS_1/LTE_700/AWS_1/5G_850/WCS
    # Alpha sectors moving from X to Y node' instead of one line per band.
    # Previously grouped by band only (no sector shown at all) — sector
    # letter comes from band_label()'s own second return value, which the
    # cell name already carries; it just wasn't being read out before.
    #
    # Each entry is wrapped individually: a single malformed 'moved' row
    # (e.g. band_label() raising on an unexpected cell-name shape) should
    # skip that one row, not crash the whole function. ──
    move_groups = {}
    for m in sow.get("moved", []):
        try:
            cell = m.get("cell")
            band, sector = band_label(cell) if cell else (None, None)
            if not band or not sector:
                continue
            key = (m.get("from_node"), m.get("to_node"), sector)
            move_groups.setdefault(key, set()).add(band)
        except Exception:
            continue
    for (from_node, to_node, sector), bands in move_groups.items():
        try:
            band_str = "/".join(sorted(str(b) for b in bands if b))
            comments.append({
                "text": f"{band_str} {sector} sectors moving from {from_node} to {to_node} node.",
                "cls": "move-comment",
            })
        except Exception:
            continue

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
