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

Returns one of 'EXACT', 'NORMALIZED', 'SUFFIX', or 'NO MATCH' - never a
silent pass. Spot-check before trusting on an unfamiliar antenna family;
this has not been run against as many real sites as the numbered rules.
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

    return 'NO MATCH', f'"{ciq_value}" vs "{rfds_value}" — no tier matched.'
