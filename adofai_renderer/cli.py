"""Command-line interface for ADOFAI Offline PV Renderer."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .gpu_detect import detect_hardware, find_adofai_dir
from .level_info import parse_adofai_info
from .level_loader import resolve_level_input
from .render_runner import RenderProgress, RenderRunner, RenderSettings

RES_PRESETS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2160p": (3840, 2160),
    "4k": (3840, 2160),
}


def parse_resolution(value: str) -> tuple[int, int]:
    low = value.strip().lower()
    if low in RES_PRESETS:
        return RES_PRESETS[low]
    if "x" in low:
        parts = low.split("x", 1)
        return int(parts[0]), int(parts[1])
    raise ValueError(f"지원하지 않는 해상도 형식입니다: {value} (예: 1080p, 1440p, 2160p, 1920x1080)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adofai_renderer",
        description="ADOFAI 결정론적 오프라인 PV 렌더러 (GUI / CLI)",
    )
    p.add_argument("level", nargs="?", help="렌더링할 .adofai 파일, 레벨 폴더, 또는 .zip 압축 파일 경로")
    p.add_argument("-o", "--output", help="출력 영상 파일 경로 (.mp4 또는 .mkv)")
    p.add_argument("--res", default="1080p", help="해상도 (720p, 1080p, 1440p, 2160p, 4k 또는 WxH, 기본값: 1080p)")
    p.add_argument("--fps", type=int, default=60, help="프레임레이트 (24~240, 기본값: 60)")
    p.add_argument("--format", choices=["mp4", "mkv"], default=None, help="출력 컨테이너 (미지정 시 출력 경로 확장자 또는 mp4)")
    p.add_argument("--gpu", choices=["auto", "nvenc", "igpu", "cpu"], default="auto", help="GPU/인코더 선택 (기본값: auto)")
    p.add_argument("--gpu-limit", type=int, default=90, help="최대 GPU 출력 상한 %% (10~100, 기본값: 90)")
    p.add_argument("--ram-limit", type=int, default=80, help="최대 RAM 사용 상한 %% (30~95, 기본값: 80)")
    p.add_argument("--cpu-cores", type=int, default=0, help="최대 점유 CPU 코어/스레드 수 (0=자동, 기본값: 0)")
    p.add_argument("--low-spec", choices=["auto", "force", "off"], default="auto", help="저사양 안전 모드 (기본값: auto)")
    p.add_argument("--substeps", type=int, default=0, help="프레임당 시뮬레이션 서브스텝 수 (0=자동(레벨 최대 BPM 기준), 1~8). 높을수록 행성 궤도·궤적이 부드러움")
    p.add_argument("--optimize-render", action="store_true", help="최적화 렌더(실험): 불투명도 0%%·화면 밖 장식 컬링 및 고해상도 장식 LOD 프록시 자동 교체")
    p.add_argument("--codec", choices=["h264", "hevc"], default="h264", help="비디오 코덱 (기본값: h264)")
    p.add_argument("--quality", choices=["ultra", "high", "balanced"], default="high", help="화질 프리셋 (기본값: high)")
    p.add_argument("--no-bgm", action="store_true", help="배경음악 제외")
    p.add_argument("--bgm-vol", type=float, default=1.0, help="배경음악 볼륨 배율 (0.0~2.0, 기본값: 1.0)")
    p.add_argument("--no-hitsounds", action="store_true", help="인게임 타격음 제외")
    p.add_argument("--hitsound-vol", type=float, default=1.0, help="타격음 볼륨 배율 (0.0~2.0, 기본값: 1.0)")
    p.add_argument("--red-color", default="#FF0000", help="불(빨강) 행성 색상 HEX (기본값: #FF0000)")
    p.add_argument("--red-opacity", type=float, default=1.0, help="불(빨강) 행성 불투명도 0.0~1.0 (기본값: 1.0)")
    p.add_argument("--blue-color", default="#0000FF", help="얼음(파랑) 행성 색상 HEX (기본값: #0000FF)")
    p.add_argument("--blue-opacity", type=float, default=1.0, help="얼음(파랑) 행성 불투명도 0.0~1.0 (기본값: 1.0)")
    p.add_argument("--show-countdown", action="store_true", help="시작 카운트다운 표시 (기본: 숨김)")
    p.add_argument("--start", type=float, default=0.0, help="시작 시점(초, 기본값: 0)")
    p.add_argument("--end", type=float, default=0.0, help="종료 시점(초, 0=레벨 끝까지)")
    p.add_argument("--end-pad", type=float, default=2.0, help="클리어 후 여백 시간(초, 기본값: 2.0)")
    p.add_argument("--adofai-dir", default="", help="Steam ADOFAI 설치 폴더 경로 (미지정 시 자동 감지)")
    p.add_argument("--keep-temp", action="store_true", help="중간 임시 파일 유지")
    p.add_argument("--info", action="store_true", help="감지된 GPU/인코더/ADOFAI 환경 정보 출력")
    p.add_argument("--cli", action="store_true", help="GUI 대신 CLI 모드로 강제 실행")
    return p


def run_cli(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    caps = detect_hardware(verify_hw=True)
    adofai_dir = args.adofai_dir or find_adofai_dir() or ""

    if args.info or not args.level:
        print("=== ADOFAI Offline PV Renderer 환경 정보 ===")
        print(f"Steam ADOFAI 경로 : {adofai_dir or '(미감지)'}")
        print(f"FFmpeg 경로       : {caps.ffmpeg_path}")
        print("감지된 GPU        :")
        for g in caps.gpus:
            kind = "외장 GPU" if g.is_discrete else "내장 GPU"
            vram = f", {g.vram_mb}MB" if g.vram_mb else ""
            print(f"  - {g.name} [{g.vendor.upper()} / {kind}{vram}]")
        print(f"검증된 인코더     : {', '.join(caps.verified_encoders)}")
        print(f"자동 권장 백엔드  : {caps.recommended_label}")
        print(f"저사양 안전 권장  : {'예' if caps.low_spec_recommended else '아니오'}")
        if args.level:
            try:
                resolved = resolve_level_input(args.level)
                info = parse_adofai_info(resolved)
                print(f"\n레벨 정보         : {info.display_title}")
                print(f"  BPM={info.bpm}, 타일={info.tile_count}, 이벤트={info.action_count}, 장식={info.decoration_count}, 필터={len(info.filters)}종")
            except Exception as e:
                print(f"\n[경고] 레벨 분석 실패: {e}")
        return 0

    try:
        width, height = parse_resolution(args.res)
        level_path = resolve_level_input(args.level)
    except Exception as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2

    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        ext = args.format or "mp4"
        base_dir = os.path.basename(os.path.dirname(level_path))
        stem = os.path.splitext(os.path.basename(level_path))[0]
        out_name = f"{base_dir}_{stem}_{height}p_{args.fps}fps.{ext}"
        output_path = os.path.join(os.path.dirname(level_path), out_name)

    if args.format and not output_path.lower().endswith("." + args.format):
        output_path = os.path.splitext(output_path)[0] + "." + args.format

    settings = RenderSettings(
        level_path=level_path,
        output_path=output_path,
        adofai_dir=adofai_dir,
        width=width,
        height=height,
        fps=args.fps,
        gpu_mode=args.gpu,
        gpu_limit_percent=args.gpu_limit,
        ram_limit_percent=args.ram_limit,
        max_cpu_cores=args.cpu_cores,
        codec=args.codec,
        quality=args.quality,
        include_bgm=not args.no_bgm,
        bgm_volume=args.bgm_vol,
        include_hitsounds=not args.no_hitsounds,
        hitsound_volume=args.hitsound_vol,
        hide_countdown=not args.show_countdown,
        hide_hit_text=True,
        start_sec=args.start,
        end_sec=args.end,
        end_padding_sec=args.end_pad,
        keep_temp=args.keep_temp,
        red_planet_color=args.red_color,
        red_planet_opacity=args.red_opacity,
        blue_planet_color=args.blue_color,
        blue_planet_opacity=args.blue_opacity,
        low_spec_mode=args.low_spec,
        experimental_optimize_render=args.optimize_render,
        substeps=args.substeps,
    )

    runner = RenderRunner(caps=caps)
    last_line_len = [0]

    def on_log(msg: str) -> None:
        if last_line_len[0] > 0:
            print()
            last_line_len[0] = 0
        print(f"[INFO] {msg}", flush=True)

    def on_progress(p: RenderProgress) -> None:
        bar_w = 24
        filled = int(round((p.percent / 100.0) * bar_w))
        bar = "#" * filled + "-" * (bar_w - filled)
        eta_str = f"ETA {p.eta_sec:.0f}s" if p.eta_sec > 0.5 else ""
        line = f"\r[{bar}] {p.percent:5.1f}% | {p.message} {eta_str}".ljust(last_line_len[0])
        last_line_len[0] = len(line)
        print(line, end="", flush=True)

    try:
        res = runner.run(settings, progress_cb=on_progress, log_cb=on_log)
    except KeyboardInterrupt:
        print("\n[중단] 취소 요청을 보냈습니다...")
        runner.cancel()
        return 130

    if last_line_len[0] > 0:
        print()

    if res.success:
        print(f"\n[성공] 출력 파일: {res.output_path}")
        print(f"       프레임: {res.frames} ({res.video_sec:.2f}초), 인코더: {res.encoder_used}, 소요 시간: {res.wall_sec:.1f}초")
        if res.fallback_note:
            print(f"       안전 우회: {res.fallback_note}")
        return 0
    else:
        print(f"\n[실패] {res.error}", file=sys.stderr)
        return 1
