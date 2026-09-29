import pytest

from vecho.templates import (
    BUILTIN_NAME,
    TemplateError,
    TemplateStore,
    conform,
    example_lines,
    headings,
    remove_copied,
    strip_fences,
    top_sections,
)

# A filled-in note used as a template: its facts are examples, its shape is the template.
EXAMPLE = """\
## 1. 현황 스냅샷

- **지원 현황**
    - 사람인 628개 (다른 플랫폼 지원은 아직 없음)
- **면접**
    - 총 3회 진행 / 앞으로 6개 예정

## 2. 핵심 인사이트

### 💡 공부 ≠ 취업 준비

- 신입들이 가장 많이 하는 실수: 실력이 부족해서 지원을 미루는 것

## 3. 로드맵

| 주차 | 일정 | 활동 |
| --- | --- | --- |
| 1주차 | 5/26 ~ 6/1 | 이력서 완성 |

## 4. 다짐

-
"""


def test_headings_ignore_code_blocks():
    body = "## A\n```\n## not a heading\n```\n### B ###\n"
    assert headings(body) == [(2, "A"), (3, "B")]


def test_example_lines_skip_structure_and_labels():
    examples = example_lines(EXAMPLE)
    assert any("사람인628개" in e for e in examples)
    assert not any(e == "지원현황" for e in examples)  # a bare label is structure
    assert not any("주차" in e and "활동" in e for e in examples)  # table header


def test_copied_example_facts_are_removed_but_labels_stay():
    answer = """## 1. 현황 스냅샷

- **지원 현황**
    - 사람인 628개 (다른 플랫폼 지원은 아직 없음)
- **면접**
    - 총 2회 진행
- **지원 현황**: 사람인 628개 (다른 플랫폼 지원은 아직 없음)
"""
    cleaned = remove_copied(answer, EXAMPLE)
    assert "628" not in cleaned
    assert "- **지원 현황**" in cleaned and "총 2회 진행" in cleaned


def test_conform_restores_missing_sections_in_order_and_empty():
    answer = """## 3. 로드맵

| 주차 | 일정 | 활동 |
| --- | --- | --- |
| 이번 주 | 금요일 | 이력서 수정 |

## 1. 현황 스냅샷

- **면접**
    - 총 2회 진행
"""
    result = conform(answer, EXAMPLE)
    order = [text for _, text in headings(result)]
    assert order == ["1. 현황 스냅샷", "2. 핵심 인사이트", "3. 로드맵", "4. 다짐"]
    assert "## 4. 다짐\n" in result  # empty, not invented
    assert "| 이번 주 | 금요일 | 이력서 수정 |" in result


def test_example_subheadings_are_not_forced_back():
    answer = """## 2. 핵심 인사이트

### 💡 도메인은 하나로

- 핀테크로 통일
"""
    result = conform(answer, EXAMPLE)
    assert "공부 ≠ 취업 준비" not in result  # an example insight, not structure
    assert "### 💡 도메인은 하나로" in result
    # the model echoing the example sub-heading with nothing under it is dropped too
    echoed = answer + "\n### 💡 공부 ≠ 취업 준비\n\n"
    assert "공부 ≠ 취업 준비" not in conform(echoed, EXAMPLE)


def test_conform_matches_slightly_renamed_headings_and_keeps_extra_ones():
    answer = """## 현황 스냅샷

- **면접**
    - 1회

### 추가 메모

- 다음 주 재확인
"""
    result = conform(answer, EXAMPLE)
    assert result.startswith("## 1. 현황 스냅샷")
    assert "### 추가 메모" in result and result.index("### 추가 메모") < result.index("## 2.")


def test_strip_fences():
    assert strip_fences("```markdown\n## A\n- x\n```") == "## A\n- x"
    assert strip_fences("## A") == "## A"


# ---- store ---------------------------------------------------------------------------------


def test_store_lists_builtin_first_and_saves_templates(tmp_path):
    store = TemplateStore(tmp_path / "templates")
    assert [t.name for t in store.list()] == [BUILTIN_NAME]
    store.save("멘토링 노트", EXAMPLE)
    names = [t.name for t in store.list()]
    assert names == [BUILTIN_NAME, "멘토링 노트"]
    assert store.get("멘토링 노트").body.startswith("## 1. 현황")
    assert store.get("없는 이름").builtin


def test_store_default_template(tmp_path):
    store = TemplateStore(tmp_path)
    assert store.default_name() == BUILTIN_NAME
    store.save("주간 회의", "## 결정\n")
    store.set_default("주간 회의")
    assert store.default_name() == "주간 회의"
    store.delete("주간 회의")
    assert store.default_name() == BUILTIN_NAME
    with pytest.raises(TemplateError):
        store.set_default("없는 템플릿")


@pytest.mark.parametrize("name", ["", "   ", BUILTIN_NAME, "x" * 61, "bell\x07name"])
def test_store_rejects_bad_names(tmp_path, name):
    with pytest.raises(TemplateError):
        TemplateStore(tmp_path).save(name, "## A\n")


@pytest.mark.parametrize(
    "name", ["1:1 미팅", "../escape", "a/b", ".hidden", 'Q&A "주간"?', "50% 회의"]
)
def test_any_reasonable_name_is_stored_safely(tmp_path, name):
    store = TemplateStore(tmp_path / "t")
    store.save(name, "## A\n")
    files = list((tmp_path / "t").iterdir())
    assert len(files) == 1 and files[0].parent == tmp_path / "t"  # never escapes the folder
    assert not files[0].name.startswith(".")
    assert [t.name for t in store.list()][1:] == [name]
    assert store.get(name).body == "## A\n"
    store.set_default(name)
    assert store.default_name() == name


def test_store_requires_a_heading(tmp_path):
    with pytest.raises(TemplateError, match="heading"):
        TemplateStore(tmp_path).save("무제", "그냥 문장만 있음")


def test_top_sections_are_the_ones_every_summary_has():
    assert top_sections(EXAMPLE) == ["1. 현황 스냅샷", "2. 핵심 인사이트", "3. 로드맵", "4. 다짐"]
    assert top_sections("no headings") == []
