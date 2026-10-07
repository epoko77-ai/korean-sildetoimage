# 시각 확인한 획의 국소 복구

이 경로는 **실제 획을 본 실행자가 마스크를 정하고 코드가 그 픽셀만 보간하는 보조 도구**다. OCR·자모 분석으로 자동 마스크나 정상 여부를 확정하지 않는다. 코드 이미지 편집이 현재 사용자 선택과 도구 지시에서 허용된 경우에 사용하며 이미 받은 허용은 다시 묻지 않는다.

지원 범위는 초성·종성이 같고 중성만 `ㅗ→ㅡ`, `ㅜ→ㅡ`, `ㅕ→ㅓ`인 단일 음절 오류다. 정답은 사용자 원문에서 가져온다. 일반 맞춤법 사전이나 문맥으로 정답을 새로 만들지 않는다. 같은 자모 차이라도 서체에 따라 획 위치가 달라지므로 실제 모양을 확인해야 한다.

## 위치를 찾고 수정 여부를 판단한다

정규화한 기준 PNG 전체를 보고 원문과 대조한다. 선택적 macOS OCR은 언어 교정을 끄고 글자별 위치 힌트도 남긴다.

```bash
swift "$SKILL_DIR/scripts/ocr_macos.swift" "$RUN_DIR/base.png" "$RUN_DIR/ocr.json"
python3 "$SKILL_DIR/scripts/stroke_repair.py" locate \
  --image "$RUN_DIR/base.png" --source "$RUN_DIR/source.json" \
  --ocr "$RUN_DIR/ocr.json" --out "$RUN_DIR/locations.json"
```

`locate`는 유일하게 대응되는 한 줄의 문자 차이와 OCR 글자 좌표를 제안한다. 중복 문구·여러 줄·가까운 후보가 여럿이면 `needs_visual_mapping`으로 남긴다. 비어 있거나 유효하지 않은 OCR 좌표는 `null`로 남기고 문자 차이는 유지한다. 실행자가 전체 레이아웃을 보고 원문 id와 실제 위치를 대응시킨다. 줄 전체 bbox를 글자 수로 나눠 정확한 획 위치처럼 쓰지 않는다. OCR의 글자 bbox도 넓거나 어긋날 수 있으므로 실제 픽셀로 교정한다.

원시 OCR이 원문과 같아도 전체 시각 검수는 필요하다. OCR이 틀렸지만 실제 글자는 정상일 때는 수정하지 않는다. 실제 글자를 확정하지 못하면 보류하고 관찰을 기록한다. 정상 입력의 무수정도 올바른 결과다.

실제 별도 시험에서는 `뎌`를 OCR과 실행자가 모두 `더`로 읽어 놓친 사례가 있었다. OCR 일치와 시각 검수 기록이 함께 있어도 무오류의 증거가 되지는 않는다. 문맥상 자연스러운 단어로 보완해 읽지 말고 획 자체를 확인하며, 결과 보고에서도 자동 검수 보장으로 설명하지 않는다.

### 문맥과 분리한 글자 점검

`glyph_inspect.py`는 원문에서 모음이 `ㅡ`·`ㅓ`인 음절과 개별 대응 가능한 OCR 불일치를 점검 카드로 만든다. OCR이 원문과 같아도 카드를 만든다. 부정확한 좌표 때문에 획이 잘리지 않도록 주변 여백을 포함하며, 픽셀을 최근접 방식으로 확대한다. 파란 표시가 가리키는 곳은 대략적인 위치다. 표시 밖에 있는 실제 획까지 보고 음절 전체를 읽는다.

```bash
python3 "$SKILL_DIR/scripts/glyph_inspect.py" prepare \
  --image "$RUN_DIR/base.png" --source "$RUN_DIR/source.json" \
  --ocr "$RUN_DIR/ocr.json" --out-dir "$RUN_DIR/inspection"
```

먼저 `blind.json`이 가리키는 카드 또는 묶음 이미지만 보고 한 음절씩 전사한다. 가능하면 원문을 아직 읽지 않은 별도 검수자에게 이 자료만 전달한다. 혼자 작업할 때는 이미 원문을 봤을 수 있으므로 독립 판독이라고 주장하지 않는다. 이 단계에서 `packet.json`의 정답이나 원시 OCR 전사를 참고해 읽은 글자를 고치지 않는다. 대상이 두 글자로 보이거나 획이 잘려 있으면 `uncertain`으로 남기고 전체 화면에서 위치를 다시 찾는다.

`readings.json` 구조:

```json
{
  "blind_sha256":"<blind.json SHA-256>",
  "reviewer":"<실제 판독자>",
  "entries":[
    {"id":"<카드 id>","status":"readable","observed_text":"<본 한 음절>"},
    {"id":"<다른 id>","status":"uncertain","observed_text":null,"note":"<불확실한 이유>"}
  ]
}
```

전사를 저장한 다음 정답과 대조한다.

```bash
python3 "$SKILL_DIR/scripts/glyph_inspect.py" compare \
  --packet "$RUN_DIR/inspection/packet.json" --readings "$RUN_DIR/readings.json" \
  --out "$RUN_DIR/inspection-findings.json"
```

`possible_difference`는 재확인할 위치이며 오자 확정이나 삭제 허가가 아니다. `unmapped`와 누락·불확실 판독은 시각 확인 대상으로 남는다. 카드에 없는 문구·숫자·추가 텍스트도 기존 전체 검수에서 확인한다. 이 보조 도구는 입력 이미지를 수정하지 않고 마스크도 만들지 않는다. 카드 판독과 원문 대조 후에는 **원본 문맥에서 실제 위치·전체 글자 구조를 확인**하고 아래 수정 판단으로 이어간다.

여러 줄 문단·반복 문구 때문에 OCR이 원문과 대응되지 않으면 실행자가 전체 이미지와 확대본을 보고 위치를 확인한다. 좌표를 사용자에게 요구하거나 줄 너비를 글자 수로 나누지 않는다. 다음 `manual-boxes.json`을 준비할 수 있다.

```json
{
  "kind":"glyph-manual-locations",
  "image_sha256":"<base.png SHA-256>", "source_sha256":"<source.json SHA-256>",
  "reviewer":"<실제로 위치를 본 실행자>",
  "entries":[{"item_id":"line", "expected_index":7, "glyph_bbox":[120,80,148,112],
              "note":"<어느 문구의 어느 음절을 어떻게 확인했는지>"}]
}
```

`expected_index`는 NFC 원문 문자열에서 공백·구두점도 세는 0부터 시작하는 음절 위치다. `prepare --manual-boxes manual-boxes.json`을 추가하면 해당 위치가 OCR 힌트보다 우선하고 두 좌표를 모두 기록한다. `--ocr`을 생략하면 수동으로 선택한 글자만 카드로 만들며, 전체 원문 검수는 별도로 수행한다. 이 상자는 삭제 마스크가 아니다. 파일 해시·문구 id·index·좌표가 잘못되면 거절하고 큰 상자는 미해결로 남긴다.

사용한 packet은 해당 이미지의 `workflow.py register --inspection`으로 연결하고, 판독·미해결 위치의 문맥 재확인은 [workflow.md](workflow.md)의 `glyph_inspections`에 남긴다. 이미지가 바뀐 뒤에는 최종 이미지의 카드를 새로 준비한다.

대상 단어를 최근접 방식으로 8~16배 확대하고 다음을 확인한다.

- 초성·중성·종성 중 어디가 다른지, 지울 부분과 보존할 부분이 분리되는지.
- 획을 제거한 뒤 남는 **글자 전체**가 정답 형태인지. 반대 방향의 돌출·붙은 받침·끊긴 획도 본다.
- `ㅕ→ㅓ`는 남길 가로획의 높이가 현재 서체의 `ㅓ`와 맞는지. 두 팔 중 하나를 임의로 지우지 않는다. 같은 서체의 주변 글자를 형태 근거로 참고하되 픽셀을 잘라 붙이지 않는다.
- 보간에 쓸 양옆/위아래 픽셀이 같은 배경이나 보존할 같은 윤곽인지. 다른 글자, 그림자, 사진 경계가 섞이면 보류한다.

겹받침 변경·획 추가·큰 재조판은 지원하지 않는다. `짦→짧`을 사각형 안에 줄 하나 넣는 문제로 단순화하지 않는다. 불확실한 잔여 획을 반복해서 지워 정답처럼 보이게 만들지 않는다. 다른 복구 경로가 적합하면 [repair.md](repair.md)로 돌아간다.

## 계획을 저장한다

원본을 보고 고른 마스크를 `stroke-review.json`에 기록한다. 아래 숫자는 구조 예시이며 실제 이미지에서 다시 정한다. `observed_text`는 해당 원문 id에 대응하는 전체 실제 전사다. 이 버전은 그 문구 안의 음절 하나만 다른 경우를 지원한다.

```json
{
  "image_sha256":"<base.png SHA-256>",
  "source_sha256":"<source.json SHA-256>",
  "reviewer":"<실제로 이미지를 본 실행자>",
  "item_id":"line", "status":"incorrect", "evidence":"visual+ocr",
  "observed_text":"<이미지에서 확인한 실제 문구>",
  "glyph_box":[120,80,148,112], "context_box":[80,60,440,140],
  "shape_note":"<실제 오자와 정답의 초성·중성·종성, 남길 획의 근거>",
  "background_note":"<보간용 이웃 픽셀이 적절한 이유>",
  "design_note":"<유지할 높이·굵기·기준선·색·효과>",
  "segments":[
    {"axis":"horizontal", "fixed":96, "start":132, "end":134},
    {"axis":"horizontal", "fixed":97, "start":132, "end":134}
  ]
}
```

좌표는 기준본 픽셀이다. horizontal은 y=`fixed`, x=`start..end-1`을 바꾸고 `(start-1,y)`와 `(end,y)` 사이를 선형 보간한다. vertical은 x=`fixed`, y=`start..end-1`을 바꾸고 위아래 이웃을 보간한다. 수직 돌출은 보통 horizontal, 수평 돌출은 vertical을 사용한다. 연결된 작은 마스크 하나만 허용한다. 글자 폭·높이를 재조정하거나 폰트를 덮지 않는다.

```bash
python3 "$SKILL_DIR/scripts/stroke_repair.py" plan \
  --base "$RUN_DIR/base.png" --source "$RUN_DIR/source.json" \
  --review "$RUN_DIR/stroke-review.json" --out "$RUN_DIR/plan.json"
```

출력은 계획 JSON, 이진 마스크, 원본 context, 보존 job, 분홍색 마스크 표시 확대본이다. **표시 확대본은 수정 결과가 아니다.** 이를 실제로 보고 잘못 잡힌 픽셀이나 이웃 글자가 포함되지 않았는지 확인한다. 후보 생성 전 계획을 고칠 수 있지만 변경 이유를 기록한다. 좌표·마스크를 사용자에게 요구하는 단계가 아니다.

스크립트는 마스크 크기, 연결성, 불투명 영역, 보간 이웃의 급격한 색차를 검사한다. 이것은 배경 복구나 한글 인식의 품질 보증이 아니다. 조건 때문에 거절되면 허용 범위를 넓혀 우회하지 않는다.

## 고정한 계획을 한 번 적용한다

정답 문구와 계획 설명을 `plan-text.txt`에 저장하고 다음 순서를 따른다.

```bash
python3 "$SKILL_DIR/scripts/workflow.py" init-run \
  --source "$RUN_DIR/source.json" --image "$RUN_DIR/base.png" \
  --out "$RUN_DIR/repair-run.json"
python3 "$SKILL_DIR/scripts/workflow.py" freeze \
  --source "$RUN_DIR/source.json" --prompt "$RUN_DIR/plan-text.txt" \
  --kind local-stroke --target-id line --stroke-plan "$RUN_DIR/plan.json" \
  --run "$RUN_DIR/repair-run.json" \
  --out "$RUN_DIR/request.json"
python3 "$SKILL_DIR/scripts/stroke_repair.py" apply \
  --plan "$RUN_DIR/plan.json" --request "$RUN_DIR/request.json" \
  --out "$RUN_DIR/candidate.png" --report "$RUN_DIR/patch.json"
python3 "$SKILL_DIR/scripts/workflow.py" register \
  --request "$RUN_DIR/request.json" --actual-prompt "$RUN_DIR/plan-text.txt" \
  --image "$RUN_DIR/candidate.png" --out "$RUN_DIR/candidate.json"
```

후보는 항상 `needs_review`로 시작한다. 한 계획에서 후보 하나만 만들고, 실패한 후보를 다시 깎는 연속 보정은 하지 않는다. 원본·계획·결과는 덮어쓰지 않는다.

위 `init-run`은 첫 수정에서 한 번만 실행한다. 뒤의 다른 오타를 수정할 때는 같은 run과 이전 보고서들을 `--prior-patch`로 연결한다. 이미 선택한 점검이 있으면 해당 PNG의 등록에 `--inspection`을 포함한다.

## 최종 글자와 디자인을 다시 검수한다

후보 전체와 확대본을 원본과 같은 배율로 본다. 수정 부위만 확인하지 말고 단어·완성된 음절·이웃 문구·배경을 확인한다. 원시 OCR을 다시 실행하고 [workflow.md](workflow.md)의 `review.json`을 작성한다. 결과가 애매하거나 OCR과 시각 판독이 충돌하면 관찰을 그대로 남긴다. 검수자가 단어를 정답으로 추측해서 채우지 않는다.

```bash
python3 "$SKILL_DIR/scripts/workflow.py" gate \
  --candidate "$RUN_DIR/candidate.json" --review "$RUN_DIR/review.json" \
  --ocr "$RUN_DIR/final-ocr.json" --patch-report "$RUN_DIR/patch.json" \
  --out "$RUN_DIR/gate.json"
python3 "$SKILL_DIR/scripts/workflow.py" release \
  --gate "$RUN_DIR/gate.json" --out "$RUN_DIR/final.png"
```

`gate/release`는 고정한 획 마스크 밖의 픽셀 동일성과 계획한 보간 결과를 다시 계산한다. 글자 사각형 안이더라도 마스크 밖 픽셀이 달라지면 실패한다. 정상 결과의 디자인 보존, 오류 탐지의 누락·오판·보류, 실제 수정 성공, 준비·실행 시간은 서로 다른 평가 항목으로 기록한다. 이 도구는 마스크를 자동으로 발견하지 않는다.
