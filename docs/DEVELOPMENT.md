# vecho — 개발자 문서

일반 사용자용 설명은 [README](../README.md)에 있습니다. 이 문서는 명령어, 설정, 동작 방식, 개발 방법을 다룹니다.

```
마이크 ("나") ──────────┐                          ┌─ 대화 (대본 형식)
                       ├─ 트랙별 WAV ─ Whisper ────┤
시스템 오디오 ("상대방") ┘   (me / remote)  (로컬 STT) └─ 요약 ← Ollama (로컬 LLM)
```

내 목소리(마이크)와 상대방 목소리(컴퓨터에서 재생되는 소리)를 **별도 트랙**으로 녹음하기 때문에,
화자 분리 모델 없이도 `나` / `상대방`이 정확히 구분됩니다.

## 설치

### 설치 스크립트 (일반 사용자용과 같음)

```bash
curl -fsSL https://raw.githubusercontent.com/Nhahan/vecho/main/install.sh | sh        # macOS, Linux
irm https://raw.githubusercontent.com/Nhahan/vecho/main/install.ps1 | iex             # Windows
```

스크립트가 하는 일: [uv](https://docs.astral.sh/uv/) 설치 → `uv tool install "vecho[desktop] @ <main 브랜치 zip>"`
→ [Ollama](https://ollama.com) 설치(macOS는 `/Applications` 또는 `~/Applications`, 관리자 권한 불필요)
→ `vecho setup`. 다시 실행하면 업데이트됩니다. 지우기: `uninstall.sh` / `uninstall.ps1`(녹음과 Ollama는 남김). 시험용 환경 변수: `VECHO_SOURCE`(다른 위치에서 설치,
예: 로컬 체크아웃), `VECHO_NO_OPEN=1`(마지막에 앱을 열지 않음).

`vecho setup`은 Ollama를 켜고, 요약 모델과 음성 인식 모델을 진행률과 함께 내려받고, 상대방 소리 녹음
도우미를 준비하고, 바로가기(macOS: `/Applications/vecho.app`, 쓸 수 없으면 `~/Applications/vecho.app`, Windows: 시작 메뉴·바탕 화면,
Linux: 앱 메뉴)를 만듭니다. 몇 번을 실행해도 안전합니다.

### 소스에서

```bash
git clone https://github.com/Nhahan/vecho.git && cd vecho
uv tool install --python 3.12 --editable ".[desktop]"   # `vecho` 명령 설치 (desktop: 전용 창)
vecho setup                                             # 모델·바로가기 준비 (Ollama는 따로 설치)
vecho doctor                                            # 준비 상태 점검
```

## 요약 모델

`llm_model`, `llm_num_ctx`, `chunk_chars`를 따로 정하지 않으면 컴퓨터 메모리에 맞춰 정해집니다
(`src/vecho/models.py`의 `LLM_TIERS`).

| 메모리 | 모델 | 컨텍스트 | 한 번에 넣는 글자 수 |
| --- | --- | --- | --- |
| 32GB 이상 | `qwen3.8:27b` (약 18GB) | 32768 | 16000 |
| 16GB 이상 | `qwen3.5:9b` (약 6.6GB) | 16384 | 8000 |
| 그 밖 | `qwen3.5:4b` (약 3.4GB) | 8192 | 4000 |

모델이 없으면 첫 요약 때 자동으로 내려받고(작업 단계 `downloading`), Ollama가 설치되어 있지만 꺼져
있으면 자동으로 켭니다. 긴 대화는 부분 노트를 만든 뒤 하나의 요약으로 합칩니다(map-reduce), 노트가
너무 길면 더 짧게 줄이는 과정을 반복합니다.

## 음성 인식

Apple Silicon Mac에서는 **Mac GPU**(MLX)로, 그 밖의 환경에서는 CPU(faster-whisper)로 전사합니다.
M4 Max 기준 68초짜리 대화(두 트랙)를 약 5초에 전사합니다(CPU로는 약 65초).
두 방식 모두 Silero VAD로 말소리 구간만 골라(10분 단위로 읽어 메모리를 아끼고) 1.5초 간격으로 이어
붙여 전사하고, 단어 시각을 원래 녹음 시각으로 되돌립니다. 그래서 조용한 구간에 없는 말("감사합니다" 등)이
생기지 않고, 문장 첫 단어가 앞 문장에 붙지 않습니다. 긴 녹음은 약 10분 분량씩 나누어 진행률을 보고합니다.
`whisper_backend` 설정으로 직접 고를 수 있습니다.

## 상대방 소리 녹음

**가상 오디오 드라이버, 관리자 권한, 소리 설정 변경이 필요 없습니다.**

| OS | 방식 | 필요한 것 |
| --- | --- | --- |
| macOS 14.4+ | Core Audio 프로세스 탭 (Swift 도우미) | 처음 녹음할 때 **시스템 오디오 녹음** 권한 허용 |
| Windows 10/11 | WASAPI 루프백 | 없음 |
| Linux | PulseAudio / PipeWire 모니터 | `pipewire-pulse` 또는 PulseAudio |

macOS 도우미(`src/vecho/resources/system_audio.swift`)는 미리 빌드한 universal 바이너리
(`resources/bin/system-audio`, arm64 + x86_64)로 함께 배포되므로 Xcode 도구가 필요 없습니다.
소스를 바꿨다면 `scripts/build-helper.sh`로 다시 빌드하세요(테스트가 소스와 바이너리의 해시가 같은지 확인합니다).
바이너리가 소스와 다르면 `swiftc`로 직접 컴파일합니다.

도우미는 캡처한 소리를 1분 분량 버퍼에 넣고 별도 스레드로 내보내므로, 앱이 잠시 바빠도 소리를 잃지 않습니다.
마이크도 별도 프로세스(`python -m vecho.miccapture`)에서 녹음합니다. PortAudio가 macOS에서 정지 중에
멈추는 문제([PortAudio #1174](https://github.com/PortAudio/portaudio/issues/1174))가 앱에 번지지 않게 하고,
앱이 바빠도 마이크 소리를 잃지 않게 하기 위해서입니다. 녹음이 끝나면 트랙마다 받은 소리 길이를 실제
시간과 비교해, 1초 넘게 빠진 부분이 있으면 알려 줍니다.

> 이어폰을 쓰면 가장 깨끗합니다. 스피커로 들으면 상대방 소리가 마이크에 다시 들어가지만,
> **마이크 쪽 에코는 자동으로 제거**됩니다 (6글자 미만의 짧은 맞장구는 구분이 어려워 그대로 둡니다).

## 저장 위치와 개인정보

모든 데이터는 `~/.vecho/sessions/<날짜-제목>/`에 저장됩니다 (Windows: `C:\Users\<이름>\.vecho`).

| 파일 | 내용 |
| --- | --- |
| `me.wav`, `remote.wav` | 트랙별 원본 녹음 (16 kHz, mono) |
| `transcript.md` / `transcript.json` | 타임스탬프와 화자가 붙은 전사문 |
| `summary.md` | 요약 |
| `session.json` | 제목, 시간, 사용한 모델, 녹음 중 생긴 문제 |

앱은 `127.0.0.1`에서만 열리고, 실행할 때마다 바뀌는 접근 토큰이 있어 다른 웹사이트가 녹음을 조작할 수 없습니다.
전사나 요약이 실패해도 **녹음은 항상 보존**되며, 앱에서 **다시 시도**를 누르면 됩니다.

## 요약 템플릿

앱의 템플릿 화면에서 Markdown을 붙여 넣어 만듭니다(예전에 쓴 노트를 그대로 붙여 넣어도 됩니다).

- 요약은 템플릿의 **섹션 제목과 순서 그대로** 만들어지고, 굵은 항목 이름·표의 열·번호 목록 같은 구조를 따릅니다.
- 템플릿의 **예시 내용은 형식 참고용**으로만 쓰이고 요약에 옮겨 적지 않습니다(옮겨 적은 문장은 자동으로 지워집니다).
- 대화에서 채울 수 없는 섹션·항목은 **비워 둡니다**. 들여 쓴 소제목(`###`)은 내용이 있을 때만 들어갑니다.
- 제목 뒤의 `← (설명)`은 작성 안내로 보고, 요약 제목에는 넣지 않습니다.
- 섹션 제목은 그대로 쓰이므로 날짜처럼 매번 바뀌는 내용은 제목에서 빼 두세요.

템플릿은 `~/.vecho/templates/`에 Markdown 파일로 저장됩니다.

```bash
vecho templates add "멘토링 노트" note.md    # 템플릿 추가
vecho templates default "멘토링 노트"        # 기본 템플릿으로
vecho templates                              # 목록 (* = 기본)
vecho summarize --template "멘토링 노트"     # 특정 템플릿으로 다시 요약
```

## 명령어

| 명령 | 설명 |
| --- | --- |
| `vecho app` | 앱 열기 (`--browser` 브라우저로, `--no-open` 서버만, `--port`) |
| `vecho setup` | AI 모델 내려받기, 상대방 소리 녹음 준비, 바로가기 만들기 (`--no-open`) |
| `vecho shortcut` | 바로가기만 다시 만들기 |
| `vecho record` | 터미널에서 녹음, Ctrl+C로 종료하면 전사·요약 (`--no-process`로 녹음만) |
| `vecho import --me a.wav --remote b.wav` | 이미 있는 오디오 파일로 세션 생성 (`--mixed`는 한 파일에 양쪽이 섞인 경우) |
| `vecho transcribe [세션]` | 전사만 다시 실행 |
| `vecho summarize [세션]` | 요약만 다시 실행 (`--template`, 모델/언어를 바꿔 재요약 가능) |
| `vecho templates [add\|show\|remove\|default]` | 요약 템플릿 관리 |
| `vecho list` | 세션 목록 |
| `vecho show [세션]` | 요약 출력 (`--transcript` 전사문, `--path` 폴더 경로) |
| `vecho devices` | 오디오 입력 장치 목록 |
| `vecho doctor` | 마이크 · 시스템 오디오 캡처 · Whisper · Ollama 점검 |

`[세션]`에는 전체 ID, 앞부분(prefix), 일부 문자열 또는 `latest`(기본값)를 쓸 수 있습니다.

```bash
vecho record --mic "MacBook"                     # 마이크를 이름 일부 또는 번호로 지정
vecho record --remote "USB"                       # 시스템 소리 대신 특정 입력 장치(이름 일부/번호) 녹음
vecho record --mic-only                           # 상대방 소리 없이 마이크만 녹음
vecho record --model small --language ko          # 더 가벼운 Whisper 모델
vecho summarize --llm-model gemma3:12b --summary-language English
```

## 설정

우선순위: 기본값 < `~/.vecho/config.toml` < `VECHO_*` 환경 변수 < 명령행 옵션.

```toml
# ~/.vecho/config.toml
whisper_model = "large-v3-turbo"   # tiny / base / small / medium / large-v3 ...
language = "ko"                    # 생략하면 자동 감지
llm_model = "qwen3.8:27b"          # 생략(또는 "auto")하면 메모리에 맞춰 선택
summary_language = "Korean"
me_label = "나"
remote_label = "상대방"
```

| 설정 | 환경 변수 | 기본값 |
| --- | --- | --- |
| `whisper_model` | `VECHO_WHISPER_MODEL` | `large-v3-turbo` |
| `whisper_backend` | `VECHO_WHISPER_BACKEND` | `auto` (Apple Silicon이면 `mlx`, 아니면 `faster-whisper`) |
| `whisper_compute_type` | `VECHO_WHISPER_COMPUTE_TYPE` | `int8` (faster-whisper 전용) |
| `language` | `VECHO_LANGUAGE` | 자동 감지 |
| `llm_host` | `VECHO_LLM_HOST` | `http://127.0.0.1:11434` |
| `llm_model` | `VECHO_LLM_MODEL` | `auto` (위 [요약 모델](#요약-모델) 표) |
| `llm_num_ctx` | `VECHO_LLM_NUM_CTX` | `0` = 메모리에 맞춰 |
| `llm_timeout` | `VECHO_LLM_TIMEOUT` | `1800` (초) |
| `llm_num_gpu` | `VECHO_LLM_NUM_GPU` | `-1` = Ollama가 결정 (`0`이면 GPU를 쓰지 않음) |
| `chunk_chars` | `VECHO_CHUNK_CHARS` | `0` = 메모리에 맞춰 (컨텍스트의 절반 이하) |
| `summary_language` | `VECHO_SUMMARY_LANGUAGE` | `Korean` |
| `sample_rate` | `VECHO_SAMPLE_RATE` | `16000` |
| `me_label` / `remote_label` | `VECHO_ME_LABEL` / `VECHO_REMOTE_LABEL` | `나` / `상대방` |
| — | `VECHO_HOME` | `~/.vecho` |

`chunk_chars`는 `llm_num_ctx`의 절반 이하(한국어 기준 토큰 ≈ 글자 수)로 유지해야 잘리지 않습니다.

## 개발

```bash
uv sync
uv run pytest        # 마이크·모델·Ollama 없이 실행되는 테스트
uv run ruff check .
scripts/build-helper.sh   # system_audio.swift를 바꿨다면 (macOS)
```
