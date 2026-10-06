"""Minimal YAML-subset parser (stdlib only).

Supports exactly the subset TokenGuard config files use:
  - nested mappings via space indentation
  - block sequences ("- item")
  - flow sequences ("[a, b, c]")
  - scalars: null / bool / int / float, single-quoted, double-quoted,
    and plain strings
  - "#" comments outside quotes

Anything else raises ValueError with a clear message. This is intentionally
not a general YAML parser.
"""


def loads(text):
    """Parse YAML-subset text into Python objects."""
    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if stripped.strip() == "":
            continue
        leading = stripped[: len(stripped) - len(stripped.lstrip(" "))]
        if "\t" in leading:
            raise ValueError("tabs are not allowed for indentation")
        indent = len(leading)
        lines.append((indent, stripped.strip()))
    if not lines:
        return {}
    value, next_i = _parse_block(lines, 0, lines[0][0])
    if next_i != len(lines):
        raise ValueError(
            "unexpected content at line: %r" % (lines[next_i][1],)
        )
    return value


def _parse_block(lines, i, indent):
    first = lines[i][1]
    if first == "-" or first.startswith("- "):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_seq(lines, i, indent):
    items = []
    while i < len(lines) and lines[i][0] == indent:
        content = lines[i][1]
        if not (content == "-" or content.startswith("- ")):
            raise ValueError(
                "expected '- item' in sequence, got: %r" % (content,)
            )
        item_text = "" if content == "-" else content[2:].strip()
        if item_text == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            else:
                value, i = None, i + 1
        else:
            value, i = _parse_value(item_text), i + 1
        items.append(value)
    return items, i


def _parse_map(lines, i, indent):
    result = {}
    while i < len(lines) and lines[i][0] == indent:
        content = lines[i][1]
        if content == "-" or content.startswith("- "):
            raise ValueError(
                "sequence item inside mapping at: %r" % (content,)
            )
        key, sep, rest = _partition_key(content)
        if not sep:
            raise ValueError("expected 'key: value', got: %r" % (content,))
        key = _parse_scalar(key.strip())
        if not isinstance(key, str):
            key = str(key)
        rest = rest.strip()
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1][0] > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1][0])
            else:
                value, i = None, i + 1
        else:
            value, i = _parse_value(rest), i + 1
        if key in result:
            raise ValueError("duplicate key: %r" % (key,))
        result[key] = value
    return result, i


def _partition_key(content):
    """Split on the first ':' that is followed by space or end-of-line,
    ignoring colons inside quotes (e.g. URLs)."""
    quote = None
    for idx, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ":" and (
            idx + 1 == len(content) or content[idx + 1] in " \t"
        ):
            return content[:idx], ":", content[idx + 1 :]
    return content, "", ""


def _parse_value(text):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if inner == "":
            return []
        return [_parse_scalar(part) for part in _split_flow(inner)]
    return _parse_scalar(text)


def _split_flow(text):
    """Split a flow sequence on top-level commas, respecting quotes and
    nested brackets."""
    parts = []
    depth = 0
    quote = None
    current = []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "[{":
            depth += 1
            current.append(ch)
        elif ch in "]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _parse_scalar(text):
    text = text.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        if text[0] == '"':
            inner = (
                inner.replace("\\\\", "\\")
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
            )
        return inner
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _strip_comment(line):
    """Remove a '#' comment that appears outside quotes."""
    out = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                out.append(line[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()
