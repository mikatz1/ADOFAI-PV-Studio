# ADOFAI PV Studio

A Dance of Fire and Ice(ADOFAI) 커스텀 레벨을 고정 프레임(CFR) MP4/MKV 영상으로 출력하는 오프라인 PV 렌더러입니다.

- 다운로드: [Releases](https://github.com/mikatz1/ADOFAI-PV-Studio/releases/latest)
- 요구 사항: Windows 10/11 (64-bit), Steam 정품 *A Dance of Fire and Ice*

## 주요 기능

- **인게임 엔진 기반 오프라인 렌더링**: Steam ADOFAI의 셰이더, 필터, 장식, 카메라, 트랙 효과를 그대로 실행하며 가상 클럭으로 프레임 드랍 없이 출력합니다. 렌더링 시 Doorstop으로 기존 모드를 격리하고 게임 폴더 파일은 건드리지 않습니다.
- **오프라인 오디오 합성**: 원곡과 인게임 히트사운드 WAV를 렌더링 타임라인에 맞춰 합성합니다.
- **프록시 미리보기 및 구간 트림**: `240p`~`720p` / `5`~`60fps` 프록시 생성(Chart만 렌더링 · 고밀도 그래픽 필터만 제외 · 모든 요소 렌더링 3모드), 필름스트립·오디오 파형 타임라인, 시작/종료 구간 트림을 지원합니다.
- **로딩 재사용 & 체크포인트 시작**: 프록시에서 불러온 레벨·장식을 본 렌더에 재사용하고, 트림 시 체크포인트 지점부터 시뮬레이션합니다.
- **인코더 및 리소스 설정**: NVENC / QSV / AMF / D3D12VA / CPU(`libx264`, `libx265`) 자동 선택 및 GPU·RAM·CPU 상한 조절, 다크/라이트 테마, 한국어·영어·일본어를 지원합니다.
- **최적화 렌더(실험)**: 불투명도 0% 장식 비활성화, 화면 밖 장식 컬링, 고해상도 장식 LOD 축소를 선택적으로 켤 수 있습니다.

## 설치 및 실행

### 설치 파일 / 실행 파일
1. [Releases](https://github.com/mikatz1/ADOFAI-PV-Studio/releases/latest)에서 `ADOFAI_PV_Studio_Setup_1.0.0.exe`를 받아 설치합니다.
2. `.adofai` 파일, 레벨 폴더, 또는 `.zip` 파일을 프로그램 창이나 아이콘에 드래그해서 열 수 있습니다.

### 소스 실행
```powershell
python render.py
```

### CLI
```powershell
# 환경 확인
python render.py --info

# 1080p 60fps 렌더링
python render.py "path\to\main.adofai" -o "output\out.mp4" --res 1080p --fps 60

# 구간 트림 + 최적화 렌더
python render.py "path\to\main.adofai" -o "output\out.mp4" --res 3840x2160 --fps 60 --start 180 --end 240 --optimize-render

# 리소스 상한 / 행성 색상 지정
python render.py "path\to\main.adofai" -o "output\out.mp4" --gpu-limit 60 --ram-limit 75 --cpu-cores 8 --red-color "#FF3366" --blue-color "#00CCFF"
```

## 빌드

```powershell
# C# 렌더 에이전트 빌드
powershell -ExecutionPolicy Bypass -File agent\build.ps1

# 단일 실행 파일 빌드 (dist\ADOFAI PV Studio.exe)
powershell -ExecutionPolicy Bypass -File build_exe.ps1

# Inno Setup 6 설치 마법사 빌드 (dist\installer\ADOFAI_PV_Studio_Setup_1.0.0.exe)
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
```

## 구조

- `agent/src/` — C# Unity 오프라인 렌더 에이전트 (`AdofaiRenderAgent.dll`)
- `adofai_renderer/` — 렌더 파이프라인, 오디오 믹서, 하드웨어 감지, CLI, 자동 업데이터
- `adofai_renderer/ui/` — PySide6 GUI
- `installer/` — Inno Setup 6 설치 스크립트
