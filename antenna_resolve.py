"""
antenna_resolve.py

NEW module (not part of the original confirmed Blueprint rule set).
Resolves whether a CIQ antenna model string and an RFDS Port Level Details
vendor/model string refer to the same antenna, via three progressively
looser tiers - the same idea as the sibling HTML tool's antenna matching,
rebuilt here against this backend's plain-text RFDS extraction rather than
ported byte-for-byte (that tool's version works against pdf.js coordinate
data this backend doesn't have).

Tiers:
  1. EXACT      - same string, case/whitespace-insensitive.
  2. NORMALIZED - strip all non-alphanumeric characters, case-insensitive
                  substring match either direction (handles "NNH4-85B-R6"
                  vs "ANDREW/COMMSCOPE NNH4-85B-R6").
  3. SUFFIX     - compare only the base model token before the last
                  '-<variant>' suffix (handles "NNH4-65B-R6" vs
                  "NNH4-65B-R6A" style hardware-revision suffixes).
  4. TRUNCATED  - one side's normalized model string is a prefix of the
                  other's (handles CIQ cells that hard-truncate the model
                  at a fixed character width, e.g. CIQ "ROSENBERGER
                  CMA-BTLBHH-6516-2" vs RFDS "CMA-BTLBHH-6516-20-20" -
                  confirmed real case: CIQ column cuts the string to 18
                  chars mid-token, dropping the final "0-20"). Vendor
                  words are stripped first via _base_model's tokenizer so
                  "ROSENBERGER..." doesn't defeat the prefix check the way
                  it defeats the NORMALIZED tier's plain substring test.
                  Requires the shorter side to be >=6 chars (avoid false
                  positives on very short fragments) and the longer side
                  to extend the shorter by <=6 chars (avoid matching an
                  unrelated-but-coincidentally-prefixed model).

Returns one of 'EXACT', 'NORMALIZED', 'SUFFIX', 'TRUNCATED', or
'NO MATCH' - never a silent pass. Spot-check before trusting on an
unfamiliar antenna family; this has not been run against as many real
sites as the numbered rules.
"""
import re

_NON_ALNUM_RE = re.compile(r'[^A-Z0-9]')


def _norm(s):
    return _NON_ALNUM_RE.sub('', str(s or '').upper())


def _base_model(s):
    """Strip vendor words and a trailing '-<variant>' suffix, e.g.
    'ANDREW/COMMSCOPE NNH4-85B-R6' -> 'NNH4-85B'."""
    s = str(s or '').upper()
    # keep the token that looks most like a model code: contains a digit
    # and is at least 4 chars, prefer the last such token (vendor words
    # usually come first).
    tokens = re.split(r'[\s/]+', s)
    candidates = [t for t in tokens if len(t) >= 4 and any(c.isdigit() for c in t)]
    model = candidates[-1] if candidates else (tokens[-1] if tokens else '')
    return re.sub(r'-[A-Z0-9]{1,3}$', '', model)


def resolve_antenna(ciq_value, rfds_value):
    """Compare a CIQ antenna model/type string against an RFDS Port Level
    Details vendor_model string. Returns (tier, detail)."""
    ciq_value = (ciq_value or '').strip()
    rfds_value = (rfds_value or '').strip()
    if not ciq_value or not rfds_value:
        return 'NO MATCH', 'One side is blank.'

    if ciq_value.strip().upper() == rfds_value.strip().upper():
        return 'EXACT', f'"{ciq_value}" == "{rfds_value}".'

    nc, nr = _norm(ciq_value), _norm(rfds_value)
    if nc and nr and (nc in nr or nr in nc):
        return 'NORMALIZED', f'"{ciq_value}" ~ "{rfds_value}" after stripping punctuation/case.'

    bc, br = _base_model(ciq_value), _base_model(rfds_value)
    if bc and br and bc == br:
        return 'SUFFIX', f'Base model "{bc}" matches after dropping vendor words / variant suffix.'

    # TRUNCATED: compare the model tokens with vendor words already
    # stripped (via _base_model's tokenizer, before it trims the variant
    # suffix) so a hard-truncated CIQ cell can still prefix-match the
    # full RFDS model despite a leading vendor name defeating the plain
    # NORMALIZED substring test above.
    nbc, nbr = _norm(bc), _norm(br)
    if nbc and nbr:
        shorter, longer = (nbc, nbr) if len(nbc) <= len(nbr) else (nbr, nbc)
        if len(shorter) >= 6 and longer.startswith(shorter) and (len(longer) - len(shorter)) <= 6:
            return 'TRUNCATED', f'"{ciq_value}" vs "{rfds_value}" — one is a truncated prefix of the other.'

    return 'NO MATCH', f'"{ciq_value}" vs "{rfds_value}" — no tier matched.'
