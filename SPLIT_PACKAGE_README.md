[원본 v20 인계 자료 — 현재 v21 실행은 README_START_HERE.md 참조]

# Codex 파일 첨부 가이드 (각 파일 32 MiB 미만)

1. `Polar_Codex_01_CODE.zip`과 `Polar_Codex_02_REFERENCE_IMAGES.zip`, `Polar_Codex_03_VIDEO_PART01.zip`, `Polar_Codex_04_VIDEO_PART02.zip` 총 **4개 파일 모두** Codex에 업로드한다.
2. Codex가 로컬 프로젝트를 편집할 수 있다면 4개 ZIP을 **같은 상위 폴더에 모두 압축 해제**한다. 모든 ZIP의 루트는 `Polar_Codex_Handoff_v20/` 이며 동일 폴더에 합쳐진다.
3. `Polar_Codex_Handoff_v20/REASSEMBLE_VIDEO.py`를 `python REASSEMBLE_VIDEO.py`로 실행하면 2개 조각이 원본 MP4로 복원되고 SHA256이 검증된다.
4. `Polar_Codex_Handoff_v20/CODEX_TASK.md`를 읽고 `polar_v18.py`, `vision_engine.py`, `GAME_INPUT_AHKv1.ahk`를 직접 수정한다.
5. 업로드한 ZIP 첨부만으로 자동으로 로컬 파일 시스템에 합쳐지는 것은 아니다. Codex가 파일을 읽고 합칠 수 있는 작업 환경을 확인한다.
6. 원본 전체 ZIP(약 74MB)은 다시 업로드할 필요가 없다.

**Codex에 보낼 문장**: "4개 ZIP을 동일한 `Polar_Codex_Handoff_v20` 폴더로 풀고 `REASSEMBLE_VIDEO.py`를 실행해 영상 SHA256을 확인하세요. 그 다음 `CODEX_TASK.md`의 요구사항에 따라 최신 v20 소스를 직접 수정하고 CHANGELOG.md 및 TEST_RESULTS.md를 작성하세요. 기존 정상 동작한 AHK 브리지는 유지하세요."
