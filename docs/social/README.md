# korean-sildetoimage 소개 자료

한 글자의 오타가 자료 전체의 신뢰를 흔들 수 있다는 문제에서 출발합니다. 잘 나온 슬라이드의 한글은 코드로 먼저 복구하고, 해결하기 어려울 때만 필요한 부분을 생성형으로 편집한다는 원칙을 소개합니다.

- [30초 가로 소개 영상 · MP4, 약 6MB](https://github.com/epoko77-ai/korean-sildetoimage/releases/download/intro-20261007/intro-landscape.mp4)
- [GitHub·페이스북 공유용 썸네일 · 1280×640 PNG](github-social-preview.png)
- [기존 SNS 썸네일 · PNG](thumbnail.png)
- [영상 렌더러·스토리보드·실제 전후 원본 · ZIP, 약 3MB](https://github.com/epoko77-ai/korean-sildetoimage/releases/download/intro-20261007/intro-source.zip)

![실제 획 복구 장면 미리보기](intro-preview.gif)

기본 소개 영상은 가로 1920×1080(16:9), 30fps, 30초입니다. 설명은 왼쪽, 실제 복구 장면은 오른쪽에 배치했습니다. **Claude Opus 5.5**가 구성과 Python 렌더러를 작성했고, Codex가 가로 화면에 맞춰 다시 조판하고 코드 복구 우선 원칙이 드러나도록 문구를 조정한 뒤 렌더링·시각 확인·파일 검사를 수행했습니다. 화면 문구만으로 내용을 따라갈 수 있고, 직접 합성한 배경음과 전환음을 넣었습니다. 음성 내레이션은 없습니다.

실제 생성 슬라이드의 ‘중가→증가’ 복구 자료를 사용했습니다. 원본 두 장의 차이는 9픽셀이며, 영상의 확대·강조 표시·압축은 설명을 위한 연출입니다. ‘수정 영역 밖 변경 0’과 ‘생성 호출 0’은 **해당 원본 복구 과정**의 기록입니다. 영상·썸네일 제작 전체에 대한 수치가 아닙니다.

GitHub·페이스북 공유용 썸네일은 실제 복구 전후의 글자를 확대하고 문구를 코드로 조판했습니다. 추가 이미지 생성 없이 1280×640 PNG로 제작하고 시각 검수했습니다. 별도로 남겨 둔 기존 SNS 썸네일은 내장 이미지 생성 도구로 만든 설명용 그림이며, 한글과 브랜드명을 시각·OCR로 검수한 자료입니다.

최종 영상은 전체 디코딩, 길이·해상도·프레임 수·오디오 확인과 대표 시점 7장의 시각 점검을 마쳤습니다. [검사 요약](media-checks.json)

큰 MP4와 재현용 원본은 릴리스에서 선택적으로 받을 수 있습니다. 저장소에는 가벼운 미리보기와 썸네일을 포함합니다.

GitHub 공유용 PNG는 1MB 미만으로 준비했습니다. README 이미지와 링크 공유 시 나타나는 Social preview는 별도 설정입니다. 저장소 파일을 올리는 것만으로 공유 미리보기가 바뀌지는 않습니다. [GitHub 공식 등록 안내](https://docs.github.com/ko/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview)에 따라 Settings → Social preview → Edit → Upload an image에서 선택합니다.
