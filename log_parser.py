"""
Parser for Ericsson moshell "Pre kget-all" / hget log files.

These logs are a transcript of an interactive moshell session: each command the
engineer typed is echoed back as `<NODE>> <command>`, followed by its output.
Most output blocks we care about are fixed-width tables of the form:

    =================================================================================================================
    MO                gNBId   gNBIdLength
    =================================================================================================================
    GNBCUCPFunction=1 5590877 26
    =================================================================================================================
    Total: 1 MOs

A single command can produce several such tables back-to-back (e.g. one hget
block per matching MO type). We parse every table under every command into a
list of dict rows, keyed by the header tokens.
"""
import re

_PROMPT_RE = re.compile(r'^(?P<node>[A-Za-z0-9_]+)>\s*(?P<cmd>.*)$')
_SEP_RE = re.compile(r'^=+\s*$')
_TOTAL_RE = re.compile(r'^Total:\s*(\d+)\s*MOs?\s*$', re.I)


def split_commands(text):
    """Split a full log into a list of (node_id, command, block_text) in order.

    A "command" starts at a `<NODE>> <command_text>` prompt line and its block
    runs until the next prompt line (or end of file). Non-command prompt lines
    (bare `<NODE>> ` with nothing after it) are skipped as segment boundaries
    but don't start a new named command.
    """
    segments = []
    current = None  # (node, cmd, lines)
    for raw_line in text.splitlines():
        line = raw_line.rstrip('\n')
        m = _PROMPT_RE.match(line.strip())
        if m and m.group('cmd'):
            if current is not None:
                segments.append((current[0], current[1], "\n".join(current[2])))
            current = (m.group('node'), m.group('cmd').strip(), [])
        else:
            if current is not None:
                current[2].append(line)
    if current is not None:
        segments.append((current[0], current[1], "\n".join(current[2])))
    return segments


def _column_spans(header_line):
    """Given a header line like 'Proxy  Adm State     Op. State     MO',
    return [(name, start, end), ...] column spans.

    Column boundaries are runs of 2+ spaces — moshell consistently pads
    between distinct columns with 2+ spaces, but multi-word column labels
    (e.g. 'Adm State', 'Op. State') use a single space internally. Splitting
    on every whitespace-delimited token (the naive approach) incorrectly
    treats 'Adm' and 'State' as separate columns and corrupts every row
    under a two-word header — confirmed against real 'st cell'/'st nrcell'
    output, which is the whole reason this uses a 2+-space boundary instead."""
    spans = []
    boundaries = [0] + [m.end() for m in re.finditer(r' {2,}', header_line)]
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else None
        segment = header_line[start:end] if end is not None else header_line[start:]
        name = segment.strip()
        if name:
            spans.append((name, start, end))
    return spans


def parse_tables(block_text):
    """Extract every fixed-width '===...header...===...rows...===...Total: N MOs'
    table found inside a command's output block. Returns a list of dicts:
    {'header': [...], 'rows': [{col: value, ...}, ...]}.

    Deliberately tolerant: a missing 'Total:' line (some blocks omit it) doesn't
    stop table extraction, since the next separator line reliably closes the row
    section either way.
    """
    lines = block_text.splitlines()
    tables = []
    i = 0
    n = len(lines)
    while i < n:
        if _SEP_RE.match(lines[i]):
            # Expect: sep, header, sep, rows..., sep, (optional 'Total: N MOs')
            if i + 2 < n and _SEP_RE.match(lines[i + 2]):
                header_line = lines[i + 1]
                if header_line.strip() and not _SEP_RE.match(header_line):
                    spans = _column_spans(header_line)
                    if spans:
                        row_start = i + 3
                        j = row_start
                        row_lines = []
                        while j < n and not _SEP_RE.match(lines[j]):
                            row_lines.append(lines[j])
                            j += 1
                        rows = []
                        for rl in row_lines:
                            if not rl.strip():
                                continue
                            row = {}
                            for name, start, end in spans:
                                val = rl[start:end] if end is not None else rl[start:]
                                row[name] = val.strip()
                            rows.append(row)
                        tables.append({
                            'header': [s[0] for s in spans],
                            'rows': rows,
                        })
                        i = j
                        continue
        i += 1
    return tables


def get_command_block(text, command_substr):
    """Return the raw (unparsed) output block text for the first command
    containing command_substr, or None. Used when a command's output isn't
    reliably fixed-width (see extract_nr_tac in pre_extract.py for why)."""
    sub = command_substr.lower()
    for node, cmd, block in split_commands(text):
        if sub in cmd.lower():
            return block
    return None


def parse_log(text):
    """Top-level entry point. Returns a list of:
        {'node': str, 'command': str, 'tables': [ {header, rows}, ... ]}
    one entry per command found in the transcript, in file order.
    """
    out = []
    for node, cmd, block in split_commands(text):
        tables = parse_tables(block)
        if tables:
            out.append({'node': node, 'command': cmd, 'tables': tables})
    return out


def find_command(parsed, command_substr):
    """Return the first parsed command entry whose command text contains the
    given substring (case-insensitive), or None."""
    sub = command_substr.lower()
    for entry in parsed:
        if sub in entry['command'].lower():
            return entry
    return None


def all_rows(command_entry, mo_prefix=None):
    """Flatten every row across every table for a parsed command entry into
    one list, optionally keeping only rows whose 'MO' column starts with
    mo_prefix (case-insensitive)."""
    rows = []
    if not command_entry:
        return rows
    for table in command_entry['tables']:
        for row in table['rows']:
            if mo_prefix is None or row.get('MO', '').upper().startswith(mo_prefix.upper()):
                rows.append(row)
    return rows
