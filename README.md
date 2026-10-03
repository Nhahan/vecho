# vecho

대화를 녹음하고 요약합니다.

![vecho](docs/summary.png)

## 사양

| | 최소 | 권장 |
| --- | --- | --- |
| macOS | macOS 13, 메모리 8GB, 저장 공간 7GB | Apple Silicon, macOS 14.4 이상, 메모리 16GB 이상 |
| Windows | Windows 10, 메모리 8GB, 저장 공간 7GB | Windows 11, 메모리 16GB 이상 |

- 상대방 목소리 녹음은 macOS 14.4 이상에서 됩니다.
- 요약 모델은 메모리에 맞춰 설치됩니다: 32GB 이상 `qwen3.8:27b`(약 25GB 필요), 16GB 이상 `qwen3:8b`, 그 미만 `qwen3:4b`.

## 설치

macOS

```
curl -fsSL https://raw.githubusercontent.com/Nhahan/vecho/main/install.sh | sh
```

Windows (PowerShell)

```
irm https://raw.githubusercontent.com/Nhahan/vecho/main/install.ps1 | iex
```

업데이트할 때도 같은 명령을 실행하면 됩니다. 삭제는 `install`을 `uninstall`로 바꿔 실행하세요.

## 사용

vecho를 열고 **녹음 시작**을 누르세요. 녹음을 멈추면 대화 기록과 요약이 만들어집니다.

자세한 설명은 [docs/GUIDE.md](docs/GUIDE.md)에 있습니다.
