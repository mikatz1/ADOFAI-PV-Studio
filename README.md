# ADOFAI Offline PV Renderer

ADOFAI(A Dance of Fire and Ice) 커스텀 레벨을 **컴퓨터 사양과 무관하게 프레임 드랍·그래픽 틀어짐 없이** 실제 인게임과 100% 동일하게 MP4/MKV 영상으로 추출하는 결정론적 오프라인 PV 렌더러입니다.

---

## 주요 특징

- **인게임 100% 시각 일치**: Steam에 설치된 실제 ADOFAI Unity 엔진의 필터(`CameraFilterPack` 230+종)·블룸·`HallOfMirrors`·카메라·장식·`AddText`·트랙 변형을 그대로 실행하며, 셰이더나 에셋을 임의로 재작성하지 않습니다.
- **기존 모드 격리(순정 렌더링)**: Unity Doorstop 커맨드라인 오버라이드(`--doorstop-target-assembly`)를 사용해 렌더링 시 `UnityModManager` 및 타 모드의 비주얼 간섭을 차단하며, Steam 폴더 내부 파일은 수정하지 않습니다.
- **결정론적 가상 클럭 + 오프라인 오디오 믹싱**: `Time.captureDeltaTime`과 가상 `scrConductor.dspTime`으로 1프레임씩 고정 간격(CFR) 렌더링하므로 저사양 PC나 초대형 맵에서도 프레임 드랍·싱크 어긋남이 발생하지 않습니다. 히트사운드도 Unity 메모리에서 추출한 실제 인게임 WAV를 원곡과 오프라인 합성합니다.
- **최적화 렌더 (실험)**: 작업물 퀄리티를 손상시키지 않으면서 초대형 레벨의 렌더링 병목을 제거합니다.
  - 프레임별 불투명도 `0%` 장식 영구 비활성화 동기화 (수만 개 장식의 불필요한 씬 트리 갱신 차단)
  - 왜곡·필터 안전 마진이 적용된 카메라 프러스텀(화면 밖) 장식 컬링
  - `512px` 이상 고해상도 장식용 다중 해상도 LOD 프록시 피라미드 사전 생성 및 화면 투영 픽셀 크기 기반 자동 교체
- **저사양 PC 안전 폴백**: VRAM/GPU/인코더 한계로 실패하지 않도록 `SYNC_SAFE_READPIXELS`, 16비트 Depth RT 재시도, 해상도 검증 인코더 폴백 체인(`NVENC → QSV / AMF / D3D12VA → libx264/libx265`)을 자동 적용하며 목표 해상도·CFR FPS·`VisualQuality.High`를 유지합니다.
- **데스크톱 스튜디오 GUI**:
  - 시작 웰컴 카드(GPU·FFmpeg·에이전트 DLL·Unity 핵심 바이너리 OS 페이지 캐시 프리로딩)
  - 커스텀 프록시(`144p`~`720p`, `5`~`60fps`) + 16타일 필름스트립 + 오디오 파형 + 구간 트림 타임라인 (기본값: 전체 렌더링)
  - 듀얼 UI 테마(**스튜디오 다크** / 파스텔 핑크·블루 포인트 **파스텔 라이트**) 및 3개 국어(**한국어 · English · 日本語**) 실시간 전환
  - 비디오 비트레이트 3단계(`높음 / 중간 / 낮음`) 선택 및 예상 용량 실시간 계산
  - 불/얼음 행성 색상·불투명도 커스터마이징 (기본값: 순수 빨강 `#FF0000` 100% / 순수 파랑 `#0000FF` 100%)
  - 렌더링 시작 전 **최종 설정 확인 창 & 예상 소요 시간 / 완료 시각(ETA)** 표시
  - 다크 콘솔 스타일 실시간 렌더링 진행 카드 (`esc to cancel`)
  - 인앱 기본 리소스 상한: **GPU 90% / RAM 80% / CPU 자동(0)** (설정에서 GPU 10~100%, RAM 30~95%, 최대 CPU 코어 자유 조절 가능)

---

## 검증 결과 (체크리스트 & 궁극 테스트)

> 로컬 테스트 환경: NVIDIA GeForce RTX 5060 Laptop GPU (8GB VRAM), 로컬 GPU 상한 **30%** 적용 측정치

1. **체크리스트 1 — 저부하 파트 고속 렌더링** (`light_zero_deco.adofai`, 장식 0개):
   - 1080p 60fps: **145~147 fps** (GPU 30% 상한 상태에서도 실시간 대비 **2.45×** 고속)
   - 720p 30fps: **220.8 fps** (GPU 30% 상한 상태에서도 실시간 대비 **7.36×** 고속, 기본 90% 상한 기준 수배~십수배)
2. **체크리스트 2 — 초고부하 파트 시각 무결성** (`Arche FULL`, 타일 4,138 · 장식 28,835 · 이벤트 137,452 · 8K 텍스처 10장, 4:20~4:26, 1080p 60fps):
   - 최적화 OFF 기준선 대비 프레임 픽셀 상관계수 **`0.9989 ~ 0.9995`** (MAE `1.28 ~ 2.60`)로 100% 의도대로 렌더링 확인.
3. **궁극 테스트 — `Arche FULL` 3:30~5:00 구간 4K 60fps CFR (최적화 렌더 ON, GPU 30% 상한)**:
   - 출력 파일: `output/Arche_3m30_5m00_4K60_opt.mp4` (`856.0 MB`)
   - 스트림 검증: **`3840×2160` · 정확히 `60.000 fps CFR` · `5,400 프레임` (90.00초)** · AAC 48kHz 스테레오 (`Arche.ogg` + 인게임 히트사운드 1,785개 정밀 믹싱)
   - 최적화 렌더 텔레메트리: 28,828개 장식 `164ms` 인덱싱 · LOD 프록시 피라미드 `38개(384MB VRAM)` · 프레임당 평균 `28,223.8개` 불투명도 0% 장식 컬링 · `40.7개/pass` 화면 밖 컬링
   - 성능 및 안정성: **총 356.0초(5분 56초)** 완료 (`26.1 fps` 4K 캡처 유지, NVML 평균 GPU 3D `19.8% / 상한 30%`, `syncFallback=False`, 에러 0건)

---

## 실행 방법

### 1. 단일 실행 파일(EXE) 실행
빌드된 단일 실행 파일 `dist\ADOFAI PV Renderer.exe` 또는 바탕화면 바로가기(`ADOFAI PV Renderer`)를 더블클릭하여 실행합니다.
- `.adofai` 파일, 레벨 폴더, 또는 `.zip` 압축 파일을 EXE 아이콘 위에 바로 드래그하여 열 수도 있습니다.

### 2. Python 소스에서 GUI 실행
```powershell
python render.py
```
또는:
```powershell
python -m adofai_renderer
```

### 3. CLI 실행
```powershell
# 감지된 GPU / 인코더 / Steam ADOFAI 경로 확인
python render.py --info

# 1080p 60fps MP4 렌더링 (기본: GPU 90% / RAM 80% / CPU 자동, 원곡 + 히트사운드 포함)
python render.py "level\CDF_Semi-Final_Crystallized_Etell\CDF2.adofai" -o "output\crystallized_1080p60.mp4" --res 1080p --fps 60

# 4K(2160p) 60fps + 최적화 렌더(실험) ON + 구간 트림(3:30~5:00)
python render.py "level\Arche\[𝐃𝐎 𝐍𝐎𝐓 𝐎𝐏𝐄𝐍 𝐓𝐇𝐈𝐒 𝐀𝐓 𝐇𝐎𝐌𝐄] FULL.adofai" -o "output\Arche_4K60_opt.mp4" --res 3840x2160 --fps 60 --start 210 --end 300 --optimize-render

# GPU 상한 / RAM 상한 / CPU 코어 / 행성 색상 지정 예시
python render.py "level\...\CDF2.adofai" -o "output\custom.mp4" --gpu-limit 60 --ram-limit 75 --cpu-cores 8 --red-color "#FF3366" --blue-color "#00CCFF"
```

### 4. 단일 EXE 패키징 & Inno Setup 설치 마법사 빌드
```powershell
# 단일 실행 파일 + 바탕화면 바로가기 빌드
powershell -ExecutionPolicy Bypass -File build_exe.ps1

# Inno Setup 6 설치 마법사 빌드 (dist\installer\ADOFAI_PV_Renderer_Setup_2.0.5.exe)
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
```
- `dist\ADOFAI PV Renderer.exe` 단일 실행 파일(아이콘 `assets\app_icon.ico` 및 C# 에이전트 DLL 내장)과 한국어/영어/일본어 Inno Setup 6 설치 마법사(`dist\installer\ADOFAI_PV_Renderer_Setup_2.0.5.exe`)를 생성합니다.

### 업데이트 (자동 업데이트 확인)

앱은 시작할 때(그리고 `도움말 → 업데이트 확인...`에서) GitHub Releases의 최신 릴리스를 확인합니다. 새 버전이 있으면 설치 프로그램을 내려받아(SHA-256 검증) 실행하고, 업데이트 후 앱이 다시 시작됩니다.

릴리스 배포 방법 (관리자):

1. `adofai_renderer/version.py`의 `__version__`과 `installer/setup.iss`의 `MyAppVersion`을 새 버전으로 올립니다.
2. `powershell -ExecutionPolicy Bypass -File installer\\build_installer.ps1 -RebuildExe`
3. 태그 `vX.Y.Z`로 GitHub 릴리스를 만들고 `dist\installer\ADOFAI_PV_Renderer_Setup_X.Y.Z.exe`를 첨부합니다 (파일명에 `Setup`이 포함되어야 합니다).

### 5. C# 렌더 에이전트 재빌드 (ADOFAI 대형 업데이트 시)
```powershell
powershell -ExecutionPolicy Bypass -File agent\build.ps1
```

---

## 프로젝트 구조
- `agent/src/` — C# Unity 오프라인 렌더 에이전트 (`ExperimentalRenderOptimizer`, `GpuPacer`, `FrameCapturePipe`, `UiAndQualityPatches` → `agent/build/AdofaiRenderAgent.dll`)
- `adofai_renderer/paths.py` — 소스 실행 및 PyInstaller `--onefile` 통합 경로 리졸버
- `adofai_renderer/gpu_detect.py` — NVIDIA NVENC · 내장 GPU(QSV/AMF/D3D12VA) · CPU 폴백 · Steam ADOFAI 자동 감지
- `adofai_renderer/preloader.py` — 웰컴 카드 엔진/바이너리 프리로더
- `adofai_renderer/audio_mixer.py` — 원곡 + 추출된 실제 히트사운드 WAV 오프라인 정밀 믹서
- `adofai_renderer/render_runner.py` — 오프라인 렌더링·러프 프리뷰·안전 폴백·FFmpeg 합성 파이프라인
- `adofai_renderer/ui/` — PySide6 Studio GUI (`welcome_card.py`, `main_window.py`, `timeline_widget.py`, `dialogs.py`, `render_card.py`, `theme.py`)
- `adofai_renderer/cli.py` — CLI 인터페이스
- `assets/` — 심볼 앱 아이콘(`app_icon.ico`, `app_icon.png`) 및 UI 에셋
- `build_exe.ps1` — 단일 EXE 패키징 및 바탕화면 바로가기 빌드 스크립트
- `verify_compare.py` — 레퍼런스 MP4 대비 오디오 정렬 및 프레임 비교 도구
