# 호출 전 고정부터 최종 출고까지

`SKILL_DIR`는 설치된 스킬 폴더, `RUN_DIR`는 이번 작업 폴더의 절대 경로로 정한다. `workflow.py`와 `patch_image.py`는 Pillow가 필요하다. 원문 추출·문자열 대조는 표준 라이브러리, 선택적 로컬 OCR은 macOS의 Swift와 Vision을 쓴다. 별도 이미지 API나 API 키는 필요 없다. 다른 환경에서는 사용 가능한 OCR 또는 이유를 명시한 시각 전사를 쓴다.

## 1. 실제 요청 고정

한 장의 필수 문구마다 고유 id를 준다. 같은 문장이 두 곳에 있으면 서로 다른 id로 남긴다. 작업 전체의 여러 장을 한 후보의 원문에 섞지 않는다.

```json
{"items":[
  {"id":"title","text":"지역 경제 활성화"},
  {"id":"value","text":"2026년 3.5% 증가"}
]}
```

확정한 원문은 `source.json`, **도구에 실제로 보낼 전체 문자열**은 `prompt.txt`에 저장한다. 디자인 지시에서는 id별 위치와 위계를 정하고, 별도 문구 목록에 정확한 문자열을 한 번씩 제시한다. 출처·설명·금지 예시가 화면에 들어갈 문구로 혼동되지 않게 구분한다. 한글을 자모 단위로 분해해 화면에 쓰라고 하지 않는다.

```bash
python3 "$SKILL_DIR/scripts/workflow.py" freeze \
  --source "$RUN_DIR/source.json" --prompt "$RUN_DIR/prompt.txt" \
  --out "$RUN_DIR/request.json"
```

참조 이미지가 있으면 `--reference /absolute/reference.png`를 반복한다. 각 이미지의 역할은 프롬프트에 적는다. 생성에서는 모든 문구가 필요하다. 편집에서는 `--kind edit --target-id value`처럼 수정 대상만 프롬프트에 넣을 수 있지만 최종 검수는 **원문 전체**를 대상으로 한다. 폰트 합성 계획은 `--kind local-font`로 기록한다.

검사는 중복 id, 빈 원문, U+FFFD, 프롬프트의 필수 문구 누락을 거절한다. 원문과 프롬프트 파일은 고치지 않는다. NFC와 JSON 이스케이프 표현만 비교에 허용한다. 의미 충돌, 가독성, 올바른 참조 역할까지 자동 검사하지는 않는다. 예를 들어 원문을 포함한 뒤 ‘그 문구를 생략하라’고 쓴 요청도 문자열 검사는 통과하므로 실제 지시는 별도 검토한다.

고정한 프롬프트의 내용을 그대로 이미지 도구에 전달한다. 이 스크립트가 도구 호출 로그를 자동 수집하지는 않는다. 실제 전달 여부는 실행자가 책임지고 확인하며, 도구 응답의 산출물 경로·호출 ID가 있으면 보관한다. 사후 복구한 과거 기록을 호출 전에 검사한 것처럼 보고하지 않는다.

## 2. 결과 등록

```bash
python3 "$SKILL_DIR/scripts/workflow.py" register \
  --request "$RUN_DIR/request.json" --actual-prompt "$RUN_DIR/prompt.txt" \
  --image "$RUN_DIR/candidate.png" --out "$RUN_DIR/candidate.json"
```

고정한 자료·실제 프롬프트의 해시를 확인하고 PNG를 실제로 디코딩한다. 크기 요청을 도구가 따랐는지도 직접 확인한다. 상태는 `needs_review`로 시작한다. JPEG·EXIF 회전 이미지는 `patch_image.py prepare`로 별도 정규화한 뒤 등록한다. 파일 바이트가 바뀌면 새 후보다. 이미 존재하는 파일은 덮어쓰지 않는다.

## 3. 원시 OCR과 시각 판정

가능하면 정답을 제공하지 않은 OCR을 먼저 실행한다. 로컬 예:

```bash
swift "$SKILL_DIR/scripts/ocr_macos.swift" \
  "$RUN_DIR/candidate.png" "$RUN_DIR/ocr-raw.json"
```

전체 이미지와 각 문구의 확대 부분을 실제로 본 뒤 아래 형식의 `review.json`을 만든다. 해시는 해당 파일에서 계산한다. 같은 해시를 붙였다는 이유만으로 검수가 수행된 것은 아니다. 각 `bbox`는 원본 픽셀 좌표 `[left, top, right, bottom]`이며 우측·하단 경계는 제외한다.

```json
{
  "image_sha256": "<candidate.png의 SHA-256>",
  "source_sha256": "<source.json의 SHA-256>",
  "reviewer": "<실제로 전체와 확대 이미지를 확인한 사람 또는 에이전트>",
  "items": [
    {"id":"title", "status":"correct", "observed_text":"지역 경제 활성화",
     "evidence":"visual+ocr", "bbox":[60,40,1000,140], "ocr_ids":["ocr-0001"]},
    {"id":"value", "status":"correct", "observed_text":"2026년 3.5% 증가",
     "evidence":"visual+ocr", "bbox":[80,250,620,310], "ocr_ids":["ocr-0002"],
     "ocr_note":"OCR은 ‘중가’로 읽었으나 확대 이미지에서 ㅡ와 ㅇ 받침을 확인함."}
  ],
  "ignored_ocr": [],
  "inventory": {"status":"complete", "unexpected_text":[]},
  "design": {"status":"passed", "note":"전체 축소본의 가독성과 글자 기준선·자간·배경 경계를 확인함."}
}
```

이 예시의 판정을 복사해서 실제 검사 대신 사용하지 않는다.

- `observed_text`: 이미지에서 읽은 문자열. 잘못 그린 획이 있으면 읽힌 오자를 쓰고 `incorrect`로 표시한다. 판독이 안 되면 `uncertain`으로 남긴다. 원문의 줄바꿈 자체는 보존하며, 이미지 배치 때문에 생긴 자동 줄바꿈은 논리 문구로 전사하고 그 차이를 설명한다.
- `status`: `correct`, `incorrect`, `uncertain`. `evidence`: `visual`, `visual+ocr`, `ocr`. OCR만 확인했거나 불확실한 항목은 통과하지 않는다.
- `ocr_ids`: 그 문구에 해당하는 원시 관측 id를 읽기 순서대로 나열한다. OCR 줄 결합에는 줄바꿈이 유지된다. 줄배치·띄어쓰기·기호 불일치 또는 OCR 누락은 `ocr_note`에 실제 확대 확인 결과를 적는다. OCR을 정답으로 덮어쓰지 않는다.
- 모든 OCR 관측은 한 번만 대응시킨다. 글자가 아닌 그림자를 OCR이 오인한 경우에만 `ignored_ocr`에 `observation_id`와 시각 확인한 `reason`을 남긴다. 실제 추가 문구는 `inventory.unexpected_text`에 기록한다.
- `inventory.status`는 전체 화면에서 누락·추가 글자를 찾았을 때만 `complete`. `design.status`는 `passed`, `failed`, `not_checked`. 교정 후보는 기준본과 같은 배율의 확대 영역 및 실제 표시 크기의 전체 화면을 비교한다. `design.note`에는 비교한 기준본과 글자 폭·굵기·기준선·자간·효과·배경 경계의 관찰을 기록한다. 정상 문자열이라도 다른 서체로 보이거나 배경·합성 경계가 달라지면 실패다. 자세한 채택 기준은 [repair.md](repair.md)를 따른다.
- OCR이 실행 불가능하면 `ocr_unavailable_reason`에 구체적 이유를 적고 `evidence: visual`로 실제 전사한다. 일부 OCR 실패를 전체 시각 검수 완료로 바꾸지 않는다.

```bash
python3 "$SKILL_DIR/scripts/workflow.py" gate \
  --candidate "$RUN_DIR/candidate.json" --review "$RUN_DIR/review.json" \
  --ocr "$RUN_DIR/ocr-raw.json" --out "$RUN_DIR/gate.json"
```

직접 시각 전사 경로에서는 `--ocr`을 생략한다. 오류가 남으면 `unresolved`, 누락된 검수·미판독 항목이 있으면 `needs_review`, 문구·디자인 및 요청한 보존 검사를 통과하면 `verified`다. 종료 코드 0은 통과, 2는 미통과, 1은 입력·실행 오류다. 실패 뒤 기존 기록을 고쳐 덮지 말고 새 버전으로 검수한다.

## 4. 수정 결과 연결

코드 합성이 현재 지시와 도구 규칙에서 허용되었으면 [repair.md](repair.md)에 따라 **수정 전에** 기준본과 영역을 고정한다. 사용 예시에 합성·폰트 교체를 일괄 허용하는 문장을 요구하지 않는다. 수단의 허용과 디자인 변경 여부는 별개로 판단한다.

```bash
python3 "$SKILL_DIR/scripts/workflow.py" freeze \
  --source "$RUN_DIR/source.json" --prompt "$RUN_DIR/edit-prompt.txt" \
  --kind edit --target-id value --reference "$RUN_DIR/edit-input.png" \
  --preserve-job "$RUN_DIR/edit-job.json" --out "$RUN_DIR/edit-request.json"
```

합성 결과를 `register`한 후 `gate`에 `--patch-report "$RUN_DIR/edit-check.json"`을 추가한다. 보고서의 `passed` 값만 믿지 않고 고정한 기준본과 실제 출력의 영역 밖 RGBA 픽셀을 다시 비교한다. 요청 전에 고정하지 않은 영역이나 다른 작업의 보고서를 받아들이지 않는다. 수정 영역 안의 글자·디자인은 여전히 시각 판정이 필요하다.

서로 다른 오류를 순차 수정하면 단계별 job·후보·보고서를 보관한다. 마지막 `gate`의 보존 판정 범위는 **해당 요청의 기준본과 수정 영역**이다. 최초 원본 대비 누적 보존을 주장하려면 `patch_image.py verify`로 전체 보고서 연결과 영역 합집합 밖을 별도 검사한다. 임의로 승인 영역을 넓혀 통과시키지 않는다.

## 5. 최종 PNG 출고

```bash
python3 "$SKILL_DIR/scripts/workflow.py" release \
  --gate "$RUN_DIR/gate.json" --out "$RUN_DIR/final.png"
```

검수 입력의 해시를 확인하고 `gate`를 다시 계산한다. 통과한 PNG 바이트를 그대로 복사하고 `final.png.receipt.json`을 남긴다. 저장된 상태만 `verified`로 고치거나, PNG·원문·OCR·검수 기록이 바뀌면 출고할 수 없다. 기존 final은 덮어쓰지 않는다. 내보낸 PNG를 변형하거나 다른 파일로 바꾼 경우 이전 영수증을 재사용하지 않는다.

이 절차는 검수 누락과 파일 혼동을 막는다. 시각 판정 자체는 실행자의 관측이며 암호학적으로 보증된 무오류 인증이 아니다. 전체 과정·사용한 방법·미해결 내용을 사실대로 보고한다.

## 설계 근거와 한계

- [OpenAI 이미지 생성 문서](https://developers.openai.com/api/docs/guides/image-generation): 정밀한 글자 배치·선명도는 한계가 남아 있다. 부분 이미지 스트리밍을 편집 개입 지점으로 가정하지 않는다.
- [OpenAI 이미지 프롬프트 안내](https://developers.openai.com/api/docs/guides/image-prompting): 정확한 문구 지정과 작은 수정 단위가 유용하다. 특정 프롬프트가 모든 한글을 보장한다고 해석하지 않는다.
- [OpenAI 비전 한계](https://developers.openai.com/api/docs/guides/images-vision#limitations): 작고 비라틴계인 글자 판독을 자동 정답으로 취급하지 않는다.

스킬 개발 중 비교한 한 장의 반복 사례는 절차를 점검한 파일럿이다. 일반적인 한글 성공률이나 특정 모델의 우열을 입증한 벤치마크로 확대하지 않는다.
