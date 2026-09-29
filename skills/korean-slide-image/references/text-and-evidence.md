# 원문과 관측 기록

이 문서의 `compare`는 문자열 대조 도구다. 실제 이미지의 시각 판정·OCR 오인식 처리·최종 출고는 [workflow.md](workflow.md)의 `review.json`과 `gate`를 사용한다. 아래 관측 JSON과 시각 검수 JSON은 서로 다른 형식이다.

## 원문 추출

`SKILL_DIR`는 이 스킬 폴더의 절대 경로, `RUN_DIR`는 작업 출력 폴더로 정한다. 원본 PPTX는 읽기만 한다.

```bash
python3 "$SKILL_DIR/scripts/slide_text.py" extract deck.pptx --out "$RUN_DIR/source.json"
```

Python 표준 라이브러리만 필요하다. 실제 프레젠테이션의 슬라이드 순서를 따르고, 노트는 제외한다. 각 문단의 연속 run을 합치며 명시적 줄바꿈은 보존한다. 자동 bullet·번호, 글꼴 효과, 마스터·레이아웃의 글자, 그림·차트·SmartArt의 글자는 완전 추출하지 않으므로 렌더 이미지와 대조한다. 숨겨진 슬라이드와 그래픽 포함 여부는 메타데이터에 표시된다. 추출 결과 자체는 정답 목록 초안이며, 보이는 요소인지 확인하고 필요한 항목을 보완한 후 확정한다.

PPTX가 아니면 같은 구조의 JSON을 만든다. 원문 수정은 별도 파일 버전으로 기록한다.

```json
{
  "items": [
    {"id": "s01-title", "text": "지역 경제 활성화"},
    {"id": "s01-value", "text": "2026년 3.5% 증가"}
  ]
}
```

## 관측 문구

이미지에서 **실제로 읽은 값**을 기록한다. OCR이 맞춤법을 보정한 값, 정답을 보고 끼워 맞춘 값은 관측이 아니다.

```json
{
  "image": "slide-01-final.png",
  "items": [
    {"id": "s01-title", "text": "지역 경제 활성화", "evidence": "visual+ocr", "bbox": [100, 80, 920, 180]},
    {"id": "s01-value", "text": "2026년 3.6% 증가", "evidence": "ocr", "bbox": [100, 250, 620, 320]}
  ]
}
```

box는 좌상단 원점 `[left, top, right, bottom]`, 우측·하단 경계는 제외한다. OCR 결과를 원문의 id에 대응시키는 일은 위치·구조를 보고 수행한다. 반복되는 문구도 하나로 합치지 않는다. 대응되지 않는 실제 문구는 새 id로 넣어 추가 문구로 검출되게 한다. OCR이 누락한 글자는 이미지에서 확인하고 관측 근거를 명시한다.

최종 관측 JSON에는 해당 이미지의 `image_sha256`도 넣는다. 아래 로컬 OCR은 이 값을 자동 기록한다. 시각 전사라면 실제로 확인한 이미지 파일에서 SHA-256을 계산해 기록한다. 파일이 바뀌면 다시 관측하며, 과거 기록의 해시만 새 값으로 바꾸지 않는다.

```bash
python3 "$SKILL_DIR/scripts/slide_text.py" compare \
  --source "$RUN_DIR/source.json" --observed "$RUN_DIR/observed.json" \
  --image "$RUN_DIR/slide-01-final.png" \
  --out "$RUN_DIR/text-check.json"
```

종료 코드: 0 = 정규화 후 전부 일치, 2 = 검토할 차이 있음, 1 = 잘못된 입력·실행 오류. 0도 시각 검수 합격을 뜻하지 않는다. 중복 id는 실행 오류로 거절한다.

`--image`는 관측 기록의 해시를 실제 파일과 비교하여 빠졌거나 오래된 기록을 거절한다. 문구만 시험할 때는 생략할 수 있으나, 결과의 `image_binding_checked`가 false이면 최종 이미지에 대한 검증으로 보고하지 않는다.

`exact`, `whitespace_difference`, `mismatch`, `missing`, `unexpected`를 구분한다. NFC의 조합형 한글은 같은 문자로 비교하지만 NFKC나 대소문자 변환은 하지 않는다. 띄어쓰기·문장부호를 지워서 합격시키지 않는다.

## macOS 로컬 OCR

Apple Vision과 Swift 실행 환경이 있는 경우만 사용한다. 별도 서비스 업로드나 API 키가 없다. 먼저 아래 이미지 정규화 과정으로 만든 PNG를 사용한다. 이 스크립트는 EXIF 회전을 추론하지 않고 정규화된 PNG의 픽셀 좌표를 보고한다.

```bash
swift "$SKILL_DIR/scripts/ocr_macos.swift" "$RUN_DIR/base.png" "$RUN_DIR/ocr-raw.json"
```

여러 장은 `입력.png 출력.json` 쌍을 이어서 전달할 수 있다. 각 결과는 해당 이미지에 따로 묶이며 기존 출력은 덮어쓰지 않는다. 배치 중 오류가 나면 앞서 완료된 파일은 보존되고 이후 쌍은 실행되지 않는다.

첫 실행의 Swift 모듈 캐시 위치가 제한되면 허용된 작업 폴더를 `swift -module-cache-path <폴더>`로 지정한다. Vision 실행이 불가능하면 오류를 숨기거나 성공 결과를 만들지 말고, 사용 가능한 OCR 또는 직접 시각 전사로 전환하며 방법을 기록한다.

언어는 `ko-KR`, `en-US`, 정확도 모드, 언어 교정 비활성화다. 해당 런타임의 지원 언어를 확인하고 한글이 없으면 실패한다. 후보 문구·신뢰도·좌표를 저장한다. 신뢰도는 맞춤법 정확도 확률이 아니다. 원시 관측은 자동 읽기 순서나 원문 id 매핑을 보장하지 않는다. 두 번째 OCR 후보를 정답에 맞춰 선택한 뒤 ‘OCR 일치’로 보고하지 않는다.

Apple 설명: [언어 교정](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/useslanguagecorrection), [지원 언어](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/supportedrecognitionlanguages()).

## 장별 최종 기록

작업 규모에 맞게 하나의 JSON 또는 Markdown에 남긴다. 불필요한 데이터베이스는 만들지 않는다.

```json
{
  "slide": 1,
  "status": "needs_review",
  "baseline": "slide-01-base.png",
  "final": "slide-01-final.png",
  "text_check": "text-check.json",
  "visual_text_review": "pending",
  "visual_style_review": "passed",
  "pixel_preservation": "pixel-check.json",
  "generation_calls": 1,
  "edit_calls": 1,
  "unresolved": ["각주 두 번째 단어의 받침 확인 필요"]
}
```

OCR 결과가 없으면 미실행으로 적는다. 원본 대비 픽셀 검사를 실행하지 않았으면 보존을 증명했다고 쓰지 않는다. 실제 이미지 생성 없이 합성 테스트만 했다면 ‘기계적 검사 검증, 생성 품질 미검증’으로 구분한다.
