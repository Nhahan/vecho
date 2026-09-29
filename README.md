# vecho

양방향 음성 대화(화상회의, 통화, 인터뷰 등)를 **내 컴퓨터에서만** 녹음하고, 전사하고, 요약하는 CLI 서비스입니다.
녹음 · 음성 인식 · 요약이 모두 로컬에서 실행되므로 오디오와 대화 내용이 외부 서버로 나가지 않습니다.
(최초 실행 시 모델 파일을 내려받을 때만 네트워크를 사용합니다.)

```
마이크 ("나") ─────────┐                               ┌─ transcript.md
                       ├─ 트랙별 WAV ─ faster-whisper ─┤
루프백 ("상대방") ─────┘   (me / remote)   (STT, 로컬) └─ summary.md ← Ollama (LLM, 로컬)
```

내 목소리(마이크)와 상대방 목소리(시스템 출력)를 **별도 트랙**으로 녹음하기 때문에, 화자 분리 모델 없이도
`나: …` / `상대방: …` 형태로 정확하게 화자가 구분됩니다.

## 요구 사항

- macOS (Apple Silicon 권장) — 다른 OS에서는 루프백 장치를 `--remote`로 직접 지정하세요.
- Python 3.11 – 3.13, [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) — 요약용 로컬 LLM
- [BlackHole](https://github.com/ExistentialAudio/BlackHole) — 상대방 소리(시스템 출력) 캡처용 가상 오디오 장치

## 설치

```bash
git clone https://github.com/Nhahan/vecho.git && cd vecho
uv sync                         # 의존성 설치 (.venv 생성)

brew install ollama
ollama serve &                  # 또는 Ollama 앱 실행
ollama pull qwen3.8:27b         # 기본 요약 모델 (약 18GB, 메모리 32GB 이상 권장)

brew install --cask blackhole-2ch
```

### 상대방 소리 캡처 설정 (1회)

1. **Audio MIDI Setup** 앱 → 좌하단 `+` → **Multi-Output Device** 생성
2. 내 스피커/헤드폰 **과** `BlackHole 2ch`를 모두 체크
3. 시스템 설정 → 사운드 → 출력에서 이 Multi-Output Device 선택
   (통화 소리가 내 귀에도 들리면서 BlackHole로도 복사됩니다)

> **헤드폰(이어폰)을 사용하세요.** 스피커로 들으면 상대방 소리가 내 마이크에 다시 들어가 전사문에 중복으로 나타납니다.

설정이 끝나면 환경을 점검합니다.

```bash
uv run vecho doctor
```

## 빠른 시작

```bash
uv run vecho record --title "주간 회의" --language ko
# ● REC 00:12:41  나 ████░░░░  상대방 ██░░░░░░     ← Ctrl+C 로 종료
```

종료하면 자동으로 전사 → 요약까지 진행하고 요약을 출력합니다. 결과는 `~/.vecho/sessions/<세션>/`에 저장됩니다.

| 파일 | 내용 |
| --- | --- |
| `me.wav`, `remote.wav` | 트랙별 원본 녹음 (16 kHz, mono) |
| `transcript.md` / `transcript.json` | 타임스탬프와 화자가 붙은 전사문 |
| `summary.md` | TL;DR · 핵심 내용 · 결정 사항 · 액션 아이템 · 미해결 질문 |
| `session.json` | 제목, 시간, 사용한 모델 등 메타데이터 |

전사나 요약이 실패해도(예: Ollama 미실행) **녹음은 항상 보존**되며, 안내되는 명령으로 다시 시도할 수 있습니다.

## 명령어

| 명령 | 설명 |
| --- | --- |
| `vecho record` | 마이크 + 시스템 오디오 녹음 후 전사·요약 (`--no-process`로 녹음만) |
| `vecho import --me a.wav --remote b.wav` | 이미 있는 오디오 파일로 세션 생성 (`--mixed`는 한 파일에 양쪽이 섞인 경우) |
| `vecho transcribe [세션]` | 전사만 다시 실행 |
| `vecho summarize [세션]` | 요약만 다시 실행 (모델/언어를 바꿔 재요약 가능) |
| `vecho list` | 세션 목록 |
| `vecho show [세션]` | 요약 출력 (`--transcript` 전사문, `--path` 폴더 경로) |
| `vecho devices` | 오디오 입력 장치 목록 (`loopback` 표시) |
| `vecho doctor` | 마이크 · BlackHole · Whisper · Ollama 점검 |

`[세션]`에는 전체 ID, 앞부분(prefix), 일부 문자열 또는 `latest`(기본값)를 쓸 수 있습니다.

자주 쓰는 옵션:

```bash
vecho record --mic "MacBook" --remote blackhole   # 장치를 이름 일부 또는 번호로 지정
vecho record --mic-only                           # 마이크만 녹음 (BlackHole 없이)
vecho record --model small --language ko          # 더 가벼운 Whisper 모델
vecho summarize --llm-model gemma3:12b --summary-language English
```

## 설정

우선순위: 기본값 < `~/.vecho/config.toml` < `VECHO_*` 환경 변수 < 명령행 옵션.

```toml
# ~/.vecho/config.toml
whisper_model = "large-v3-turbo"   # tiny / base / small / medium / large-v3 ...
language = "ko"                    # 생략하면 자동 감지
llm_model = "qwen3.8:27b"
summary_language = "Korean"
me_label = "나"
remote_label = "상대방"
```

| 설정 | 환경 변수 | 기본값 |
| --- | --- | --- |
| `whisper_model` | `VECHO_WHISPER_MODEL` | `large-v3-turbo` |
| `whisper_compute_type` | `VECHO_WHISPER_COMPUTE_TYPE` | `int8` |
| `language` | `VECHO_LANGUAGE` | 자동 감지 |
| `llm_host` | `VECHO_LLM_HOST` | `http://127.0.0.1:11434` |
| `llm_model` | `VECHO_LLM_MODEL` | `qwen3.8:27b` |
| `llm_num_ctx` | `VECHO_LLM_NUM_CTX` | `32768` |
| `llm_timeout` | `VECHO_LLM_TIMEOUT` | `1800` (초) |
| `chunk_chars` | `VECHO_CHUNK_CHARS` | `16000` |
| `summary_language` | `VECHO_SUMMARY_LANGUAGE` | `Korean` |
| `sample_rate` | `VECHO_SAMPLE_RATE` | `16000` |
| `me_label` / `remote_label` | `VECHO_ME_LABEL` / `VECHO_REMOTE_LABEL` | `나` / `상대방` |
| — | `VECHO_HOME` | `~/.vecho` |

> 기본값은 메모리가 넉넉한 Mac(예: 64GB 이상)을 기준으로 잡았습니다. 메모리가 작다면
> `VECHO_LLM_MODEL=qwen3:8b VECHO_LLM_NUM_CTX=8192 VECHO_CHUNK_CHARS=6000`처럼 낮추세요.
> `chunk_chars`는 `llm_num_ctx`의 절반 이하(한국어 기준 토큰 ≈ 글자 수)로 유지해야 잘리지 않습니다.

긴 대화는 `chunk_chars` 단위로 나누어 부분 노트를 만든 뒤 하나의 요약으로 합칩니다(map-reduce).
그래서 컨텍스트 창보다 긴 회의도 요약할 수 있습니다.

## 개발

```bash
uv sync
uv run pytest        # 마이크·모델·Ollama 없이 실행되는 단위 테스트
uv run ruff check .
```

## 주의

- **녹음에는 상대방의 동의가 필요할 수 있습니다.** 통화·회의를 녹음하기 전에 참석자에게 알리고 지역 법규를 확인하세요.
- 음성 인식은 완벽하지 않으므로 중요한 결정 사항은 `transcript.md`와 대조해 확인하세요.
- 처음 마이크를 사용할 때 macOS가 터미널 앱의 마이크 접근 권한을 묻습니다. 허용해야 녹음됩니다.

## 라이선스

[MIT](LICENSE)
