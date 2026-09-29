# vecho

양방향 음성 대화(화상회의, 통화, 인터뷰 등)를 **내 컴퓨터에서만** 녹음하고, 전사하고, 요약하는 CLI 서비스입니다.
녹음 · 음성 인식 · 요약이 모두 로컬에서 실행되므로 오디오와 대화 내용이 외부 서버로 나가지 않습니다.
(최초 실행 시 모델 파일을 내려받을 때만 네트워크를 사용합니다.)

```
마이크 ("나") ──────────┐                               ┌─ transcript.md
                       ├─ 트랙별 WAV ─ faster-whisper ─┤
시스템 오디오 ("상대방") ┘   (me / remote)   (STT, 로컬) └─ summary.md ← Ollama (LLM, 로컬)
```

내 목소리(마이크)와 상대방 목소리(시스템 출력)를 **별도 트랙**으로 녹음하기 때문에, 화자 분리 모델 없이도
`나: …` / `상대방: …` 형태로 정확하게 화자가 구분됩니다.

## 요구 사항

- macOS 14.4 이상 (Apple Silicon 권장)
- Python 3.11 – 3.13, [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) — 요약용 로컬 LLM
- Xcode Command Line Tools (`xcode-select --install`) — 첫 실행 때 시스템 오디오 캡처 헬퍼(Swift)를 한 번 컴파일합니다

**가상 오디오 드라이버(BlackHole 등)나 관리자 권한, 소리 설정 변경은 필요 없습니다.**

## 설치

```bash
git clone https://github.com/Nhahan/vecho.git && cd vecho
uv tool install --editable .           # 어디서나 `vecho` 명령 사용

brew install ollama
brew services start ollama             # 로그인 시 자동 실행
ollama pull qwen3.8:27b                # 기본 요약 모델 (약 17GB, 메모리 32GB 이상 권장)

vecho doctor                           # 환경 점검
```

### 상대방 소리 캡처 (설정 없음)

Discord · Zoom · Meet · FaceTime · 카카오톡 등 **Mac에서 재생되는 모든 앱의 소리**를 상대방 트랙으로 녹음합니다.
macOS의 Core Audio 프로세스 탭을 사용하므로 다음이 모두 성립합니다.

- 가상 오디오 장치를 설치하지 않고, 사운드 설정 목록에 아무것도 추가되지 않습니다.
- **소리 출력 장치를 바꾸지 않습니다.** 내가 듣는 소리에는 아무 영향이 없고 볼륨 키도 그대로 동작합니다.
- 스피커, AirPods, USB 헤드셋, 모니터 어느 것으로 듣든, 통화 중에 바꿔도 끊김 없이 녹음됩니다.
- 앱이 특정 출력 장치로 고정돼 있어도 녹음됩니다.

처음 녹음할 때 macOS가 터미널 앱의 **시스템 오디오 녹음** 권한을 묻습니다. 허용하세요.
상대방 트랙이 무음이라고 경고가 나오면 시스템 설정 → 개인정보 보호 및 보안 →
**화면 및 시스템 오디오 녹음**에서 사용 중인 터미널 앱을 켜고, 재생 중인 소리가 있는지 확인하세요.

> 헤드폰을 쓰면 가장 깨끗합니다. 스피커로 들으면 상대방 소리가 마이크에 다시 들어가지만,
> 깨끗한 시스템 오디오 트랙과 비교해 **마이크 쪽 에코는 전사 단계에서 자동으로 제거**됩니다
> (6글자 미만의 짧은 맞장구는 구분이 어려워 그대로 둡니다).

<details>
<summary>macOS 14.4 미만 (BlackHole 사용)</summary>

시스템 오디오 캡처를 쓸 수 없는 구형 macOS에서는 루프백 장치로 대체됩니다.

```bash
brew install --cask blackhole-2ch switchaudio-osx   # BlackHole 설치는 관리자 비밀번호 필요
sudo killall coreaudiod                             # 드라이버 로드 (재부팅해도 됨)
```

`vecho record`가 BlackHole을 자동으로 찾아, 녹음하는 동안에만 현재 출력 장치와 BlackHole로 동시에 내보내는
`vecho Multi-Output`을 만들어 선택하고 종료하면 원래 출력으로 되돌립니다(통화 중 출력이 바뀌면 따라갑니다).
`--no-routing`으로 자동 전환을 끌 수 있고, `vecho setup [--remove]`로 장치를 미리 만들거나 지울 수 있습니다.
이 방식에서는 앱의 출력 장치가 **기본값**이어야 하며, 특정 장치로 고정된 앱은 녹음되지 않습니다.

</details>

## 빠른 시작

```bash
vecho record --title "주간 회의" --language ko
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
| `vecho setup` | (구형 macOS·BlackHole 전용) Multi-Output 장치 미리 만들기 / `--remove` 삭제 |
| `vecho record` | 마이크 + 시스템 오디오 녹음 후 전사·요약 (`--no-process`로 녹음만) |
| `vecho import --me a.wav --remote b.wav` | 이미 있는 오디오 파일로 세션 생성 (`--mixed`는 한 파일에 양쪽이 섞인 경우) |
| `vecho transcribe [세션]` | 전사만 다시 실행 |
| `vecho summarize [세션]` | 요약만 다시 실행 (모델/언어를 바꿔 재요약 가능) |
| `vecho list` | 세션 목록 |
| `vecho show [세션]` | 요약 출력 (`--transcript` 전사문, `--path` 폴더 경로) |
| `vecho devices` | 오디오 입력 장치 목록 (`loopback` 표시) |
| `vecho doctor` | 마이크 · 시스템 오디오 캡처 · Whisper · Ollama 점검 |

`[세션]`에는 전체 ID, 앞부분(prefix), 일부 문자열 또는 `latest`(기본값)를 쓸 수 있습니다.

자주 쓰는 옵션:

```bash
vecho record --mic "MacBook"                     # 마이크를 이름 일부 또는 번호로 지정
vecho record --remote blackhole                   # 시스템 오디오 대신 루프백 장치 사용
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
