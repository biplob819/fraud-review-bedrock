"""Policy as code (Tech Spec §4.1, D-6).

`policy.md` holds numbered clauses: a `## P<n>.<m> Title` heading, plain-language prose, and one
fenced YAML block with `id`, `kind` (signal, clear, no_clear), `fields` and a `when` predicate.

`policy.md` is a deliberately small subset of Markdown, so that every renderer (GitHub's
cmark-gfm, markdown-it, an editor preview) shows the same clauses the router checks. Outside
code blocks a line is blank, a `# Title` (before the clauses), a `## P<n>.<m> Title` clause
heading, or unindented prose (paragraphs and simple list items). Indentation, other headings,
underline headings and rules, block quotes, tables, link and footnote definitions, and the
characters `<` (raw HTML), `&` (character references) and `\\` (escapes) are refused, as are
invisible, control and default-ignorable characters. The file is then parsed as CommonMark and
the parse must agree with the line scan on every clause heading and code block; each clause
holds exactly one ```yaml block and no other code block.

Predicates are parsed with `ast` against a node allow-list, type-checked against the alert
fields at load time, and evaluated by walking the tree; nothing is executed. The router (A2.2)
uses `clearing_allowed`; the evaluation (E2.2) uses `unknown_clauses`; prompts use
`prompt_text`, which drops the YAML.

Predicates read a validated `AlertCase`, so every value has its schema type. The one value that
can be missing is `amt_vs_usual` (a card with almost no history); a non-finite number is treated
as missing too. Missing values fail safe: a clear clause does not hold, a no-clear clause holds,
and a signal does not hold.
"""

from __future__ import annotations

import ast
import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, get_args

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from fraudreview.config import StrictLoader
from fraudreview.schemas import CLAUSE_RE, FILLERS, AlertCase, Category, FeatureName

Kind = Literal["signal", "clear", "no_clear"]
FieldType = Literal["number", "bool", "category"]

# The type of every field a predicate may read, from the AlertCase schema.
FIELD_TYPES: Mapping[str, FieldType] = MappingProxyType(
    {
        "amt_vs_usual": "number",
        "hour": "number",
        "category_freq": "number",
        "merchant_first_seen": "bool",
        "card_history_n": "number",
        "amount": "number",
        "category": "category",
        "risk_score": "number",
    }
)
assert set(FIELD_TYPES) == set(get_args(FeatureName)), "FIELD_TYPES must cover FeatureName"
CATEGORIES = frozenset(get_args(Category))

HEADING_RE = re.compile(r"(P[1-9][0-9]{0,2}\.(?:0|[1-9][0-9]{0,2}))[ \t]+(\S.*)")  # no leading 0
DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "policy.md"

# The default nesting limit (20) makes markdown-it silently drop deeper content that GitHub
# still renders; the line scan refuses nesting anyway, and a high limit keeps both readings whole.
_MD = MarkdownIt("commonmark", {"maxNesting": 1000})
_REFUSED_BLOCKS = {
    "blockquote_open": "a block quote",
    "html_block": "raw HTML",
    "code_block": "an indented code block",
}
_HIDDEN_CATEGORIES = {"Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp"}
_HIDDEN_CHARS = (
    set(FILLERS)
    | {chr(c) for c in range(0xFE00, 0xFE10)}  # variation selectors
    | {chr(c) for c in range(0xE0100, 0xE01F0)}  # variation selectors supplement
    | {chr(c) for c in range(0x180B, 0x1810)}  # Mongolian selectors and vowel separator
    | {"\u034f", "\u17b4", "\u17b5", "\u2800"}  # grapheme joiner, Khmer inherent vowels, blank
)
_FENCE_OPEN = re.compile(r"(`{3,}|~{3,})")
# Outside code blocks a line may use only these characters. Everything a renderer can turn into
# hidden, struck, linked or rendered-differently text (`<`, `&`, `|`, `\`, `~`, `[`, `]`, `` ` ``,
# `$`, `{`, `}`, `^`, `=`, `@`, non-ASCII) is outside the set, so no renderer extension can apply.
_PROSE_CHARS = re.compile(r"[A-Za-z0-9 .,;:'\"?!()/%*_#+\-]*")
_CHAR_REASON = {
    "<": "raw HTML",
    "&": "character references",
    "|": "tables",
    "\\": "escapes",
    "~": "strikethrough",
    "[": "links, footnotes and images",
    "]": "links, footnotes and images",
    "`": "code spans",
    "$": "math",
}
# anything a reader might take for a clause id
_CLAUSE_LIKE = re.compile(r"(?<![A-Za-z])[Pp][^A-Za-z0-9]{0,6}[0-9]")
_CLAUSE_EXACT = re.compile(r"P[0-9]{1,3}\.[0-9]{1,3}(?![0-9.]*[0-9])")
# A yaml block is exactly four short lines, so nothing can sit off-screen in GitHub's
# non-wrapping code block or fold across lines.
_YAML_LINE = re.compile(r"(id|kind|fields|when): [^\s&*!'\"|>](?:[^\t]*\S)?")  # plain values
MAX_YAML_LINE = 100
_WITHDRAWN = re.compile(
    r"\b(withdrawn|repealed|superseded|void|revoked|suspended|obsolete|deprecated|disabled|"
    r"inactive|retired|cancelled|canceled|removed|replaced|rescinded|expired|lapsed|annulled|"
    r"abolished|defunct|invalid|struck|dropped|draft|unused|ignore|ignored|archived|legacy|"
    r"terminated|ended)|no longer|out of force|not in force|"
    r"do not use",
    re.I,
)
_SHORTCODE = re.compile(r":(?=\S)")  # `:name:` renders as an emoji on GitHub
_MARKER = r"(?:[-*+]|[0-9]{1,9}[.)])"
_NESTED_ITEM = re.compile(
    rf"{_MARKER}[ \t]+(?:{_MARKER}(?:[ \t]|$)|#|>|`{{3}}|~{{3}}|(?:[-*_][ \t]*){{3,}}$)"
)
_RULE_LINE = re.compile(r"[=\-*_:+ \t]+")  # underlines, rules, and table delimiter rows
MAX_PREDICATE = 300

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
    ast.Name,
    ast.Load,
    ast.Constant,
)
_ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_OPS = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


class PolicyError(Exception):
    """policy.md is malformed; the message names the clause (or file and line) and the problem."""


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and not math.isfinite(value))


@dataclass(frozen=True)
class Clause:
    """One clause. Only immutable values are stored; the predicate tree is rebuilt from `when`
    (already checked at load) on each evaluation, so a loaded clause cannot be altered."""

    id: str
    title: str
    kind: Kind
    fields: tuple[str, ...]
    when: str

    def missing(self, case: AlertCase) -> tuple[str, ...]:
        """The fields this clause reads that have no usable value on this alert."""
        values = alert_values(case)
        return tuple(f for f in self.fields if _missing(values[f]))

    def holds(self, case: AlertCase) -> bool:
        """Evaluate the predicate on one alert; missing values fail safe (module doc)."""
        if self.missing(case):
            return self.kind == "no_clear"
        tree = ast.parse(self.when.strip(), mode="eval")
        return bool(_evaluate(tree.body, alert_values(case)))


def alert_values(case: AlertCase) -> dict[str, Any]:
    """The values a predicate may read: the features plus amount, category and risk score."""
    return {
        **case.features.model_dump(),
        "amount": case.amount,
        "category": case.category,
        "risk_score": case.risk_score,
    }


def _evaluate(node: ast.AST, values: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        results = (_evaluate(v, values) for v in node.values)
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.UnaryOp):  # only `not` is allowed by the parser
        return not _evaluate(node.operand, values)
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, values)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, values)
            if not _OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    raise PolicyError(f"unsupported predicate node {type(node).__name__}")  # parser prevents this


def _operand_type(node: ast.expr, clause_id: str, source: str) -> FieldType:
    """The type of one side of a comparison: a field name or a constant."""
    if isinstance(node, ast.Name):
        return FIELD_TYPES[node.id]
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int | float):
            if not math.isfinite(value):
                raise PolicyError(f"{clause_id}: constant {value!r} is not finite")
            written = ast.get_source_segment(source, node) or ""
            if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", written):
                raise PolicyError(
                    f"{clause_id}: write {written!r} as a plain decimal (at most 6 decimal places)"
                )
            return "number"
        if isinstance(value, str):
            if ast.get_source_segment(source, node) not in (f"'{value}'", f'"{value}"'):
                raise PolicyError(f"{clause_id}: write each category as one quoted string")
            if value not in CATEGORIES:
                raise PolicyError(f"{clause_id}: {value!r} is not a known category")
            return "category"
        raise PolicyError(f"{clause_id}: constant {value!r} is not allowed")
    raise PolicyError(f"{clause_id}: compare a field with a field or a constant, not an expression")


def _check_boolean(node: ast.expr, clause_id: str, source: str) -> None:
    """Every condition must be a true/false test: a comparison, a bool field, or and/or/not of
    those. A bare number (`hour`) or constant (`True`) is refused, since its truth is accidental."""
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            _check_boolean(value, clause_id, source)
    elif isinstance(node, ast.UnaryOp):
        _check_boolean(node.operand, clause_id, source)
    elif isinstance(node, ast.Name):
        if FIELD_TYPES[node.id] != "bool":
            raise PolicyError(f"{clause_id}: {node.id} is not true/false; compare it with a value")
    elif isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        types = [_operand_type(o, clause_id, source) for o in operands]
        if len(set(types)) != 1:
            raise PolicyError(f"{clause_id}: compares values of types {sorted(set(types))}")
        if not any(isinstance(o, ast.Name) for o in operands):
            raise PolicyError(f"{clause_id}: a comparison must read a field")
        if types[0] != "number" and any(isinstance(op, _ORDERING) for op in node.ops):
            raise PolicyError(f"{clause_id}: a {types[0]} can only be compared with == or !=")
    else:
        raise PolicyError(f"{clause_id}: a predicate must be a true/false test")


def parse_predicate(when: str, fields: tuple[str, ...], clause_id: str) -> ast.Expression:
    """Parse a `when` predicate, allowing only comparisons, and/or/not, field names and
    constants. The names read must be exactly the clause's declared `fields`, and every
    comparison must be between values of the same type."""
    if not when.isascii():
        raise PolicyError(f"{clause_id}: a predicate is plain ASCII")
    if "#" in when:
        raise PolicyError(f"{clause_id}: comments are not allowed in a predicate")
    if re.search(r"\s\s|[^\S ]", when):
        raise PolicyError(f"{clause_id}: a predicate is one line with single spaces")
    if len(when) > MAX_PREDICATE:
        raise PolicyError(f"{clause_id}: predicate is longer than {MAX_PREDICATE} characters")
    try:
        tree = ast.parse(when.strip(), mode="eval")
    except SyntaxError as exc:
        raise PolicyError(f"{clause_id}: predicate does not parse: {exc.msg}") from exc
    except (RecursionError, MemoryError) as exc:
        raise PolicyError(f"{clause_id}: predicate is nested too deeply") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise PolicyError(f"{clause_id}: {type(node).__name__} is not allowed in a predicate")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    if names != set(fields):
        raise PolicyError(
            f"{clause_id}: predicate reads {sorted(names)} but declares fields {sorted(fields)}"
        )
    _check_boolean(tree.body, clause_id, when.strip())
    return tree


@dataclass(frozen=True)
class Policy:
    clauses: Mapping[str, Clause]  # read-only, in file order
    version: str  # first 16 hex of sha256 over the file bytes
    text: str
    _prompt: str = field(repr=False, default="")

    def unknown_clauses(self, ids: Iterable[str]) -> list[str]:
        """Cited clause ids that do not exist in this policy (B8)."""
        return [i for i in ids if i not in self.clauses]

    def clearing_allowed(self, cited: Iterable[str], case: AlertCase) -> tuple[bool, list[str]]:
        """Router rules 3 and 4: at least one cited clear clause holds, and no no-clear clause
        in the whole policy holds. Returns (allowed, reasons for refusal)."""
        cited = list(cited)
        reasons = [f"unknown_clause:{i}" for i in self.unknown_clauses(cited)]
        clears = [self.clauses[i] for i in cited if i in self.clauses]
        clears = [c for c in clears if c.kind == "clear"]
        if not clears:
            reasons.append("no_clear_clause_cited")
        elif not any(c.holds(case) for c in clears):
            reasons += [
                f"{'missing_value' if c.missing(case) else 'predicate_false'}:{c.id}"
                for c in clears
            ]
        reasons += [
            f"no_clear:{c.id}"
            for c in self.clauses.values()
            if c.kind == "no_clear" and c.holds(case)
        ]
        return (not reasons, reasons)

    def prompt_text(self) -> str:
        """The policy as the model sees it: prose and clause ids, without the YAML blocks."""
        return self._prompt


@dataclass
class _Section:
    id: str
    title: str
    line: int
    yaml: list[str] = field(default_factory=list)
    other_blocks: int = 0
    prose: list[str] = field(default_factory=list)


def _has_words(text: str) -> bool:
    return any(ch.isalnum() and ch not in FILLERS for ch in text)


def _check_characters(text: str, path: Path) -> None:
    for n, line in enumerate(text.split("\n"), 1):
        for ch in line:
            if ch in _HIDDEN_CHARS or (
                ch != "\t" and unicodedata.category(ch) in _HIDDEN_CATEGORIES
            ):
                raise PolicyError(
                    f"{path}: line {n}: invisible or control character U+{ord(ch):04X}"
                )


def _scan_lines(text: str, path: Path) -> tuple[list[int], list[tuple[int, int]]]:
    """Check every line against the allowed shapes (module doc). Returns the 0-based lines of
    `##` headings and the [start, end) line ranges of code blocks, for the cross-check."""
    headings: list[int] = []
    fences: list[tuple[int, int]] = []
    open_at: int | None = None
    closer: re.Pattern[str] | None = None
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if open_at is not None:
            assert closer is not None
            if closer.fullmatch(line):
                fences.append((open_at, i + 1))
                open_at = None
            continue
        if not line.strip(" \t"):  # CommonMark blank: spaces and tabs only
            continue
        where = f"{path}: line {i + 1}"
        m = _FENCE_OPEN.match(line)
        if m:
            marker = m.group(1)
            open_at, closer = i, re.compile(f"{re.escape(marker[0])}{{{len(marker)},}}[ \t]*")
            continue
        if line[0].isspace():
            raise PolicyError(f"{where}: indentation is only allowed inside code blocks")
        if line.startswith("## "):
            headings.append(i)
        elif line.startswith("#") and not line.startswith("# "):
            raise PolicyError(f"{where}: only '# Title' and '## P<n>.<m> Title' headings")
        elif _RULE_LINE.fullmatch(line):
            raise PolicyError(f"{where}: underline headings and horizontal rules are not allowed")
        elif _NESTED_ITEM.match(line):
            raise PolicyError(f"{where}: a list item may not start a list, heading, quote or code")
        elif line[0] == ">":
            raise PolicyError(f"{where}: block quotes are not allowed")
        elif line[0] == "[":
            raise PolicyError(f"{where}: a line may not start with '[' (a link definition)")
        if not _PROSE_CHARS.fullmatch(line):
            bad = next(ch for ch in line if not _PROSE_CHARS.fullmatch(ch))
            reason = _CHAR_REASON.get(bad, "outside the allowed set")
            raise PolicyError(
                f"{where}: {bad!r} ({reason}) is not allowed outside code blocks; prose uses "
                "ASCII letters, digits, spaces and . , ; : ' \" ? ! ( ) / % * _ # + -"
            )
        if _SHORTCODE.search(line):
            raise PolicyError(
                f"{where}: ':' must be followed by a space (':name:' renders as an emoji)"
            )
    if open_at is not None:
        raise PolicyError(f"{path}: line {open_at + 1}: code block is never closed")
    return headings, fences


def _sections(text: str, path: Path) -> tuple[list[_Section], list[tuple[int, int]]]:
    """Walk the CommonMark token stream; return the clause sections and the line ranges of the
    yaml blocks (dropped from the prompt)."""
    tokens: list[Token] = _MD.parse(text)
    sections: list[_Section] = []
    fences: list[tuple[int, int]] = []
    titles = 0
    for t in tokens:
        line = (t.map[0] + 1) if t.map else 0
        if t.type in _REFUSED_BLOCKS:
            raise PolicyError(f"{path}: line {line}: {_REFUSED_BLOCKS[t.type]} is not allowed")
        children = (t.children or []) if t.type == "inline" else []
        if any(c.type in ("html_inline", "image") for c in children):
            raise PolicyError(f"{path}: line {line}: raw HTML and images are not allowed")
        if any(c.type == "link_open" and c.attrGet("title") for c in children):
            raise PolicyError(f"{path}: line {line}: link titles (hover text) are not allowed")
        if t.type in ("heading_open", "fence") and t.level != 0:
            raise PolicyError(f"{path}: line {line}: headings and code blocks may not be nested")
    for i, t in enumerate(tokens):
        line = (t.map[0] + 1) if t.map else 0
        current = sections[-1] if sections else None
        if t.type == "heading_open":
            content = tokens[i + 1].content
            shown = _rendered(tokens[i + 1])
            if t.tag == "h1":
                if current is not None or titles:
                    raise PolicyError(f"{path}: line {line}: one '# Title', before the clauses")
                if not shown.isascii() or re.search(r"P\s*[0-9]", shown):
                    raise PolicyError(f"{path}: line {line}: a clause needs a ## heading")
                titles += 1
            elif t.tag == "h2":
                m = HEADING_RE.fullmatch(content)
                if not m or not CLAUSE_RE.fullmatch(m.group(1)):
                    raise PolicyError(
                        f"{path}: line {line}: a ## heading must be a clause "
                        f"(## P<n>.<m> Title), found {content!r}"
                    )
                if not _has_words(shown.removeprefix(m.group(1))):
                    raise PolicyError(f"{m.group(1)}: the title needs words")
                sections.append(_Section(m.group(1), m.group(2).strip(), line))
            elif current is not None:
                current.prose.append(shown)
        elif t.type == "fence":
            if current is None:
                raise PolicyError(f"{path}: line {line}: code blocks belong inside a clause")
            assert t.map is not None
            fences.append((t.map[0], t.map[1]))
            if t.info.strip() == "yaml" and t.markup == "```":
                current.yaml.append(t.content)
            else:
                current.other_blocks += 1
        elif t.type == "inline" and current is not None and tokens[i - 1].type != "heading_open":
            current.prose.append(_rendered(t))
    return sections, fences


def _shown_text(text: str) -> str:
    """All inline text as markdown-it renders it, clause ids removed from `##` headings."""
    tokens = _MD.parse(text)
    parts = []
    for i, t in enumerate(tokens):
        if t.type != "inline":
            continue
        shown = _rendered(t)
        if i and tokens[i - 1].type == "heading_open" and tokens[i - 1].tag == "h2":
            m = HEADING_RE.match(shown)
            shown = shown[len(m.group(1)) :] if m else shown
        parts.append(shown)
    return "\n".join(parts)


def _check_mentions(
    lines: list[tuple[int, str]], clauses: Mapping[str, Clause], path: Path
) -> None:
    """Every clause-id look-alike must be an existing id written exactly. The scan reads each
    run of whitespace, line breaks included, as one space (as a browser shows it)."""
    flat: list[str] = []
    owner: list[int] = []
    for n, t in lines:
        for ch in t + "\n":
            if ch.isspace():
                if flat and flat[-1] == " ":
                    continue
                ch = " "
            flat.append(ch)
            owner.append(n)
    joined = "".join(flat)
    for found in _CLAUSE_LIKE.finditer(joined):
        exact = _CLAUSE_EXACT.match(joined, found.start())
        if not exact or exact.group(0) not in clauses:
            line = owner[found.start()]
            where = f"line {line + 1}: " if line >= 0 else ""
            raise PolicyError(
                f"{path}: {where}text that reads like a clause id must be an existing clause, "
                "written exactly (P<n>.<m>)"
            )


def _normalised(text: str, marks: str = "") -> str:
    """Text as a reader takes it in: emphasis marks replaced by `marks`, every run of other
    characters one space."""
    return " " + re.sub(r"[^A-Za-z0-9]+", " ", re.sub(r"[*_]", marks, text)).strip() + " "


# Distinctive entries also matched with every non-letter deleted, so hyphens, letter-spacing
# and line breaks inside the word (`with-drawn`, `W I T H D R A W N`) cannot split it.
_WITHDRAWN_SQUASHED = re.compile(
    r"withdrawn|repealed|superseded|rescinded|annulled|abolished|revoked|nolonger|"
    r"notinforce|outofforce|donotuse"
)


def _withdrawn(text: str) -> bool:
    """A listed withdrawal word, reading `*`/`_` both as emphasis (gone) and as literal marks,
    and the distinctive entries with every non-letter removed."""
    if any(_WITHDRAWN.search(_normalised(text, marks)) for marks in ("", " ")):
        return True
    return bool(_WITHDRAWN_SQUASHED.search(re.sub(r"[^a-z]", "", text.lower())))


def _rendered(inline: Token) -> str:
    """The text a reader sees for one inline token: text and code spans, without markup or
    link targets."""
    parts = []
    for c in inline.children or []:
        if c.type in ("text", "code_inline"):
            parts.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            parts.append(" ")  # a line break reads as a space
    return "".join(parts)


def _clause(section: _Section) -> Clause:
    clause_id = section.id
    if len(section.yaml) != 1 or section.other_blocks:
        raise PolicyError(f"{clause_id}: needs exactly one ```yaml block and no other code block")
    lines = section.yaml[0].rstrip("\n").split("\n")
    keys = [m.group(1) if (m := _YAML_LINE.fullmatch(ln)) else None for ln in lines]
    if keys != ["id", "kind", "fields", "when"]:
        raise PolicyError(
            f"{clause_id}: the yaml is exactly four lines, id, kind, fields and when, "
            "each 'key: value' on one line"
        )
    if any(len(ln) > MAX_YAML_LINE or "  " in ln for ln in lines):
        raise PolicyError(
            f"{clause_id}: yaml lines are at most {MAX_YAML_LINE} characters with single "
            "spaces, so nothing sits off-screen"
        )
    if _CLAUSE_LIKE.search(section.title):
        raise PolicyError(f"{clause_id}: a clause title may not name another clause id")
    for where, words in (("title", section.title), ("prose", " ".join(section.prose))):
        if _withdrawn(words):
            raise PolicyError(f"{clause_id}: a live clause's {where} may not call it withdrawn")
    if "\\" in section.yaml[0]:
        raise PolicyError(f"{clause_id}: escapes are not allowed in the yaml (write it plainly)")
    if "#" in section.yaml[0]:
        raise PolicyError(f"{clause_id}: comments are not allowed in the yaml (they hide text)")
    if not any(_has_words(p) for p in section.prose):
        raise PolicyError(f"{clause_id}: needs a plain-language rule")
    try:
        data = yaml.load(section.yaml[0], Loader=StrictLoader)  # a SafeLoader subclass
    except (yaml.YAMLError, RecursionError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PolicyError(f"{clause_id}: yaml does not parse ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {"id", "kind", "fields", "when"}:
        raise PolicyError(f"{clause_id}: yaml must have exactly id, kind, fields, when")
    if data["id"] != clause_id:
        raise PolicyError(f"{clause_id}: yaml id {data['id']!r} does not match the heading")
    if data["kind"] not in get_args(Kind):
        raise PolicyError(f"{clause_id}: kind {data['kind']!r} is not signal, clear or no_clear")
    fields = data["fields"]
    if not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields):
        raise PolicyError(f"{clause_id}: fields must be a non-empty list of names")
    if len(set(fields)) != len(fields):
        raise PolicyError(f"{clause_id}: fields repeat a name")
    unknown = [f for f in fields if f not in FIELD_TYPES]
    if unknown:
        raise PolicyError(f"{clause_id}: fields {unknown} are not on the feature allow-list")
    if not isinstance(data["when"], str) or not data["when"].strip():
        raise PolicyError(f"{clause_id}: when must be a non-empty predicate")
    parse_predicate(data["when"], tuple(fields), clause_id)
    return Clause(clause_id, section.title, data["kind"], tuple(fields), data["when"])


def load_policy(path: Path | str = DEFAULT_POLICY) -> Policy:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"{path}: cannot read ({exc.strerror})") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError(f"{path}: not UTF-8 text") from exc
    text = text.removeprefix("\ufeff")  # a byte-order mark, which renderers drop too
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # CommonMark line endings
    _check_characters(text, path)
    line_headings, line_fences = _scan_lines(text, path)
    sections, fences = _sections(text, path)
    md_headings = [t.map[0] for t in _MD.parse(text) if t.type == "heading_open" and t.map]
    md_headings = [n for n in md_headings if text.split("\n")[n].startswith("## ")]
    if md_headings != line_headings or fences != line_fences:
        first = min(
            set(md_headings) ^ set(line_headings) or {f[0] for f in set(fences) ^ set(line_fences)}
        )
        raise PolicyError(
            f"{path}: line {first + 1}: the Markdown structure is ambiguous; "
            "keep to the simple subset"
        )
    if not sections:
        raise PolicyError(f"{path}: no clause headings (## P<n>.<m> Title)")
    clauses: dict[str, Clause] = {}
    for section in sections:
        if section.id in clauses:
            raise PolicyError(f"{section.id}: duplicate clause id")
        clauses[section.id] = _clause(section)
    if not any(c.kind == "clear" for c in clauses.values()):
        raise PolicyError(f"{path}: the policy names no clear clause, so nothing could be cleared")
    lines = text.split("\n")
    dropped = {n for start, end in fences for n in range(start, end)}
    # Every `P` followed by a digit, in titles, clause titles and prose (across line breaks, as
    # paragraphs render), must be an existing clause id written exactly.
    raw_prose = []
    for n, ln in enumerate(lines):
        if n in dropped:
            continue
        m = HEADING_RE.match(ln[3:]) if ln.startswith("## ") else None
        raw_prose.append((n, ln[3 + len(m.group(1)) :] if m else ln))
    shown = _shown_text(text)
    # Every reasonable reading of the text, so a mix of emphasis and literal marks cannot slip
    # between them: as markdown-it renders it, and the raw text with `*`/`_` removed, read as
    # spaces, and kept. A reading that finds a problem is enough to refuse.
    for marks in ("", " ", None):
        readings = [(n, t if marks is None else re.sub(r"[*_]", marks, t)) for n, t in raw_prose]
        _check_mentions(readings, clauses, path)
        if _withdrawn(" ".join(t for _, t in readings)):
            raise PolicyError(f"{path}: the policy text calls something withdrawn; remove it")
    _check_mentions([(-1, shown)], clauses, path)
    if _withdrawn(shown):
        raise PolicyError(f"{path}: the policy text calls something withdrawn; remove it")
    kept = "\n".join(ln for n, ln in enumerate(lines) if n not in dropped)
    prompt = re.sub(r"\n{3,}", "\n\n", kept).strip() + "\n"
    version = hashlib.sha256(raw).hexdigest()[:16]
    return Policy(clauses=MappingProxyType(clauses), version=version, text=text, _prompt=prompt)
