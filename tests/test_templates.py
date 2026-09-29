import pytest

from vecho.templates import (
    BUILTIN_NAME,
    TemplateError,
    TemplateStore,
    conform,
    drop_placeholders,
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


def test_fillers_are_removed_but_facts_that_say_none_stay():
    answer = """## 1. 현황 스냅샷

- **지원 현황**
    - (전사 기록에 해당 정보 없음)
- **면접**: 정보 없음
- **과제**
    - 과제는 아직 받은 것 없음
- N/A
- 원티드 150개

| 시기 | 활동 | 비고 |
| --- | --- | --- |
| (전사 기록에 해당 정보 없음) | | |
| 과거 | 은행 창구 근무 | 3년 |

> (해당 내용 없음)
"""
    cleaned = drop_placeholders(answer)
    assert "정보 없음" not in cleaned and "N/A" not in cleaned and "해당 내용 없음" not in cleaned
    assert "- **지원 현황**" in cleaned and "- **면접**" in cleaned  # labels stay, empty
    assert "과제는 아직 받은 것 없음" in cleaned and "원티드 150개" in cleaned
    assert "| 과거 | 은행 창구 근무 | 3년 |" in cleaned and "| 시기 | 활동 | 비고 |" in cleaned


def test_example_subheadings_filled_only_with_fillers_disappear():
    answer = """## 2. 핵심 인사이트

### 💡 공부 ≠ 취업 준비

> (전사 기록에 해당 정보 없음)

### 💡 도메인은 하나로

- 핀테크로 통일
"""
    result = conform(drop_placeholders(answer), EXAMPLE)
    assert "공부 ≠ 취업 준비" not in result and "도메인은 하나로" in result


# ---- regressions found in review --------------------------------------------------------


def test_text_before_the_first_heading_is_kept():
    result = conform(
        "Intro with real facts.\n# Meeting notes\nimportant stuff\n## A\nx\n", "## A\n## B"
    )
    assert "Intro with real facts." in result and "important stuff" in result
    assert result.index("## A") < result.index("## B")


def test_fenced_answer_after_a_short_intro():
    answer = "Here you go:\n```markdown\n## A\n- fact\n```\n"
    assert strip_fences(answer) == "## A\n- fact"


@pytest.mark.parametrize(
    "line",
    [
        "- 기록이 없는 거래 3건 발견",
        "- 보고서 내용이 부족하다는 피드백",
        "- Pricing not discussed; revisit Monday",
        "- Vendor sent no details yet",
        "- 과제는 아직 받은 것 없음",
    ],
)
def test_real_facts_are_not_mistaken_for_fillers(line):
    assert drop_placeholders(line) == line


@pytest.mark.parametrize(
    "line",
    [
        "- 없음",
        "- (전사 기록에 해당 정보 없음)",
        "- 관련 내용 없음",
        "- Not mentioned in the transcript",
        "- N/A",
    ],
)
def test_whole_value_fillers_are_removed(line):
    assert drop_placeholders(line) == ""


def test_table_header_rows_survive_copy_removal():
    template = (
        "## People\n\n| Name | Role | Notes |\n|---|---|---|\n| Kim | Designer | example row |\n"
    )
    answer = (
        "## People\n\n| Name | Role | Notes |\n|---|---|---|\n"
        "| Lee | PM | new hire |\n| Kim | Designer | example row |\n"
    )
    cleaned = remove_copied(answer, template)
    assert "| Name | Role | Notes |" in cleaned and "| Lee | PM | new hire |" in cleaned
    assert "example row" not in cleaned


def test_repeated_sub_headings_keep_their_own_section():
    template = "## A\n### Details\n## B\n### Details\n"
    result = conform("## A\nfoo\n## B\n### Details\nB-detail\n", template)
    assert result.index("B-detail") > result.index("## B")


def test_names_differing_only_by_case_are_one_template(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("Weekly", "## A\n")
    with pytest.raises(TemplateError, match="already exists"):
        store.save("weekly", "## B\n")
    assert store.get("WEEKLY").builtin  # no accidental match on a case-insensitive disk
    store.save("weekly", "## B\n", previous="Weekly")  # a rename that only changes case
    assert [t.name for t in store.list()][1:] == ["weekly"]
    assert store.get("weekly").body == "## B\n"


def test_rename_keeps_the_default(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("old", "## A\n")
    store.set_default("old")
    store.save("new", "## A\n", previous="old")
    assert store.default_name() == "new" and [t.name for t in store.list()][1:] == ["new"]


# ---- second review ------------------------------------------------------------------------


def test_a_code_block_inside_a_summary_is_not_unwrapped():
    answer = (
        "## 1. 현황\n\n- 배포 스크립트 논의\n\n```\n# 배포\n./deploy.sh\n```\n\n"
        "## 2. 숙제\n\n1. 테스트\n"
    )
    assert strip_fences(answer) == answer


def test_opening_chatter_and_stray_titles_are_not_kept():
    body = "## 1. 현황\n"
    for answer in (
        "다음은 요약입니다:\n\n## 1. 현황\n- 사실\n",
        "# 회의 요약\n\n## 1. 현황\n- 사실\n",
        "Here is the summary\n## 1. 현황\n- 사실\n",
    ):
        result = conform(answer, body)
        assert result.startswith("## 1. 현황"), result


@pytest.mark.parametrize(
    "value",
    [
        "별도 언급 없음",
        "구체적인 언급 없음",
        "추가 정보 없음",
        "No details were given",
        "해당 없음 (언급 없음)",
        "언급되지 않음",
        "대화에서 언급되지 않음",
        "명시되지 않음",
    ],
)
def test_more_filler_forms(value):
    assert drop_placeholders(f"- {value}") == ""


@pytest.mark.parametrize("value", ["예산 문제 없음", "이견 없음", "특이사항: 서버 증설 필요"])
def test_facts_with_similar_words_stay(value):
    assert drop_placeholders(f"- {value}") == f"- {value}"


def test_renaming_onto_another_template_is_refused(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("A", "## a\n")
    store.save("B", "## b important\n")
    with pytest.raises(TemplateError, match="already exists"):
        store.save("B", "## a edited\n", previous="A")
    assert {t.name: t.body for t in store.list()[1:]} == {"A": "## a\n", "B": "## b important\n"}
    store.save("B", "## b edited\n")  # plain overwrite of the same template still works


def test_emoji_only_headings_keep_their_content():
    result = conform("## 🔥\n\n- fire content\n", "## 💡\n\n## 🔥\n")
    assert result.index("## 🔥") < result.index("fire content")
    assert "## 💡\n\n## 🔥" in result


def test_heading_text_ending_in_hash_is_kept():
    assert headings("## C#\n## Title ##\n") == [(2, "C#"), (2, "Title")]


def test_decomposed_hangul_names_match_what_people_type(tmp_path):
    import unicodedata

    store = TemplateStore(tmp_path)
    store.save(unicodedata.normalize("NFD", "멘토링"), "## A\n")
    assert store.get("멘토링").name == "멘토링"
    assert [t.name for t in store.list()][1:] == ["멘토링"]


@pytest.mark.parametrize("name", ["CON", "nul", "Com1", "LPT9.txt"])
def test_windows_device_names_are_stored_safely(tmp_path, name):
    store = TemplateStore(tmp_path)
    store.save(name, "## A\n")
    (path,) = tmp_path.glob("*.md")
    assert path.stem.upper() not in {"CON", "NUL", "COM1", "LPT9.TXT"}
    assert store.get(name).name == name


@pytest.mark.parametrize("name", [".", "..", "..."])
def test_dot_only_names_are_rejected(tmp_path, name):
    with pytest.raises(TemplateError):
        TemplateStore(tmp_path).save(name, "## A\n")
