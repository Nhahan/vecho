"""Summary templates: a Markdown document whose structure the summary must follow.

A template can be written from scratch or simply be an old, filled-in note ("make the next one
look like this"). Its headings, labels and table columns become the structure of the summary;
anything it says is only an example and must never leak into a new summary. Sections the
conversation does not cover stay empty.

Templates live in ``<home>/templates/<name>.md``. One built-in template (the standard
summary) always exists and cannot be changed.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .errors import VechoError

BUILTIN_NAME = "기본 요약"
# Characters a file name cannot hold on some system; stored as %XX so any name works.
_UNSAFE = set('\\/:*?"<>|%')
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
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
    # NFC: macOS hands out decomposed Hangul (NFD) in file names and drag-and-drop
    name = unicodedata.normalize("NFC", " ".join(name.split()))
    if not name or not name.strip("."):
        raise TemplateError("the template needs a name", "template_name_empty")
    if len(name) > 60:
        raise TemplateError("a template name can have at most 60 characters", "template_name_long")
    if name == BUILTIN_NAME:
        raise TemplateError(
            f"'{BUILTIN_NAME}' is the built-in template; choose another name", "template_builtin"
        )
    # control characters, and code points file systems refuse (unassigned, surrogates)
    if any(ord(c) < 32 or unicodedata.category(c) in ("Cc", "Cn", "Cs") for c in name):
        raise TemplateError("the template name contains control characters", "template_name_bad")
    return name


def _same_name(name: str) -> str:
    """A name as the store lists it, for comparing names the user typed or pasted."""
    return unicodedata.normalize("NFC", " ".join(name.split()))


_RESERVED = re.compile(r"(?:con|prn|aux|nul|com\d|lpt\d)(?:\..*)?", re.IGNORECASE)  # Windows


def _file_name(name: str) -> str:
    encoded = "".join(f"%{ord(c):02X}" if c in _UNSAFE else c for c in name)
    if encoded.startswith(".") or _RESERVED.fullmatch(encoded):
        encoded = f"%{ord(encoded[0]):02X}" + encoded[1:]
    return encoded + ".md"


def _name_of(path: Path) -> str:
    return unicodedata.normalize("NFC", urllib.parse.unquote(path.stem))


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a unique temporary file, so concurrent writers never share one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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
        """The template with exactly this name; the built-in one for ``None`` or an unknown name.

        Matching goes through the listing rather than the file system, which may ignore case.
        """
        wanted = _same_name(name or "")
        if wanted and wanted != BUILTIN_NAME:
            for template in self.list()[1:]:
                if template.name == wanted:
                    return template
        return Template(BUILTIN_NAME, "", builtin=True)

    def save(self, name: str, body: str, previous: str | None = None) -> Template:
        """Create or update a template; ``previous`` renames an existing one."""
        name = _check_name(name)
        if previous is not None:
            previous = _same_name(previous)
        body = body.strip()
        if not headings(body):
            raise TemplateError(
                "a template needs at least one heading (a line starting with #)",
                "template_no_heading",
            )
        if len(body) > MAX_TEMPLATE_CHARS:
            raise TemplateError(
                f"a template can have at most {MAX_TEMPLATE_CHARS} characters", "template_too_long"
            )
        existing = {t.name.casefold(): t.name for t in self.list()[1:]}
        clash = existing.get(name.casefold())
        renaming = previous is not None and previous != name
        if clash is not None and clash != previous and (clash != name or renaming):
            raise TemplateError(f"a template named '{clash}' already exists", "template_exists")
        if clash is None and name.casefold() == BUILTIN_NAME.casefold():
            raise TemplateError(
                f"'{BUILTIN_NAME}' is the built-in template; choose another name",
                "template_builtin",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        was_default = previous is not None and self.default_name() == previous
        if previous and previous != name and self.get(previous).name == previous:
            self._path(previous).unlink()  # also covers renames that only change letter case
        path = self._path(name)
        try:
            write_text_atomic(path, body + "\n")
        except OSError as exc:
            raise TemplateError(f"could not save the template: {exc}", "template_name_bad") from exc
        if was_default:
            self.set_default(name)
        return Template(name, body + "\n")

    def delete(self, name: str) -> None:
        name = _same_name(name)
        if self.get(name).name != name:
            raise TemplateError(f"no template named '{name}'", "template_missing")
        self._path(name).unlink()
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
        if template.name != _same_name(name):
            raise TemplateError(f"no template named '{name}'", "template_missing")
        self.root.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.root / _DEFAULT_FILE, template.name)
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


_DIVIDER = re.compile(r"\|?[\s:|-]+\|?")


def _header_rows(lines: list[str]) -> set[int]:
    """Indexes of table header rows (the row right above a divider): structure, not content."""
    return {
        i
        for i in range(len(lines) - 1)
        if lines[i].strip().startswith("|")
        and "-" in lines[i + 1]
        and _DIVIDER.fullmatch(lines[i + 1].strip())
    }


def example_lines(body: str) -> set[str]:
    """Normalized example sentences of a template, for spotting copies in a summary."""
    found = set()
    lines = body.splitlines()
    headers = _header_rows(lines)
    for index, line in enumerate(lines):
        if index in headers or _is_structure(line):
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
    lines = summary.splitlines()
    headers = _header_rows(lines)
    for index, line in enumerate(lines):
        if index in headers or _is_structure(line):
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
# The *whole* value must be one of these; a sentence that merely contains "없음" is content.
_WHERE = r"(?:(?:이\s*)?(?:전사|대화|녹음|회의)\s*(?:기록|내용|중)?\s*(?:에서|에는|에|상)?\s*)?"
_MODIFIER = r"(?:(?:해당|관련|별도|추가|구체적(?:인)?|특별한|자세한|상세|언급된)\s*)*"
_PLACEHOLDER = re.compile(
    r"(?:없음|없다|없습니다|해당\s*없음|미정|미확인|미언급|n/?a|none|nothing|tbd|unknown|[-–—?]+)"
    + r"|"
    + _WHERE
    + _MODIFIER
    + r"(?:정보|내용|언급|기록|사항|자료|논의)\s*(?:이|가|은|는)?\s*"
    r"(?:없음|없습니다|없다|없었음|없었습니다|부족|확인\s*(?:불가|되지\s*않음))"
    + r"|"
    + _WHERE
    + _MODIFIER
    + r"(?:언급|논의|명시|확인|기재|공유)\s*되지\s*(?:않음|않았음|않았습니다|않습니다|않았다)"
    r"|(?:not\s+(?:mentioned|discussed|covered|stated|specified)|no\s+(?:information|mention|details)"
    r"(?:\s+(?:was|were)?\s*(?:given|provided|available|mentioned))?)"
    r"(?:\s+in\s+the\s+(?:transcript|conversation|recording))?",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"^(\s*(?:[-*+]|\d+[.)]|>)?\s*)")
_LABEL = re.compile(r"\*\*[^*]+\*\*\s*:?\s*")


def _is_placeholder(text: str) -> bool:
    text = re.sub(r"\*\*|__|`", "", text).strip().strip("[]{}「」.。,·:;").strip()
    # "해당 없음 (언급 없음)": a filler followed by another filler in parentheses
    paren = re.fullmatch(r"(.*?)\s*[(（]([^()（）]*)[)）]", text)
    if paren and paren.group(1).strip():
        return _is_placeholder(paren.group(1)) and _is_placeholder(paren.group(2))
    text = text.strip("()（）").strip()
    return bool(text) and len(text) <= 40 and bool(_PLACEHOLDER.fullmatch(text))


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
        if _HEADING.match(stripped) or re.fullmatch(r"[-*_](?:\s*[-*_]){2,}", stripped):
            kept.append(line)  # headings and rules ("---" is not a "none" dash)
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
    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code and _HEADING.match(line.strip()):
            sections.append((line.strip(), []))
            continue
        if sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _key(text: str) -> str:
    """Heading identity: its letters and digits, or the text itself for "## 💡" and the like."""
    return _norm(text) or text.strip()


# An answer's opening remark, not part of the notes: "다음은 요약입니다:", "Here is the summary".
# Only short lines about the answer itself: "다음은 3분기 예산안이며 …" is a fact, not chatter.
_OPENER = re.compile(
    r"(?:다음은|아래는|요청하신|이\s*템플릿|템플릿에\s*맞춰|here\s+(?:is|are)|here's|below\s+is|sure|certainly)",
    re.IGNORECASE,
)
_ABOUT_ANSWER = re.compile(
    r"요약|정리|회의록|노트|템플릿|양식|작성|summar|notes|template", re.IGNORECASE
)


def _is_chatter(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80 or _HEADING.match(stripped):
        return False
    if re.search(r"[:：]$", stripped) and not re.match(r"[-*+]|\d+[.)]|\*\*", stripped):
        return True  # "요약은 다음과 같습니다:" (a labelled item is content)
    return bool(_OPENER.match(stripped)) and (
        bool(_ABOUT_ANSWER.search(stripped)) or len(stripped) <= 20
    )


def _lead(preamble: list[str]) -> list[str]:
    """What the model wrote before the first template heading, minus its opening remarks.

    Bare titles with nothing under them go, and so does a stray fence line (from a
    half-unwrapped ```markdown answer); balanced code blocks and headings with content stay.
    """
    fences = [i for i, line in enumerate(preamble) if line.lstrip().startswith("```")]
    drop = set(fences) if len(fences) % 2 else set()
    drop |= {i for i, line in enumerate(preamble) if _is_chatter(line)}
    for i, line in enumerate(preamble):
        if _HEADING.match(line.strip()):  # a bare title ("# 회의 요약") with nothing under it
            below = (p for j, p in enumerate(preamble[i + 1 :], i + 1) if j not in drop)
            following = next((p for p in below if p.strip()), "")
            if not following or _HEADING.match(following.strip()):
                drop.add(i)
    return [line for i, line in enumerate(preamble) if i not in drop]


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
    keys = [_key(text) for _, text in wanted]
    preamble, sections = _split_sections(summary)

    own: dict[int, list[str]] = {}  # what the model wrote directly under a template heading
    extra: dict[int, list[str]] = {}  # headings the model added, kept after that section
    current = -1
    for heading_line, lines in sections:
        match = _HEADING.match(heading_line)
        key = _key(match.group(2)) if match else ""
        # the first unused template heading with this text, looking forward first (templates
        # may repeat a sub-heading such as "### Details" under several sections)
        order = [*range(current + 1, len(keys)), *range(0, current + 1)]
        index = next((i for i in order if key and keys[i] == key and i not in own), -1)
        if index == -1:
            # fuzzy: a template heading contained in the model's heading or vice versa
            index = next(
                (
                    i
                    for i in order
                    if keys[i] and key and i not in own and (keys[i] in key or key in keys[i])
                ),
                -1,
            )
        if index >= 0:
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
    lead = trimmed(_lead(preamble))
    if lead:  # facts the model wrote before the first template heading are kept, not lost
        out += [*lead, ""]
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
    """Models sometimes wrap the whole answer in a ```markdown block, maybe after a short intro.

    A code block *inside* a summary is left alone: only an answer whose headings are all
    inside one fence, with at most a line of text around it, is unwrapped.
    """
    match = re.search(r"(?m)^```(?:markdown|md)?[ \t]*\n(.*)\n```[ \t]*$", text, re.DOTALL)
    if not match or "\n```" in match.group(1) or not headings(match.group(1)):
        return text
    outside = (text[: match.start()] + text[match.end() :]).strip()
    if headings(outside) or outside.count("\n") > 1:
        return text
    return match.group(1)
