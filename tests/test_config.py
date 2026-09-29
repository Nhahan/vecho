from pathlib import Path

import pytest

from vecho.config import Config, load_config
from vecho.errors import ConfigError


def test_defaults_use_home_directory(tmp_path):
    config = load_config({"VECHO_HOME": str(tmp_path)})
    assert config.home == tmp_path
    assert config.sessions_dir == tmp_path / "sessions"
    assert config.language is None
    assert config.summary_language == "Korean"


def test_environment_overrides_are_typed(tmp_path):
    env = {
        "VECHO_HOME": str(tmp_path),
        "VECHO_LLM_NUM_CTX": "4096",
        "VECHO_LLM_TIMEOUT": "12.5",
        "VECHO_LANGUAGE": "ko",
        "VECHO_WHISPER_MODEL": "small",
    }
    config = load_config(env)
    assert config.llm_num_ctx == 4096
    assert config.llm_timeout == 12.5
    assert config.language == "ko"
    assert config.whisper_model == "small"


def test_language_auto_means_detect(tmp_path):
    assert load_config({"VECHO_HOME": str(tmp_path), "VECHO_LANGUAGE": "auto"}).language is None


def test_config_file_is_read_and_env_wins(tmp_path):
    (tmp_path / "config.toml").write_text(
        'llm_model = "from-file"\nchunk_chars = 1000\nme_label = "Me"\n', encoding="utf-8"
    )
    config = load_config({"VECHO_HOME": str(tmp_path), "VECHO_LLM_MODEL": "from-env"})
    assert config.llm_model == "from-env"
    assert config.chunk_chars == 1000
    assert config.me_label == "Me"


def test_unknown_setting_is_rejected(tmp_path):
    (tmp_path / "config.toml").write_text("typo_setting = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="typo_setting"):
        load_config({"VECHO_HOME": str(tmp_path)})


def test_home_cannot_be_set_in_file(tmp_path):
    (tmp_path / "config.toml").write_text('home = "/elsewhere"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="VECHO_HOME"):
        load_config({"VECHO_HOME": str(tmp_path)})


def test_malformed_toml_is_reported(tmp_path):
    (tmp_path / "config.toml").write_text("this is = not [valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config({"VECHO_HOME": str(tmp_path)})


@pytest.mark.parametrize(
    ("name", "value"),
    [("VECHO_LLM_NUM_CTX", "lots"), ("VECHO_LLM_NUM_CTX", "0"), ("VECHO_CHUNK_CHARS", "-5")],
)
def test_invalid_numbers_are_rejected(tmp_path, name, value):
    with pytest.raises(ConfigError):
        load_config({"VECHO_HOME": str(tmp_path), name: value})


def test_overrides_skip_unset_values_and_normalize_language():
    base = Config(language="ko")
    assert base.with_overrides(whisper_model=None, language=None) == base
    assert base.with_overrides(language="auto").language is None
    assert base.with_overrides(llm_model="x").llm_model == "x"


def test_label_for_roles():
    config = Config()
    assert config.label_for("me") == "나"
    assert config.label_for("remote") == "상대방"
    assert config.label_for("mixed") == ""


def test_home_expands_user():
    assert load_config({"VECHO_HOME": "~/somewhere"}).home == Path.home() / "somewhere"


def test_default_chunk_fits_the_context_window():
    config = Config()
    assert config.chunk_chars * 2 <= config.llm_num_ctx  # leaves room for prompt and answer


def test_ollama_client_defaults_follow_config():
    from vecho.summarize import OllamaClient

    client = OllamaClient("http://x", "m")
    assert client.num_ctx == Config().llm_num_ctx
    assert client.timeout == Config().llm_timeout


def test_empty_home_variable_is_ignored():
    assert load_config({"VECHO_HOME": ""}).home == Config().home
