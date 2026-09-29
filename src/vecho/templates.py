"""Summary templates: a Markdown document whose structure the summary must follow.

A template can be written from scratch or simply be an old, filled-in note ("make the next one
look like this"). Its headings, labels and table columns become the structure of the summary;
anything it says is only an example and must never leak into a new summary. Sections the
conversation does not cover stay empty.

Templates live in ``<home>/templates/<name>.md``. One built-in template (the standard
summary) always exists and cannot be changed.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .errors import VechoError

BUILTIN_NAME = "기본 요약"
# Characters a file name cannot hold on some system; stored as %XX so any name works.
_UNSAFE = set('\\/:*?"<>|%')
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_DEFAULT_FILE = ".default"
MAX_TEMPLATE_CHARS = 20000


class TemplateError(VechoError):
    pass


@dataclass(frozen=True)
class Template:
    name: str
    body: str
    builtin: bool = False


def _check_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise TemplateError("the template needs a name")
    if len(name) > 60:
        raise TemplateError("a template name can have at most 60 characters")
    if name == BUILTIN_NAME:
        raise TemplateError(f"'{BUILTIN_NAME}' is the built-in template; choose another name")
    if any(ord(c) < 32 for c in name):
        raise TemplateError("the template name contains control characters")
    return name


def _file_name(name: str) -> str:
    encoded = "".join(f"%{ord(c):02X}" if c in _UNSAFE else c for c in name)
    return ("%2E" + encoded[1:] if encoded.startswith(".") else encoded) + ".md"


def _name_of(path: Path) -> str:
    return urllib.parse.unquote(path.stem)


class TemplateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, name: str) -> Path:
        return self.root / _file_name(_check_name(name))

    def list(self) -> list[Template]:
        found = []
        if self.root.is_dir():
            for path in sorted(self.root.glob("*.md"), key=lambda p: _name_of(p).lower()):
                try:
                    found.append(Template(_name_of(path), path.read_text("utf-8")))
                except OSError:
                    continue
        return [Template(BUILTIN_NAME, "", builtin=True), *found]

    def get(self, name: str | None) -> Template:
        """The named template; the built-in one for ``None`` or an unknown name."""
        if name and name != BUILTIN_NAME:
            try:
                path = self._path(name)
            except TemplateError:
                path = None
            if path is not None and path.is_file():
                return Template(_check_name(name), path.read_text("utf-8"))
        return Template(BUILTIN_NAME, "", builtin=True)

    def save(self, name: str, body: str) -> Template:
        body = body.strip()
        if not headings(body):
            raise TemplateError("a template needs at least one heading (a line starting with #)")
        if len(body) > MAX_TEMPLATE_CHARS:
            raise TemplateError(f"a template can have at most {MAX_TEMPLATE_CHARS} characters")
        path = self._path(name)
        self.root.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.tmp")
        partial.write_text(body + "\n", encoding="utf-8")
        os.replace(partial, path)
        return Template(_name_of(path), body + "\n")

    def delete(self, name: str) -> None:
        path = self._path(name)
        if not path.is_file():
            raise TemplateError(f"no template named '{name}'")
        path.unlink()
        if self.default_name() == name:
            self.set_default(BUILTIN_NAME)

    def default_name(self) -> str:
        try:
            name = (self.root / _DEFAULT_FILE).read_text("utf-8").strip()
        except OSError:
            return BUILTIN_NAME
        return self.get(name).name

    def set_default(self, name: str) -> str:
        template = self.get(name)
        if template.name != " ".join(name.split()):
            raise TemplateError(f"no template named '{name}'")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / _DEFAULT_FILE).write_text(template.name, encoding="utf-8")
        return template.name


# --------------------------------------------------------------------------- structure


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()


def headings(body: str) -> list[tuple[int, str]]:
    """``(level, text)`` of every Markdown heading, outside code blocks."""
    found = []
    in_code = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        match = _HEADING.match(line) if not in_code else None
        if match:
            found.append((len(match.group(1)), match.group(2).strip()))
    return found


def top_sections(body: str) -> list[str]:
    """Headings at the template's top level: the sections every summary will have."""
    found = headings(body)
    if not found:
        return []
    top = min(level for level, _ in found)
    return [text for level, text in found if level == top]


def _is_structure(line: str) -> bool:
    """Lines that carry the template's shape rather than its example facts."""
    stripped = line.strip()
    if not stripped or _HEADING.match(stripped) or re.fullmatch(r"[-*_]{3,}", stripped):
        return True
    if re.fullmatch(r"\|?[\s:|-]+\|?", stripped):  # table divider
        return True
    # a bare label such as "- **지원 현황**" (nothing after the label)
    return bool(re.fullmatch(r"[-*]?\s*\*\*[^*]+\*\*\s*:?", stripped))


def example_lines(body: str) -> set[str]:
    """Normalized example sentences of a template, for spotting copies in a summary."""
    found = set()
    for line in body.splitlines():
        if _is_structure(line):
            continue
        text = re.sub(r"^\s*(?:[-*>]|\d+\.)\s*", "", line)
        text = re.sub(r"\*\*[^*]+\*\*\s*:?", "", text)  # keep the label out of the comparison
        key = _norm(text)
        if len(key) >= 8:
            found.add(key)
    return found


def remove_copied(summary: str, body: str) -> str:
    """Drop lines the model copied from the template's example content."""
    examples = example_lines(body)
    if not examples:
        return summary
    kept = []
    for line in summary.splitlines():
        if _is_structure(line):
            kept.append(line)
            continue
        text = re.sub(r"^\s*(?:[-*>]|\d+\.)\s*", "", line)
        label = re.match(r"\s*\*\*[^*]+\*\*\s*:?", text)
        rest = text[label.end() :] if label else text
        if _norm(rest) in examples:
            if label:  # keep "- **label**" but drop the copied value
                kept.append(line[: line.index(label.group(0)) + len(label.group(0))].rstrip())
            continue
        kept.append(line)
    return "\n".join(kept)


# "Nothing to say" fillers models write even when told to leave a field empty.
_PLACEHOLDER = re.compile(
    r"^(?:없음|없다|없습니다|해당\s*없음|미정|미확인|미언급|n/?a|none|nothing|tbd|unknown|not\s+mentioned|"
    r"no\s+information|[-–—?]+)$"
    r"|(?:정보|내용|언급|기록|해당\s*사항|자료|논의)\s*(?:이|가|은|는)?\s*(?:없|부족|확인\s*(?:되지|불가))"
    r"|(?:not|never)\s+(?:mentioned|discussed|covered|stated)|no\s+(?:information|mention|details)",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"^(\s*(?:[-*+]|\d+[.)]|>)?\s*)")
_LABEL = re.compile(r"\*\*[^*]+\*\*\s*:?\s*")


def _is_placeholder(text: str) -> bool:
    text = re.sub(r"\*\*|__|`", "", text).strip().strip("()[]{}（）「」.。,·:;").strip()
    return bool(text) and len(text) <= 40 and bool(_PLACEHOLDER.search(text))


def drop_placeholders(summary: str) -> str:
    """Remove "no information" fillers, so an unfilled field really stays empty.

    Only whole values are judged: "(전사 기록에 해당 정보 없음)" goes, while a real fact such as
    "과제는 아직 받은 것 없음" stays. A label keeps its line and loses just the filler, and a
    table row made only of fillers is dropped.
    """
    kept = []
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and not re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(not c or _is_placeholder(c) for c in cells):
                continue
            kept.append(line)
            continue
        if _HEADING.match(stripped):
            kept.append(line)
            continue
        prefix = _PREFIX.match(line).group(1)
        rest = line[len(prefix) :]
        label = _LABEL.match(rest)
        value = rest[label.end() :] if label else rest
        if value.strip() and _is_placeholder(value):
            if label:
                kept.append((prefix + rest[: label.end()]).rstrip().rstrip(":").rstrip())
            continue
        kept.append(line)
    return "\n".join(kept)


def _split_sections(markdown: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Leading lines, then ``(heading line, body lines)`` for every heading."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in markdown.splitlines():
        if _HEADING.match(line.strip()):
            sections.append((line.strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def conform(summary: str, body: str) -> str:
    """Make the summary follow the template's sections, in the template's order.

    Top-level sections of the template are always present (empty when the conversation had
    nothing for them), even if the model dropped or reordered them. Deeper headings are kept
    only when the model wrote something under them: in a filled-in example such as
    "### 💡 <some insight>" they are content, not structure. Sections the model invented
    stay under the section they followed.
    """
    wanted = headings(body)
    if not wanted:
        return summary.strip()
    top = min(level for level, _ in wanted)
    keys = [_norm(text) for _, text in wanted]
    preamble, sections = _split_sections(summary)

    own: dict[int, list[str]] = {}  # what the model wrote directly under a template heading
    extra: dict[int, list[str]] = {}  # headings the model added, kept after that section
    current = -1
    for heading_line, lines in sections:
        match = _HEADING.match(heading_line)
        key = _norm(match.group(2)) if match else ""
        index = keys.index(key) if key in keys else -1
        if index == -1:
            # fuzzy: a template heading contained in the model's heading or vice versa
            index = next(
                (i for i, k in enumerate(keys) if k and key and (k in key or key in k)), -1
            )
        if index >= 0 and index not in own:
            current = index
            own[current] = lines
        elif current >= 0:
            extra.setdefault(current, []).extend(["", heading_line, *lines])
        else:
            preamble += [heading_line, *lines]

    def trimmed(lines: list[str]) -> list[str]:
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return lines

    out: list[str] = []
    for index, (level, text) in enumerate(wanted):
        content = trimmed(own.get(index, []))
        added = trimmed(extra.get(index, []))
        if level == top or content:
            out.append(f"{'#' * level} {text}")
            if content:
                out += ["", *content]
            out.append("")
        # else: a sub-heading the model left empty (often the example's own content)
        if added:
            out += [*added, ""]
    return "\n".join(out).strip() + "\n"


def strip_fences(text: str) -> str:
    """Models sometimes wrap the whole answer in a ```markdown block."""
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", text, re.DOTALL)
    return match.group(1) if match else text
