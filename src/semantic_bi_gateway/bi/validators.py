"""Structural syntax validators for generated DAX, TMDL, and Power Query M scripts."""

from __future__ import annotations

import re
from collections.abc import Sequence

_SECRET_LITERAL_RE = re.compile(
    r"(?:password|pwd|secret|api_key|access_token)\s*=\s*['\"][^'\"]+['\"]",
    re.IGNORECASE,
)


def _strip_line_comments_and_strings(text: str, comment_prefix: str) -> tuple[str, list[str]]:
    """Scan code for quote balance and return code outside string literals and comments."""
    errors: list[str] = []
    out_chars: list[str] = []
    i = 0
    n = len(text)
    in_double = False
    in_single = False

    while i < n:
        ch = text[i]
        if not in_double and not in_single and text.startswith(comment_prefix, i):
            newline_pos = text.find("\n", i)
            if newline_pos == -1:
                break
            out_chars.append("\n")
            i = newline_pos + 1
            continue

        if ch == '"' and not in_single:
            if in_double and i + 1 < n and text[i + 1] == '"':
                i += 2
                continue
            in_double = not in_double
            i += 1
            continue

        if ch == "'" and not in_double:
            if in_single and i + 1 < n and text[i + 1] == "'":
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue

        if not in_double and not in_single:
            out_chars.append(ch)
        i += 1

    if in_double:
        errors.append("Unclosed double-quoted string literal.")
    if in_single:
        errors.append("Unclosed single-quoted identifier/string literal.")
    return "".join(out_chars), errors


def _check_balanced_delimiters(code_text: str, pairs: tuple[tuple[str, str], ...]) -> list[str]:
    errors: list[str] = []
    open_to_close = {op: cl for op, cl in pairs}
    close_to_open = {cl: op for op, cl in pairs}
    stack: list[str] = []

    for ch in code_text:
        if ch in open_to_close:
            stack.append(ch)
        elif ch in close_to_open:
            if not stack or stack[-1] != close_to_open[ch]:
                errors.append(f"Unmatched closing delimiter '{ch}'.")
                return errors
            stack.pop()

    if stack:
        errors.append(f"Unclosed opening delimiter '{stack[-1]}'.")
    return errors


def validate_dax_syntax(dax_text: str) -> tuple[bool, list[str]]:
    """Validate structural syntax of generated DAX measures file."""
    if not dax_text.strip():
        return False, ["DAX output is empty."]

    code_only, quote_errors = _strip_line_comments_and_strings(dax_text, "--")
    errors = list(quote_errors)
    errors.extend(_check_balanced_delimiters(code_only, (("(", ")"), ("[", "]"))))

    if "DEFINE" not in code_only or "MEASURE " not in code_only:
        errors.append("DAX output must contain DEFINE and MEASURE declarations.")

    for required_ti in ("TOTALYTD", "SAMEPERIODLASTYEAR", "DATEADD", "DATESINPERIOD"):
        if required_ti not in code_only:
            errors.append(f"Missing expected Time Intelligence DAX function '{required_ti}'.")

    if "/" in code_only:
        errors.append("Raw '/' division detected in DAX; must use safe DIVIDE(..., BLANK()).")

    return len(errors) == 0, errors


def validate_tmdl_syntax(tmdl_text: str) -> tuple[bool, list[str]]:
    """Validate structural syntax of generated PBIP TMDL measure table."""
    if not tmdl_text.strip():
        return False, ["TMDL output is empty."]

    errors: list[str] = []
    if not tmdl_text.startswith("table '_Measures'\n"):
        errors.append("TMDL output must start with \"table '_Measures'\".")

    expr_lines: list[str] = []
    measure_count = 0
    format_count = 0
    for line in tmdl_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("measure "):
            measure_count += 1
        elif stripped.startswith("formatString:"):
            format_count += 1
        elif stripped.startswith(("description:", "displayFolder:", "lineageTag:", "table ")):
            continue
        elif stripped:
            expr_lines.append(line)

    if measure_count == 0:
        errors.append("TMDL output contains zero measures.")
    if format_count != measure_count:
        errors.append(
            f"TMDL measure count ({measure_count}) mismatches formatString count ({format_count})."
        )

    code_only, quote_errors = _strip_line_comments_and_strings("\n".join(expr_lines), "//")
    errors.extend(quote_errors)
    errors.extend(_check_balanced_delimiters(code_only, (("(", ")"), ("[", "]"))))
    if "/" in code_only:
        errors.append("Raw '/' division detected in TMDL DAX expressions.")

    return len(errors) == 0, errors


def validate_power_query_syntax(
    pq_text: str,
    required_params: Sequence[str],
) -> tuple[bool, list[str]]:
    """Validate structural syntax, parameterization, and zero-secret rules for Power Query M."""
    if not pq_text.strip():
        return False, ["Power Query M output is empty."]

    if _SECRET_LITERAL_RE.search(pq_text):
        return False, ["Embedded credential literal detected in Power Query M script."]

    code_only, quote_errors = _strip_line_comments_and_strings(pq_text, "//")
    errors = list(quote_errors)
    errors.extend(_check_balanced_delimiters(code_only, (("(", ")"), ("[", "]"), ("{", "}"))))

    let_count = len(re.findall(r"\blet\b", code_only))
    in_count = len(re.findall(r"\bin\b", code_only))
    if let_count == 0 or let_count != in_count:
        errors.append(f"Unbalanced Power Query let/in blocks (let={let_count}, in={in_count}).")

    if "Table.TransformColumnTypes" not in code_only:
        errors.append("Power Query M output missing explicit Table.TransformColumnTypes contracts.")

    for param in required_params:
        if not re.search(rf"\b{re.escape(param)}\s*=", code_only):
            errors.append(f"Missing required Power Query parameter '{param}'.")

    return len(errors) == 0, errors
