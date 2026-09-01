"""
EDP Validator — per-node checklist logic, ported faithfully from QUICKIX's
embedded EDP Validator (runValidation()/evaluateNode() in the HTML tool).

Builds the expected node list from the CIQ (Mixed Mode Info -> Primary/
Secondary pairs per BBU Mode, Controller Info -> Controllers), looks each
one up in the EDP by SITE_NAME, and runs the same per-role checks the HTML
tool runs: cabinet naming, port size/facing, bearer VLAN pairing, IPv6
bearer/OAM addressing, PTP provisioning, and (for controllers) ANCEQ name/
model/IPv4 provisioning. Also runs the two cross-node checks (port reuse,
bearer VLAN reuse) and flags EDP rows that don't map back to any expected
node ("unexpected nodes").

All EDP column names below are the real EDP_SITE_ID/SITE_NAME-keyed export
columns (SIAD_PORT_FACING_BBU, BEARER_ENODEB_SB_VLAN_ID, etc.) — confirmed
present in a real published EDP export.
"""
import re

import ciq_edp_reader as cer

PASS, FAIL, WARN, INFO = "pass", "fail", "warn", "info"


def _is_blank(v):
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.lower() in ("nan", "none", "n/a")


def _norm(v):
    return "" if _is_blank(v) else str(v).strip()


def _num_eq(a, b):
    if _is_blank(a) or _is_blank(b):
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return _norm(a) == _norm(b)


def cabinet_info(cabinet_str):
    """{'kind': 'BBU'|'ANCILLARY'|'OTHER', 'num': str|None, 'is_v': bool}."""
    s = _norm(cabinet_str)
    m = re.match(r'^BBU\s*0*(\d+)\s*(V)?$', s, re.I)
    if m:
        return {"kind": "BBU", "num": m.group(1), "is_v": bool(m.group(2)), "raw": s}
    if re.match(r'^ANCILLARY', s, re.I):
        return {"kind": "ANCILLARY", "num": None, "is_v": False, "raw": s}
    return {"kind": "OTHER", "num": None, "is_v": False, "raw": s}


def build_expected_nodes(ciq_wb):
    """Same Primary/Secondary derivation as the HTML tool: for each Mixed
    Mode Info row, whichever of eNodeB/gNodeB Name matches 'Node to be
    built as' is Primary, the other is Secondary (unless BBU Mode is SMBB,
    which has no separate physical secondary unit). Controllers come from
    the Controller Info sheet."""
    nodes = []
    for idx, m in enumerate(cer.mixed_mode_rows(ciq_wb)):
        build_as = _norm(m.get("Node to be built as"))
        e_name = _norm(m.get("eNodeB Name"))
        g_name = _norm(m.get("gNodeB Name"))
        bbu_mode = _norm(m.get("BBU Mode")).upper()
        pair_id = f"pair{idx}"

        primary_name = primary_tech = secondary_name = secondary_tech = None
        if build_as and build_as.upper() == e_name.upper():
            primary_name, primary_tech = e_name, "LTE (eNodeB)"
            secondary_name, secondary_tech = g_name, "5G (gNodeB)"
        elif build_as and build_as.upper() == g_name.upper():
            primary_name, primary_tech = g_name, "5G (gNodeB)"
            secondary_name, secondary_tech = e_name, "LTE (eNodeB)"
        elif e_name and not g_name:
            primary_name, primary_tech = e_name, "LTE (eNodeB)"
        elif g_name and not e_name:
            primary_name, primary_tech = g_name, "5G (gNodeB)"
        else:
            primary_name, primary_tech = (build_as or e_name or g_name), "Unknown"

        if primary_name:
            nodes.append({"pair_id": pair_id, "role": "Primary", "tech": primary_tech,
                          "name": primary_name, "bbu_mode": bbu_mode})
        if secondary_name and bbu_mode != "SMBB":
            nodes.append({"pair_id": pair_id, "role": "Secondary", "tech": secondary_tech,
                          "name": secondary_name, "bbu_mode": bbu_mode})

    if "Controller Info" in ciq_wb.sheetnames:
        for idx, c in enumerate(cer.sheet_rows_as_dicts(ciq_wb["Controller Info"])):
            cid = _norm(c.get("Controller ID"))
            if cid:
                nodes.append({"pair_id": f"ctrl{idx}", "role": "Controller", "tech": "Controller",
                              "name": cid, "model": _norm(c.get("Controller"))})
    return nodes


def _field_group_check(label, rec, fields):
    missing = [f for f in fields if _is_blank(rec.get(f))]
    if not missing:
        return {"label": label, "status": PASS, "detail": " | ".join(f"{f}={rec.get(f)}" for f in fields)}
    return {"label": label, "status": FAIL, "detail": f"Missing: {', '.join(missing)}"}


def _ptp_check(rec):
    fields = ["SIAD_PTP_VLAN_ID", "PTP_VLAN_SUBNET_30", "PTP_SIAD_INTERFACE_IP", "PTP_CAB_INTERFACE_IP"]
    present = [f for f in fields if not _is_blank(rec.get(f))]
    if len(present) == len(fields):
        return {"label": "PTP provisioning", "status": INFO, "detail": "PTP fully configured on this node."}
    if present:
        return {"label": "PTP provisioning", "status": WARN,
                "detail": f"PTP partially configured ({len(present)}/4 fields) — verify intentional."}
    return {"label": "PTP provisioning", "status": INFO, "detail": "No PTP configured on this node."}


def _bearer_vlan_checks(node, all_nodes, edp_by_site, role):
    primary = next((n for n in all_nodes if n["pair_id"] == node["pair_id"] and n["role"] == "Primary"), None)
    secondary = next((n for n in all_nodes if n["pair_id"] == node["pair_id"] and n["role"] == "Secondary"), None)
    prim_rec = edp_by_site.get(primary["name"].upper()) if primary else None
    sec_rec = edp_by_site.get(secondary["name"].upper()) if secondary else None
    my_rec = prim_rec if role == "Primary" else sec_rec
    my_vlan = my_rec.get("BEARER_ENODEB_SB_VLAN_ID") if my_rec else None

    if _is_blank(my_vlan):
        return [{"label": "Bearer VLAN (BEARER_ENODEB_SB_VLAN_ID)", "status": FAIL, "detail": "Missing on this node."}]
    if not secondary:
        return [{"label": "Bearer VLAN (BEARER_ENODEB_SB_VLAN_ID)", "status": PASS,
                 "detail": f"{my_vlan} (standalone node, no pair to compare)"}]
    other_rec = sec_rec if role == "Primary" else prim_rec
    other_vlan = other_rec.get("BEARER_ENODEB_SB_VLAN_ID") if other_rec else None
    if _is_blank(other_vlan):
        return [{"label": "Bearer VLAN (BEARER_ENODEB_SB_VLAN_ID)", "status": WARN,
                 "detail": f"{my_vlan} present here, but paired node's EDP record is missing — cannot confirm no clash."}]
    if _num_eq(my_vlan, other_vlan):
        return [{"label": "Bearer VLAN (BEARER_ENODEB_SB_VLAN_ID)", "status": FAIL,
                 "detail": f"Clash — primary and secondary both use VLAN {my_vlan}."}]
    return [{"label": "Bearer VLAN (BEARER_ENODEB_SB_VLAN_ID)", "status": PASS,
             "detail": f"{my_vlan} (no clash with paired node's {other_vlan})"}]


def evaluate_node(node, all_nodes, edp_by_site):
    rec = edp_by_site.get(node["name"].upper())
    base = {"name": node["name"], "role": node["role"], "tech": node.get("tech"), "bbu_mode": node.get("bbu_mode")}
    if not rec:
        return {**base, "found": False, "status": "MISSING", "cabinet": None, "cabinet_id": None, "checks": []}

    checks = []
    cinfo = cabinet_info(rec.get("CABINET"))

    if node["role"] == "Primary":
        if cinfo["kind"] == "BBU" and not cinfo["is_v"]:
            checks.append({"label": "Cabinet naming", "status": PASS, "detail": f"{cinfo['raw']} (primary BBU slot, correct)"})
        else:
            checks.append({"label": "Cabinet naming", "status": FAIL,
                            "detail": f"Expected a primary \"BBU XX\" cabinet, found \"{cinfo['raw'] or '(blank)'}\""})

        port_size = _norm(rec.get("SIAD_PORT_SIZE_BBU"))
        bbu_mode = node.get("bbu_mode", "")
        if bbu_mode in ("TMBB", "MMBB"):
            if port_size.upper() == "10GE":
                checks.append({"label": "Port size (SIAD_PORT_SIZE_BBU)", "status": PASS, "detail": f"{port_size} — correct for {bbu_mode}"})
            else:
                checks.append({"label": "Port size (SIAD_PORT_SIZE_BBU)", "status": FAIL,
                                "detail": f"{bbu_mode} requires 10GE, found \"{port_size or '(blank)'}\""})
        elif bbu_mode == "SMBB":
            checks.append({"label": "Port size (SIAD_PORT_SIZE_BBU)", "status": WARN,
                            "detail": f"SMBB node — found \"{port_size or '(blank)'}\". Typically 1GE, confirm with requester."})
        else:
            checks.append({"label": "Port size (SIAD_PORT_SIZE_BBU)", "status": INFO,
                            "detail": f"BBU mode \"{bbu_mode or 'unknown'}\" — value found: \"{port_size or '(blank)'}\""})

        port_facing = _norm(rec.get("SIAD_PORT_FACING_BBU"))
        if port_facing:
            checks.append({"label": "Port facing (SIAD_PORT_FACING_BBU)", "status": PASS, "detail": port_facing})
        else:
            checks.append({"label": "Port facing (SIAD_PORT_FACING_BBU)", "status": FAIL,
                            "detail": "Missing — primary node must have a SIAD port assignment."})

        checks += _bearer_vlan_checks(node, all_nodes, edp_by_site, "Primary")
        checks.append(_field_group_check("IPv6 bearer addressing", rec,
                      ["IPV6_ENODEB_BEARER_SUBNET_61", "IPV6_ENODEB_SIAD_BEARER_SUB_64",
                       "IPV6_SIAD_BEARER_IP_DEF_ROUTER", "IPV6_ENODEB_BEARER_IP"]))
        checks.append(_field_group_check("IPv6 OAM addressing + OAM VLAN (primary only)", rec,
                      ["OAM_ENODEB_SIAD_OAM_VLAN", "IPV6_ENODEB_OAM_SUBNET_61", "IPV6_ENODEB_SIAD_OAM_SUB_64",
                       "IPV6_SIAD_OAM_IP_DEF_ROUTER", "IPV6_ENODEB_OAM_IP"]))
        checks.append(_ptp_check(rec))

    elif node["role"] == "Secondary":
        primary = next((n for n in all_nodes if n["pair_id"] == node["pair_id"] and n["role"] == "Primary"), None)
        prim_rec = edp_by_site.get(primary["name"].upper()) if primary else None
        if cinfo["kind"] == "BBU" and cinfo["is_v"]:
            if prim_rec:
                prim_cab = cabinet_info(prim_rec.get("CABINET"))
                if prim_cab["kind"] == "BBU" and prim_cab["num"] == cinfo["num"]:
                    checks.append({"label": "Cabinet naming", "status": PASS,
                                    "detail": f"{cinfo['raw']} (matches primary's BBU {prim_cab['num']})"})
                else:
                    checks.append({"label": "Cabinet naming", "status": FAIL,
                                    "detail": f"{cinfo['raw']} does not match primary cabinet number (BBU {prim_cab['num'] or '?'})"})
            else:
                checks.append({"label": "Cabinet naming", "status": WARN,
                                "detail": f"{cinfo['raw']} — could not confirm against primary (primary not found)"})
        else:
            checks.append({"label": "Cabinet naming", "status": FAIL,
                            "detail": f"Expected a \"BBU XXV\" cabinet, found \"{cinfo['raw'] or '(blank)'}\""})

        port_facing = _norm(rec.get("SIAD_PORT_FACING_BBU"))
        if not port_facing:
            checks.append({"label": "Port facing (SIAD_PORT_FACING_BBU)", "status": PASS,
                            "detail": "Blank, as expected — only the primary node carries this port."})
        else:
            checks.append({"label": "Port facing (SIAD_PORT_FACING_BBU)", "status": FAIL,
                            "detail": f"Unexpected value \"{port_facing}\" — should only be populated on the primary node."})

        checks += _bearer_vlan_checks(node, all_nodes, edp_by_site, "Secondary")
        checks.append(_field_group_check("IPv6 bearer addressing", rec,
                      ["IPV6_ENODEB_BEARER_SUBNET_61", "IPV6_ENODEB_SIAD_BEARER_SUB_64",
                       "IPV6_SIAD_BEARER_IP_DEF_ROUTER", "IPV6_ENODEB_BEARER_IP"]))
        checks.append(_ptp_check(rec))

    elif node["role"] == "Controller":
        if cinfo["kind"] == "ANCILLARY":
            checks.append({"label": "Cabinet naming", "status": PASS, "detail": f"{cinfo['raw']} (Ancillary, correct)"})
        else:
            checks.append({"label": "Cabinet naming", "status": FAIL,
                            "detail": f"Expected an \"ANCILLARY EQ\" cabinet, found \"{cinfo['raw'] or '(blank)'}\""})

        anc_name = _norm(rec.get("ANCEQ_NAME"))
        if anc_name.upper() == node["name"].upper():
            checks.append({"label": "ANCEQ_NAME matches Controller ID", "status": PASS, "detail": anc_name})
        else:
            checks.append({"label": "ANCEQ_NAME matches Controller ID", "status": FAIL,
                            "detail": f"Expected \"{node['name']}\", found \"{anc_name or '(blank)'}\""})

        node_model = _norm(rec.get("NODE_MODEL")) or _norm(rec.get("ANCEQ_TYPE"))
        model = node.get("model") or ""
        if model and model.upper() in node_model.upper():
            checks.append({"label": "Controller model match", "status": PASS,
                            "detail": f"CIQ model \"{model}\" found in \"{node_model}\""})
        else:
            checks.append({"label": "Controller model match", "status": FAIL,
                            "detail": f"CIQ model \"{model or '?'}\" not reflected in \"{node_model or '(blank)'}\""})

        checks.append(_field_group_check("Controller IPv4 provisioning", rec,
                      ["ANCEQ_TYPE", "ANCEQ_OAM_IP_POOL", "ANCEQ_SIAD_PORT", "ANCEQ_VLAN_ID",
                       "ANCEQ_OAM_SUBNET_IP", "ANCEQ_OAM_SUBNET_MASK", "ANCEQ_SIAD_IP_HOST_1", "ANCEQ_SIAD_IP_HOST_2"]))

        v6_fields = ["ANCEQ_OAM_SUBNET_IPV6", "ANCEQ_OAM_SUBNET_SIZE_IPV6", "ANCEQ_SIAD_IPV6_HOST_1", "ANCEQ_SIAD_IPV6_HOST_2"]
        v6_present = [f for f in v6_fields if not _is_blank(rec.get(f))]
        if v6_present:
            checks.append({"label": "Controller IPv6 addressing", "status": INFO,
                            "detail": f"IPv6 provisioned ({len(v6_present)}/4 fields populated) — verify expected."})
        else:
            checks.append({"label": "Controller IPv6 addressing", "status": INFO, "detail": "No IPv6 addressing provisioned."})

    has_fail = any(c["status"] == FAIL for c in checks)
    has_warn = any(c["status"] == WARN for c in checks)
    status = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")
    return {**base, "found": True, "status": status, "cabinet": _norm(rec.get("CABINET")),
            "cabinet_id": _norm(rec.get("CABINET_ID")), "checks": checks}


def run_global_checks(edp_rows, expected_names_upper):
    port_map = {}
    for r in edp_rows:
        device = _norm(r.get("SIAD_CLLI")) or "UNSPECIFIED-DEVICE"
        for f in ("SIAD_PORT_FACING_BBU", "ANCEQ_SIAD_PORT"):
            val = _norm(r.get(f))
            if not val:
                continue
            key = f"{device}::{val.upper()}"
            port_map.setdefault(key, {"device": device, "port": val, "sites": set()})
            port_map[key]["sites"].add(f"{_norm(r.get('SITE_NAME'))} ({f})")
    port_clashes = [v for v in port_map.values() if len(v["sites"]) > 1]

    vlan_map = {}
    for r in edp_rows:
        device = _norm(r.get("SIAD_CLLI")) or "UNSPECIFIED-DEVICE"
        vlan = _norm(r.get("BEARER_ENODEB_SB_VLAN_ID"))
        site = _norm(r.get("SITE_NAME"))
        if not vlan or not site:
            continue
        key = f"{device}::{vlan}"
        vlan_map.setdefault(key, {"device": device, "vlan": vlan, "sites": set()})
        vlan_map[key]["sites"].add(site)
    vlan_clashes = [v for v in vlan_map.values() if len(v["sites"]) > 1]

    return {"port_clashes": port_clashes, "vlan_clashes": vlan_clashes}


def run_edp_validation(ciq_wb, edp_rows):
    """Top-level entry point — mirrors the HTML tool's runValidation().
    Returns {'node_results': [...], 'global_checks': {...}, 'unexpected': [...]}."""
    edp_by_site = {}
    for r in edp_rows:
        key = _norm(r.get("SITE_NAME")).upper()
        if key:
            edp_by_site[key] = r

    nodes = build_expected_nodes(ciq_wb)
    node_results = [evaluate_node(n, nodes, edp_by_site) for n in nodes]
    global_checks = run_global_checks(edp_rows, {n["name"].upper() for n in nodes})

    expected_upper = {n["name"].upper() for n in nodes}
    seen, unexpected = set(), []
    for r in edp_rows:
        site = _norm(r.get("SITE_NAME"))
        if not site:
            continue
        up = site.upper()
        if up not in expected_upper and up not in seen:
            seen.add(up)
            unexpected.append({"site": site, "cabinet": _norm(r.get("CABINET")),
                               "model": _norm(r.get("NODE_MODEL")) or _norm(r.get("BBU_TYPE"))})

    return {"node_results": node_results, "global_checks": global_checks, "unexpected": unexpected}
