# vecho

양방향 음성 대화(화상회의, 통화, 인터뷰 등)를 **내 컴퓨터에서만** 녹음하고, 글로 옮기고, 요약하는 앱입니다.
macOS · Windows · Linux에서 같은 화면으로 동작합니다. 녹음 · 음성 인식 · 요약이 모두 로컬에서 실행되므로
음성과 대화 내용이 외부로 나가지 않습니다. (처음 한 번 모델 파일을 내려받을 때만 네트워크를 씁니다.)

```
마이크 ("나") ──────────┐                          ┌─ 대화 (대본 형식)
                       ├─ 트랙별 WAV ─ Whisper ────┤
시스템 오디오 ("상대방") ┘   (me / remote)  (로컬 STT) └─ 요약 ← Ollama (로컬 LLM)
```

내 목소리(마이크)와 상대방 목소리(컴퓨터에서 재생되는 소리)를 **별도 트랙**으로 녹음하기 때문에,
화자 분리 모델 없이도 `나` / `상대방`이 정확히 구분됩니다.

![요약 화면](docs/summary.png)

## 빠른 시작

처음 한 번만 [설치](#설치)를 하고 나면, 실행은 두 단계입니다.

```bash
# 1. 요약 AI(Ollama)가 켜져 있는지 확인 — `brew services start ollama`로 등록했다면 이미 켜져 있습니다
ollama list

# 2. 앱 실행
vecho app
```

앱 창(desktop 옵션을 설치하지 않았다면 기본 브라우저)이 열립니다. 창을 닫거나 터미널에서
`Ctrl+C`를 누르면 종료되며, 녹음 중이었다면 그때까지의 녹음이 저장됩니다.
이미 실행 중일 때 `vecho app`을 다시 입력하면 새로 띄우지 않고 열려 있는 앱을 보여줍니다.

> 설치하지 않고 저장소 폴더에서 바로 실행하려면: `uv run vecho app`

## 사용법

1. **녹음 시작** — 제목은 선택입니다. 통화·회의 전에 빨간 버튼을 누르세요.
2. **대화** — Discord, Zoom, Meet, 전화 앱 등 무엇이든 됩니다. 녹음 중에는 내 목소리와 상대방 소리가 각각 파형으로 표시되고, 제목도 이때 입력할 수 있습니다.
3. **중지하고 요약하기** — 자동으로 글로 옮기고 한 줄 요약 · 핵심 내용 · 결정 사항 · 액션 아이템(체크 가능) · 미해결 질문으로 정리합니다.

그 밖에 할 수 있는 것:

- 왼쪽 목록에서 지난 대화를 **검색**하고 다시 보기
- **대화** 탭: 누가 언제 무슨 말을 했는지 대본처럼 보기. 시간을 누르면 그 부분부터 **재생** (1–2배속)
- 제목을 눌러 **이름 바꾸기**(녹음 중에도 가능), 요약 **복사**, Markdown으로 **내보내기**, `⋯` 메뉴에서 **다시 요약**·**삭제**
- 액션 아이템은 **체크**할 수 있고, 담당자와 기한이 따로 정리됩니다
- **요약 템플릿**: 원하는 틀(멘토링 노트, 주간 회의록 등)에 맞춰 요약 — [아래](#요약-템플릿) 참고
- 음성 파일(mp3, m4a, wav …)을 창에 **끌어다 놓으면** 바로 요약
- 오른쪽 위 **준비 상태**: 마이크 · 상대방 소리 · 음성 인식 · 요약 AI 점검과 해결 방법
- 단축키: `R` 녹음/중지 · `/` 검색 · `Space` 재생/정지

![대화 화면 — 재생 중인 발언이 강조되고, 타임라인에 화자별 발언 구간이 표시됩니다](docs/transcript.png)

화면은 시스템 언어(한국어/영어)와 다크 모드를 따르고, 좁은 창에서도 쓸 수 있습니다.
글꼴은 [Pretendard](https://github.com/orioncactus/pretendard)(SIL OFL 1.1)를 앱에 포함해 어느 OS에서나 같은 모양으로 보입니다.
같은 기능을 터미널에서 쓰려면 아래 [명령어](#명령어)를 보세요.

### 요약 템플릿

요약을 정해진 틀에 맞춰 받고 싶다면 템플릿을 만드세요. 왼쪽 위 템플릿 아이콘 → **새 템플릿**에서
이름을 정하고 Markdown을 붙여 넣으면 됩니다. **예전에 작성한 노트나 회의록을 그대로 붙여 넣어도 됩니다.**

```markdown
## 1. 현황 스냅샷

- **지원 현황**
- **면접**

## 2. 이번 주 숙제

| 할 일 | 기한 |
| --- | --- |
```

- 요약은 템플릿의 **섹션 제목과 순서 그대로** 만들어지고, 굵은 항목 이름·표의 열·번호 목록 같은 구조를 따릅니다.
- 템플릿에 들어 있는 **예시 내용은 형식 참고용**으로만 쓰이고 요약에 옮겨 적지 않습니다(옮겨 적은 문장은 자동으로 지워집니다).
- 대화에서 채울 수 없는 섹션·항목은 **비워 둡니다**. 들여 쓴 소제목(`###`)은 해당 내용이 있을 때만 들어갑니다.
- 섹션 제목은 그대로 쓰이므로 날짜처럼 매번 바뀌는 내용은 제목에서 빼 두세요.

템플릿은 녹음 패널의 **요약 틀**에서 골라 두면 이후 녹음·가져오기에 쓰이고, 이미 만든 요약은
세션 제목 아래의 템플릿 버튼으로 다른 템플릿을 골라 다시 요약할 수 있습니다.
템플릿은 `~/.vecho/templates/`에 Markdown 파일로 저장됩니다.

터미널에서는:

```bash
vecho templates add "멘토링 노트" note.md    # 템플릿 추가
vecho templates default "멘토링 노트"        # 기본 템플릿으로
vecho templates                              # 목록 (* = 기본)
vecho summarize --template "멘토링 노트"     # 특정 템플릿으로 다시 요약
```

## 설치

필요한 것: Python 3.11 – 3.13, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com)

```bash
git clone https://github.com/Nhahan/vecho.git && cd vecho
uv tool install --python 3.12 --editable ".[desktop]"   # `vecho` 명령 설치 (desktop: 전용 창)

brew install ollama && brew services start ollama       # 요약 AI (로그인 시 자동 실행)
ollama pull qwen3.8:27b                                 # 기본 요약 모델 (약 17GB, 메모리 32GB 이상 권장)

vecho doctor                                            # 준비 상태 점검 (모두 OK면 준비 끝)
```

음성 인식 모델(약 1.6GB)은 첫 전사 때 자동으로 내려받습니다.

메모리가 적다면 더 작은 모델을 쓰세요: `ollama pull qwen3:8b` 후 `VECHO_LLM_MODEL=qwen3:8b` ([설정](#설정)).

### 음성 인식 속도

Apple Silicon Mac에서는 **Mac GPU**(MLX)로, 그 밖의 환경에서는 CPU(faster-whisper)로 전사합니다.
M4 Max 기준 68초짜리 대화(두 트랙)를 약 5초에 전사합니다(CPU로는 약 65초).
두 방식 모두 말소리 구간만 골라 전사하므로, 조용한 구간에 없는 말("감사합니다" 등)이 생기지 않습니다.
`whisper_backend` 설정으로 직접 고를 수 있습니다([설정](#설정)).

### 상대방 소리는 어떻게 녹음되나요?

**가상 오디오 드라이버, 관리자 권한, 소리 설정 변경이 필요 없습니다.** 컴퓨터에서 재생되는 소리를 그대로 녹음하므로
스피커·이어폰·헤드셋 어느 것으로 듣든, 통화 중에 바꿔도 계속 녹음되고 내가 듣는 소리에는 영향이 없습니다.

| OS | 방식 | 필요한 것 |
| --- | --- | --- |
| macOS 14.4+ | Core Audio 프로세스 탭 | Xcode Command Line Tools (`xcode-select --install`, 첫 녹음 때 캡처 도우미를 한 번 컴파일). 처음 녹음할 때 **시스템 오디오 녹음** 권한을 허용 |
| Windows 10/11 | WASAPI 루프백 | 없음 |
| Linux | PulseAudio / PipeWire 모니터 | `pipewire-pulse` 또는 PulseAudio (대부분의 데스크톱 배포판에 기본 포함) |

상대방 소리가 녹음되지 않았다면 앱이 종료 직후 원인과 해결 방법을 알려줍니다. (macOS에서는 대개
시스템 설정 → 개인정보 보호 및 보안 → **화면 및 시스템 오디오 녹음**에서 앱/터미널을 허용하면 해결됩니다.)

> 이어폰을 쓰면 가장 깨끗합니다. 스피커로 들으면 상대방 소리가 마이크에 다시 들어가지만,
> **마이크 쪽 에코는 자동으로 제거**됩니다 (6글자 미만의 짧은 맞장구는 구분이 어려워 그대로 둡니다).

### 저장 위치와 개인정보

모든 데이터는 `~/.vecho/sessions/<날짜-제목>/`에 저장됩니다 (Windows: `C:\Users\<이름>\.vecho`).

| 파일 | 내용 |
| --- | --- |
| `me.wav`, `remote.wav` | 트랙별 원본 녹음 (16 kHz, mono) |
| `transcript.md` / `transcript.json` | 타임스탬프와 화자가 붙은 전사문 |
| `summary.md` | 요약 |
| `session.json` | 제목, 시간, 사용한 모델, 녹음 중 생긴 문제 |

앱은 `127.0.0.1`에서만 열리고, 실행할 때마다 바뀌는 접근 토큰이 있어 다른 웹사이트가 녹음을 조작할 수 없습니다.
전사나 요약이 실패해도 **녹음은 항상 보존**되며, 앱에서 **다시 시도**를 누르면 됩니다.

## 명령어

| 명령 | 설명 |
| --- | --- |
| `vecho app` | 앱 열기 (`--browser` 브라우저로, `--no-open` 서버만, `--port`) |
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

자주 쓰는 옵션:

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
llm_model = "qwen3.8:27b"
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
- 처음 녹음할 때 macOS가 터미널 앱의 **마이크**와 **시스템 오디오 녹음** 권한을 묻습니다. 허용해야 녹음됩니다.

## 라이선스

[MIT](LICENSE)
