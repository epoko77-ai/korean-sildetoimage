# Korean Slide Image

한글 슬라이드·인포그래픽을 만들고 이미지 속 오타를 검수·교정하는 Codex 스킬입니다. 원문과 실제 프롬프트를 고정하고, 생성한 PNG를 시각·OCR로 대조하며, 허용된 부분 합성 뒤에는 수정 영역 밖 픽셀을 검사합니다.

현재는 **실사용 v1**입니다. 파일 연결과 합성 검사는 자동화되어 있고, 글자의 모양과 디자인에 대한 최종 판정은 실행자의 시각 검수가 필요합니다. 한글 무오류를 보장하지 않습니다.

## 설치

Codex에서 다음과 같이 요청합니다.

```text
$skill-installer로 아래 GitHub 스킬을 설치해줘.
https://github.com/epoko77-ai/korean-slide-image/tree/main/skills/korean-slide-image
```

스킬 설치 후 다음 대화 턴부터 사용할 수 있습니다. 이미 같은 이름의 스킬이 설치돼 있으면 기존 작업을 보관한 뒤 업데이트 경로를 선택합니다.

실제 이미지 생성에는 Codex에 제공되는 내장 이미지 생성 도구가 필요합니다. 이 저장소의 Python 스크립트가 이미지 생성 API를 직접 호출하지는 않습니다. 로컬 검사·합성에는 Python 3.10 이상과 [Pillow](requirements.txt)가 필요합니다. 선택적 OCR 도구는 macOS의 Swift와 Apple Vision을 사용합니다. OCR을 사용할 수 없는 환경의 시각 검수 경로는 [실행 절차](skills/korean-slide-image/references/workflow.md)에 설명돼 있습니다.

## 사용

새 이미지를 만들 때:

```text
$korean-slide-image로 첨부한 슬라이드를 이미지로 만들어줘.
한글과 숫자는 원문 그대로 유지하고 검수해줘.
오타가 있으면 기존 디자인에 맞춰 수정하고, 수정 영역만 코드로 합성해도 돼.
```

기존 이미지의 오타를 고칠 때:

```text
$korean-slide-image로 이 이미지의 ‘중가’를 ‘증가’로 고쳐줘.
주변 디자인을 유지하고, 필요한 부분의 폰트 재렌더링과 코드 합성을 허용할게.
```

사용자가 허용한 생성·편집·합성 범위를 따릅니다. 글꼴 변경과 코드 합성이 자동으로 모든 작업에 허용되는 것은 아닙니다.

## 처리 과정

| 단계 | 역할 |
|---|---|
| `freeze` | 원문·실제 프롬프트·참조 이미지와 수정 영역을 고정하고 문구 포함 여부 검사 |
| `register` | 실제 PNG를 디코딩하고 파일들을 해시로 연결 |
| `gate` | 문구별 시각 판정, 원시 OCR, 추가·누락 문구, 디자인 및 요청한 보존 검사 확인 |
| `release` | 검수 증거를 다시 확인하고 통과한 PNG 바이트를 그대로 출고 |

문구와 디자인 검수가 끝나지 않으면 `needs_review`, 확인된 오류가 남으면 `unresolved`, 필요한 검사를 통과하면 `verified`로 기록합니다. OCR 일치만으로 합격시키지 않습니다.

- [스킬 지침](skills/korean-slide-image/SKILL.md)
- [실행 명령과 검수 기록 형식](skills/korean-slide-image/references/workflow.md)
- [부분 편집·폰트 복구·픽셀 보존](skills/korean-slide-image/references/repair.md)
- [원문 추출과 OCR](skills/korean-slide-image/references/text-and-evidence.md)

## 검증과 현재 한계

기본 테스트 **32개**는 문자열 비교, 오래된 검수 기록 거절, 합성 영역과 파일 연결을 확인합니다. 실제 이미지 실험은 한 장의 슬라이드에서 생성 4회·생성형 편집 1회·로컬 폰트 후보 2개를 비교한 범위입니다. 일반적인 한글 정확도 수치로 해석하면 안 됩니다.

현재 보완할 항목:

1. `release`의 보존 검사는 마지막 수정 단계에 한정됩니다. 여러 번 수정한 결과는 최초 원본부터 `patch_image.py verify`를 별도로 실행해야 합니다.
2. `freeze`는 부분 문자열 포함 검사입니다. 문구별 위치나 반복 횟수까지 자동으로 확인하지 않습니다.
3. 폰트 기반 복구의 지침은 있지만, 모든 이미지에 적용할 공용 자동 복구 명령은 아직 없습니다.
4. 최종 글자 판정은 실행자의 관측입니다. 작은 글씨·복잡한 배경·여러 장의 자동 생산에 대한 폭넓은 검증은 남아 있습니다.

자세한 근거와 보완 순서는 [검증 범위](docs/validation.md)를 참고하세요.

## 개발 및 테스트

저장소를 받은 뒤 가상 환경에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -B -m unittest discover -s skills/korean-slide-image/scripts -p 'test_*.py' -v
```

GitHub Actions에서도 같은 테스트를 실행합니다. 이미지 생성과 OCR은 CI에서 실행하지 않습니다. 생성 이미지, 실험 원문, 로컬 경로가 포함된 작업 기록과 폰트 파일은 배포 패키지에 포함하지 않습니다.
