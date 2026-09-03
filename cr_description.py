"""
CR Description string + Radio/RET email builder — Python port of
QUICKIX_Pre-Script_Validation.html's generateCRDescription() and
buildRadioRetEmail(), driven by engineer_comments.py's output instead of
the HTML's in-browser STORE.
"""


def build_cr_description(mic_mca, site_name, fa_number, all_nodes, bands):
    """mic_mca: 'MIC - MCA' or 'MCA - CRAN' (rendered with ' | ' instead of
    ' - ' between the two halves, same as the HTML's micMcaStr).
    Returns (cr_string, breakdown_rows) or (None, None) if required fields
    are missing."""
    if not site_name or not fa_number or not all_nodes:
        return None, None
    mic_mca_str = " | ".join(p.strip() for p in mic_mca.split(" - "))
    node_str = "/".join(all_nodes)
    band_str = ("CONFIG_UPDATE_" + "/".join(bands)) if bands else "CONFIG_UPDATE"
    cr = f"{mic_mca_str} | {site_name.strip().upper()} | FA {fa_number} | {node_str} | {band_str} - RSF"

    regular_nodes = [n for n in all_nodes if not n.startswith("Delete_")]
    delete_nodes = [n for n in all_nodes if n.startswith("Delete_")]
    breakdown = [
        ("MIC | MCA", mic_mca_str),
        ("Site Name", site_name.strip().upper()),
        ("FA Number", f"FA {fa_number}"),
        ("Regular Nodes", " / ".join(regular_nodes) or "\u2014"),
        ("Delete Nodes", " / ".join(delete_nodes) or "None"),
        ("Bands", " / ".join(bands) or "\u2014"),
        ("Suffix", "RSF"),
    ]
    return cr, breakdown


def build_radio_ret_email(sw_version, fa_number, link, comments):
    """comments: list of {"text": ...} dicts from build_engineer_comments()."""
    radio_lines = "\n".join(f"\u2022 {c['text']}" for c in comments) if comments else "\u2022 \u2014"
    return (
        "Team,\n\n"
        "PFB Radio/RET/External alarm/Controller script Comments (Lab/Non Lab site)\n\n"
        f"Link: {link or ''}\n\n"
        f"Sw Version : {sw_version or ''}\n"
        f"FA Code : {fa_number or ''}\n"
        f"Radio comments:\n{radio_lines}\n"
        "RET comments:\n"
    )
