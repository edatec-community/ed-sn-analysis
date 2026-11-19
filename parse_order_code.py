#!/usr/bin/env python3
"""Parse one ordering code or SN using data/draft/product_code_parser.csv."""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

TABLE_PATH = Path(__file__).resolve().parent / "data" / "draft" / "product_code_parser.csv"


@dataclass(frozen=True)
class Token:
    kind: str
    value: str = ""
    config_key: str | None = None
    options: tuple[str, ...] = ()
    blank_allowed: bool = False
    children: tuple["Token", ...] = ()
    regex_value: str | None = None


def parse_config_map(text: str) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for item in text.split(";"):
        if ":" not in item:
            continue
        key, values = item.split(":", 1)
        key = key.strip()
        options = tuple(v.strip() for v in values.split("/") if v.strip())
        if key and options:
            result[key] = options
    return result


def tokenize(pattern: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    for match in re.finditer(r"\{([^{}]+)\}", pattern):
        if match.start() > pos:
            tokens.append(Token("literal", pattern[pos:match.start()]))
        value = match.group(1)
        if "|" in value:
            tokens.append(Token("choice", options=tuple(x.strip() for x in value.split("|") if x.strip())))
        else:
            tokens.append(Token("field", value=value.strip()))
        pos = match.end()
    if pos < len(pattern):
        tokens.append(Token("literal", pattern[pos:]))
    return tokens


def adapt_example_prefix(tokens: list[Token], row: dict[str, str]) -> list[Token]:
    """Allow a two-digit model suffix to vary when the row's example shows that form."""
    if not tokens or tokens[0].kind != "literal":
        return tokens
    model = row["product_model"].upper()
    example = row["example_code"].upper()
    root_match = re.match(r"^(ED-[A-Z]+[0-9]+)", example)
    literal = tokens[0].value.upper()
    if not root_match or not model.endswith(tuple("0123456789")) or len(model) < 2:
        return tokens
    example_root = root_match.group(1)
    if not literal.startswith(model) or len(example_root) != len(model):
        return tokens
    if not model[-2:].isdigit() or not example_root[-2:].isdigit():
        return tokens
    if model[:-2] != example_root[:-2] or model[-2:] == example_root[-2:]:
        return tokens
    suffix = tokens[0].value[len(model):]
    sample_literal = root_match.group(1) + suffix
    regex_literal = re.escape(model[:-2]) + r"[0-9]{2}" + re.escape(suffix)
    return [Token("adaptive_literal", sample_literal, regex_value=regex_literal), *tokens[1:]]


def bind_tokens(tokens: list[Token], config: dict[str, tuple[str, ...]]) -> list[Token]:
    # The table represents the HMI3010 combined memory token across adjacent DDR/SD fields.
    combined: list[Token] = []
    i = 0
    while i < len(tokens):
        if ("combined_memory_token" in config and i + 1 < len(tokens)
                and tokens[i].kind == "field" and tokens[i].value == "ddr"
                and tokens[i + 1].kind == "field" and tokens[i + 1].value == "sd"):
            combined.append(Token("field", "combined_memory_token", "combined_memory_token",
                                  config["combined_memory_token"],
                                  any(v.casefold() == "blank" for v in config["combined_memory_token"])))
            i += 2
            continue
        combined.append(tokens[i])
        i += 1

    bound: list[Token] = []
    used: set[str] = set()
    aliases = {"ddr": "pi4_ddr"}
    for token in combined:
        if token.kind == "field":
            key = token.value if token.value in config else aliases.get(token.value)
            if key not in config:
                key = None
            if key:
                used.add(key)
                opts = config[key]
                bound.append(replace(token, config_key=key, options=opts,
                                     blank_allowed=any(v.casefold() == "blank" for v in opts)))
            else:
                bound.append(token)
        elif token.kind == "choice":
            choices = tuple(v.casefold() for v in token.options)
            matches = []
            for key, opts in config.items():
                if key in used:
                    continue
                nonblank = tuple(v.casefold() for v in opts if v.casefold() != "blank")
                if set(nonblank) == set(choices) and len(nonblank) == len(choices):
                    matches.append(key)
            priority = {"model": 0, "model_suffix": 1, "screen": 2}
            key = min(matches, key=lambda k: priority.get(k, 10)) if matches else None
            if key:
                used.add(key)
                bound.append(replace(token, config_key=key, options=config[key]))
            else:
                bound.append(token)
        else:
            bound.append(token)

    # Some table rows express a trailing SKU option only in the value map.
    for key in ("suffix", "codesys"):
        if key in config and key not in used:
            opts = config[key]
            values = tuple(v for v in opts if v.casefold() != "blank")
            if values:
                field = Token("field", key, key, values)
                bound.append(Token("optional", children=(Token("literal", "-"), field)))
                used.add(key)

    # A blank choice after a hyphen means the whole hyphen-plus-field segment may be absent.
    normalized: list[Token] = []
    i = 0
    while i < len(bound):
        token = bound[i]
        if (token.kind == "field" and token.blank_allowed and normalized
                and normalized[-1].kind == "literal" and normalized[-1].value.endswith("-")):
            previous = normalized.pop()
            prefix = previous.value[:-1]
            if prefix:
                normalized.append(replace(previous, value=prefix))
            nonblank = tuple(v for v in token.options if v.casefold() != "blank")
            field = replace(token, options=nonblank, blank_allowed=False)
            normalized.append(Token("optional", children=(Token("literal", "-"), field)))
        else:
            normalized.append(token)
        i += 1
    return normalized


FIXED_WIDTHS = {
    "sensor": 3, "shutter": 1, "color": 1, "focal": 2, "focal_length": 2,
    "lens": 1, "light": 1, "camera": 1, "wifi": 1, "poe": 1,
    "case": 1, "cooling": 1, "supercap": 1, "heatsink": 1,
    "tpm": 1, "5g": 3, "ddr": 2, "emmc": 2, "sd": 2,
}


def preferred_width(name: str) -> int:
    return FIXED_WIDTHS.get(name.casefold(), 1)


def infer_widths(tokens: Iterable[Token], example: str) -> dict[str, int]:
    """Use the example code to determine widths for fixed fields omitted from the map."""
    best: tuple[int, dict[str, int]] | None = None
    visits = 0

    def starts(text: str, pos: int, value: str) -> bool:
        return text[pos:pos + len(value)].casefold() == value.casefold()

    def walk(seq: tuple[Token, ...], index: int, pos: int,
             widths: dict[str, int], score: int) -> None:
        nonlocal best, visits
        visits += 1
        if visits > 25000:
            return
        if index == len(seq):
            if pos == len(example) and (best is None or score < best[0]):
                best = (score, dict(widths))
            return
        token = seq[index]
        if token.kind == "optional":
            walk(seq, index + 1, pos, widths, score)
            walk(token.children + seq[index + 1:], 0, pos, widths, score + 1)
            return
        if token.kind in ("literal", "adaptive_literal"):
            value = token.value
            if starts(example, pos, value):
                walk(seq, index + 1, pos + len(value), widths, score)
            return
        if token.kind == "choice":
            for value in sorted(token.options, key=len, reverse=True):
                if starts(example, pos, value):
                    walk(seq, index + 1, pos + len(value), widths, score)
            return
        if token.kind != "field":
            return
        if token.options:
            values = list(token.options)
            if token.blank_allowed:
                values.append("")
            for value in sorted(values, key=len, reverse=True):
                if starts(example, pos, value):
                    walk(seq, index + 1, pos + len(value), widths, score)
            return
        name = token.value
        if name in widths:
            lengths = [widths[name]]
        else:
            preferred = preferred_width(name)
            lengths = list(dict.fromkeys([preferred, 1, 2, 3, 4]))
        for width in lengths:
            value = example[pos:pos + width]
            if len(value) != width or not re.fullmatch(r"[A-Z0-9().]+", value, re.I):
                continue
            added = 0 if width == preferred_width(name) else 3 + abs(width - preferred_width(name))
            is_new = name not in widths
            widths[name] = width
            walk(seq, index + 1, pos + width, widths, score + (added if is_new else 0))
            if is_new:
                del widths[name]

    walk(tuple(tokens), 0, 0, {}, 0)
    return best[1] if best else {t.value: preferred_width(t.value) for t in tokens if t.kind == "field" and not t.options}


def compile_rule(tokens: list[Token], widths: dict[str, int]) -> tuple[re.Pattern[str], dict[str, str], int]:
    captures: dict[str, str] = {}
    group_number = 0

    def compile_sequence(seq: Iterable[Token]) -> str:
        nonlocal group_number
        parts: list[str] = []
        for token in seq:
            if token.kind == "literal":
                parts.append(re.escape(token.value))
            elif token.kind == "adaptive_literal":
                parts.append(token.regex_value or re.escape(token.value))
            elif token.kind == "choice":
                values = sorted(token.options, key=len, reverse=True)
                expression = "(?:" + "|".join(re.escape(v) for v in values) + ")"
                if token.config_key:
                    group = f"cfg{group_number}"
                    group_number += 1
                    captures[group] = token.config_key
                    parts.append(f"(?P<{group}>{expression})")
                else:
                    parts.append(expression)
            elif token.kind == "field":
                if token.config_key:
                    values = [v for v in token.options if v.casefold() != "blank"]
                    values = sorted(set(values), key=len, reverse=True)
                    alternatives = [re.escape(v) for v in values]
                    if token.blank_allowed:
                        alternatives.append("")
                    expression = "(?:" + "|".join(alternatives) + ")" if alternatives else ""
                    group = f"cfg{group_number}"
                    group_number += 1
                    captures[group] = token.config_key
                    parts.append(f"(?P<{group}>{expression})")
                else:
                    width = widths.get(token.value, preferred_width(token.value))
                    parts.append(r"[A-Z0-9().]{" + str(width) + "}")
            elif token.kind == "optional":
                parts.append("(?:" + compile_sequence(token.children) + ")?")
        return "".join(parts)

    expression = compile_sequence(tokens)
    literal_score = sum(len(t.value) for t in tokens if t.kind in ("literal", "adaptive_literal", "choice"))
    return re.compile(expression, re.I), captures, literal_score


def load_entries() -> list[dict[str, object]]:
    with TABLE_PATH.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {"product_model", "example_code", "ordering_code_pattern", "field_value_map_transcription"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("parser table is missing required columns or rows")
    entries: list[dict[str, object]] = []
    for row in rows:
        config = parse_config_map(row["field_value_map_transcription"])
        tokens = adapt_example_prefix(tokenize(row["ordering_code_pattern"]), row)
        tokens = bind_tokens(tokens, config)
        widths = infer_widths(tokens, row["example_code"])
        regex, captures, literal_score = compile_rule(tokens, widths)
        entries.append({"row": row, "regex": regex, "captures": captures,
                        "literal_score": literal_score})
    return entries


def parse_input(text: str, *, strict: bool = False) -> dict[str, object] | None:
    serial_match = re.search(r"\s+(ES[0-9]{9})\s*\Z", text, flags=re.I)
    if serial_match:
        text = text[:serial_match.start()]
    elif strict:
        # Preserve support for older scanner suffixes without returning them as SNs.
        text = re.sub(r"\s+ES[0-9]+\s*$", "", text, flags=re.I)
    # Scanners and copied SN data may insert spaces or line breaks inside the input.
    # Ordering codes themselves contain no whitespace, so search a compact copy.
    search_text = re.sub(r"\s+", "", text)
    candidates: list[tuple[int, int, int, int, dict[str, object]]] = []
    for entry in load_entries():
        regex = entry["regex"]
        captures = entry["captures"]
        row = entry["row"]
        matches = (regex.fullmatch(search_text),) if strict else regex.finditer(search_text)
        for match in matches:
            if match is None:
                continue
            config: dict[str, str] = {}
            for group, key in captures.items():
                value = match.group(group)
                if not value or value.casefold() == "blank":
                    continue
                config[key] = value.upper()
            result = {"product_model": row["product_model"], "configuration": config}
            if strict:
                result["order_code"] = match.group().upper()
            if serial_match:
                result["sn"] = serial_match.group(1).upper()
            candidates.append((match.end() - match.start(), int(entry["literal_score"]),
                               len(config), -match.start(), result))
    if not candidates:
        return None
    if strict and any(item[4] != candidates[0][4] for item in candidates):
        raise ValueError("ambiguous ordering code")
    candidates.sort(key=lambda item: item[:4], reverse=True)
    return candidates[0][4]


def main(argv: list[str]) -> int:
    strict = bool(argv and argv[0] == "--strict")
    if strict:
        argv = argv[1:]
    if len(argv) > 1:
        print(json.dumps({"error": "pass one ordering code/SN argument"}, ensure_ascii=False))
        return 2
    try:
        if argv:
            raw_input = argv[0]
        elif not sys.stdin.isatty():
            # The scanner wrapper sends arbitrary-length payloads over a pipe.
            raw_input = sys.stdin.read()
        else:
            print(json.dumps({"error": "expected ordering code/SN input"}, ensure_ascii=False))
            return 2
        result = parse_input(raw_input, strict=strict)
    except (OSError, csv.Error, ValueError, re.error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    if result is None:
        print(json.dumps({"error": "no matching ordering code found"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
