"""Tri-lingual (Korean / English / Japanese) localization with Inno Setup registry sync."""

from __future__ import annotations

import json
import locale
import os
from typing import Dict

from .paths import get_data_root

SUPPORTED_LANGUAGES = ("ko", "en", "ja")
LANGUAGE_NAMES = {
    "ko": "한국어",
    "en": "English",
    "ja": "日本語",
}

_REG_SUBKEY = r"Software\ADOFAI PV Renderer"
_REG_VALUE = "Language"
_current_lang: str | None = None


def _normalize_lang(code: str) -> str:
    s = (code or "").strip().lower()
    if s.startswith("ko"):
        return "ko"
    if s.startswith("ja") or s.startswith("jp"):
        return "ja"
    if s.startswith("en"):
        return "en"
    return ""


def _read_registry_lang() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_SUBKEY) as k:
            val, _ = winreg.QueryValueEx(k, _REG_VALUE)
        return _normalize_lang(str(val or ""))
    except Exception:
        return ""


def _write_registry_lang(lang: str) -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_SUBKEY) as k:
            winreg.SetValueEx(k, _REG_VALUE, 0, winreg.REG_SZ, lang)
    except Exception:
        pass


def _fallback_file() -> str:
    return os.path.join(get_data_root(), "cache", "language.json")


def _read_file_lang() -> str:
    try:
        p = _fallback_file()
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _normalize_lang(str(data.get("language", "")))
    except Exception:
        pass
    return ""


def _write_file_lang(lang: str) -> None:
    try:
        p = _fallback_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"language": lang}, f)
    except Exception:
        pass


def _detect_system_lang() -> str:
    if os.name == "nt":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lcid & 0x3FF
            if primary == 0x12:  # LANG_KOREAN
                return "ko"
            if primary == 0x11:  # LANG_JAPANESE
                return "ja"
            if primary == 0x09:  # LANG_ENGLISH
                return "en"
        except Exception:
            pass
    try:
        loc = locale.getlocale()[0] or ""
        n = _normalize_lang(loc)
        if n:
            return n
    except Exception:
        pass
    return "ko"


def get_language() -> str:
    global _current_lang
    if _current_lang in SUPPORTED_LANGUAGES:
        return _current_lang
    lang = _read_registry_lang() or _read_file_lang() or _detect_system_lang()
    if lang not in SUPPORTED_LANGUAGES:
        lang = "ko"
    _current_lang = lang
    return _current_lang


def set_language(lang: str, persist: bool = True) -> str:
    global _current_lang
    norm = _normalize_lang(lang) or "ko"
    _current_lang = norm
    if persist:
        _write_registry_lang(norm)
        _write_file_lang(norm)
    return _current_lang


def format_duration(sec: float, lang: str | None = None) -> str:
    sec = max(0.0, float(sec or 0.0))
    lg = lang or get_language()
    if sec < 60:
        s = int(round(sec))
        if lg == "en":
            return f"~{s}s"
        if lg == "ja":
            return f"約{s}秒"
        return f"약 {s}초"
    mins = int(sec // 60)
    rem = int(round(sec % 60))
    if mins < 60:
        if lg == "en":
            return f"~{mins}m {rem:02d}s"
        if lg == "ja":
            return f"約{mins}分{rem:02d}秒"
        return f"약 {mins}분 {rem:02d}초"
    hrs = mins // 60
    mins = mins % 60
    if lg == "en":
        return f"~{hrs}h {mins:02d}m"
    if lg == "ja":
        return f"約{hrs}時間{mins:02d}分"
    return f"약 {hrs}시간 {mins:02d}분"


_STRINGS: Dict[str, Dict[str, str]] = {
    # ---------------- Welcome Card & Preloader ----------------
    "welcome.sub_init": {
        "ko": "엔진 및 하드웨어 환경 확인 중...",
        "en": "Checking engine and hardware environment...",
        "ja": "エンジンおよびハードウェア環境を確認中...",
    },
    "welcome.detail_init": {
        "ko": "초기화 시작 중...",
        "en": "Starting initialization...",
        "ja": "初期化を開始しています...",
    },
    "welcome.ready": {
        "ko": "모든 시스템 준비 완료",
        "en": "All systems ready",
        "ja": "すべてのシステムの準備が完了しました",
    },
    "welcome.step1": {
        "ko": "엔진 경로 및 Doorstop 확인",
        "en": "Check engine path & Doorstop",
        "ja": "エンジンパスとDoorstopの確認",
    },
    "welcome.step2": {
        "ko": "GPU · FFmpeg 하드웨어 인코더 검증",
        "en": "Verify GPU & FFmpeg hardware encoders",
        "ja": "GPU・FFmpegハードウェアエンコーダー検証",
    },
    "welcome.step3": {
        "ko": "C# 오프라인 렌더 에이전트 점검",
        "en": "Inspect C# offline render agent",
        "ja": "C#オフラインレンダーエージェント点検",
    },
    "welcome.step4": {
        "ko": "Unity 엔진 바이너리 메모리 프리워밍",
        "en": "Pre-warm Unity engine binaries in memory",
        "ja": "Unityエンジンバイナリのプリウォーム",
    },
    "welcome.step5": {
        "ko": "워크스페이스 및 샘플 메타데이터 분석",
        "en": "Analyze workspace & sample metadata",
        "ja": "ワークスペース・サンプルメタデータ解析",
    },
    "preload.s1_title": {
        "ko": "엔진 경로 확인",
        "en": "Engine path check",
        "ja": "エンジンパス確認",
    },
    "preload.s1_detail": {
        "ko": "Steam ADOFAI 및 Unity Doorstop 구성 확인 중...",
        "en": "Checking Steam ADOFAI and Unity Doorstop setup...",
        "ja": "Steam ADOFAIおよびUnity Doorstop構成を確認中...",
    },
    "preload.s2_title": {
        "ko": "GPU · 인코더 프로브",
        "en": "GPU & encoder probe",
        "ja": "GPU・エンコーダー検査",
    },
    "preload.s2_detail": {
        "ko": "외장/내장 GPU 및 FFmpeg 하드웨어 인코더 실동작 검증 중...",
        "en": "Probing discrete/integrated GPUs and FFmpeg hardware encoders...",
        "ja": "GPUおよびFFmpegハードウェアエンコーダーの動作を検証中...",
    },
    "preload.s3_title": {
        "ko": "오프라인 렌더 에이전트",
        "en": "Offline render agent",
        "ja": "オフラインレンダーエージェント",
    },
    "preload.s3_detail": {
        "ko": "AdofaiRenderAgent.dll 무결성 및 빌드 상태 확인 중...",
        "en": "Checking AdofaiRenderAgent.dll integrity and build status...",
        "ja": "AdofaiRenderAgent.dllの整合性とビルド状態を確認中...",
    },
    "preload.s4_title": {
        "ko": "Unity 엔진 프리워밍",
        "en": "Unity engine pre-warming",
        "ja": "Unityエンジン プリウォーム",
    },
    "preload.s4_detail": {
        "ko": "핵심 UnityPlayer · Assembly-CSharp 바이너리 메모리 캐싱 중...",
        "en": "Pre-caching core UnityPlayer & Assembly-CSharp binaries...",
        "ja": "UnityPlayer・Assembly-CSharpバイナリをキャッシュ中...",
    },
    "preload.s5_title": {
        "ko": "워크스페이스 초기화",
        "en": "Workspace initialization",
        "ja": "ワークスペース初期化",
    },
    "preload.s5_detail": {
        "ko": "로컬 샘플 레벨 메타데이터 및 타임라인 길이 분석 중...",
        "en": "Scanning local sample levels and timeline durations...",
        "ja": "サンプルレベルのメタデータと長さを解析中...",
    },
    "preload.done_title": {
        "ko": "준비 완료",
        "en": "Ready",
        "ja": "準備完了",
    },
    "preload.done_detail": {
        "ko": "모든 시스템 준비 완료 ({elapsed:.1f}s · 캐시 {mb:.0f}MB)",
        "en": "All systems ready ({elapsed:.1f}s · warmed {mb:.0f}MB)",
        "ja": "準備完了 ({elapsed:.1f}s · キャッシュ {mb:.0f}MB)",
    },

    # ---------------- Top Menu Bar ----------------
    "menu.file": {"ko": "파일(&F)", "en": "&File", "ja": "ファイル(&F)"},
    "menu.open_level": {
        "ko": "레벨 열기...",
        "en": "Open Level...",
        "ja": "レベルを開く...",
    },
    "menu.samples": {"ko": "샘플 레벨", "en": "Sample Levels", "ja": "サンプルレベル"},
    "menu.samples_empty": {"ko": "(샘플 없음)", "en": "(No samples)", "ja": "(サンプルなし)"},
    "menu.steam_dir": {
        "ko": "Steam ADOFAI 경로 설정...",
        "en": "Set Steam ADOFAI Folder...",
        "ja": "Steam ADOFAIフォルダ設定...",
    },
    "menu.quit": {"ko": "종료", "en": "Exit", "ja": "終了"},

    "menu.edit": {"ko": "편집(&E)", "en": "&Edit", "ja": "編集(&E)"},
    "menu.mark_in": {"ko": "트림 시작점 지정 (I)", "en": "Mark Trim In (I)", "ja": "トリム開始点を設定 (I)"},
    "menu.mark_out": {"ko": "트림 종료점 지정 (O)", "en": "Mark Trim Out (O)", "ja": "トリム終了点を設定 (O)"},
    "menu.reset_trim": {"ko": "구간 전체로 초기화", "en": "Reset Trim to Full", "ja": "範囲を全体にリセット"},
    "menu.planet_cfg": {
        "ko": "행성 색상 및 불투명도 설정...",
        "en": "Planet Color & Opacity...",
        "ja": "惑星の色と不透明度...",
    },
    "menu.planet_reset": {
        "ko": "행성 기본값 복원 (빨강/파랑 100%)",
        "en": "Reset Planets (Red/Blue 100%)",
        "ja": "惑星を初期値に戻す (赤/青 100%)",
    },
    "menu.export_cfg": {
        "ko": "환경설정 및 리소스 상한...",
        "en": "Preferences & Resource Limits...",
        "ja": "詳細設定とリソース上限...",
    },
    "menu.language": {
        "ko": "언어 (Language)",
        "en": "Language",
        "ja": "言語 (Language)",
    },

    "menu.seq": {"ko": "시퀀스(&S)", "en": "&Sequence", "ja": "シーケンス(&S)"},
    "menu.prev_build": {
        "ko": "프록시 생성",
        "en": "Generate Proxy",
        "ja": "プロキシを生成",
    },
    "menu.prev_rebuild": {
        "ko": "프록시 다시 생성",
        "en": "Rebuild Proxy",
        "ja": "プロキシを再生成",
    },
    "menu.proxy_cfg": {
        "ko": "프록시 설정...",
        "en": "Proxy Settings...",
        "ja": "プロキシ設定...",
    },
    "menu.open_seq_tab": {
        "ko": "행성 · 시퀀스 탭 열기",
        "en": "Open Planets & Sequence Tab",
        "ja": "惑星・シーケンスタブを開く",
    },
    "menu.clear_cache": {
        "ko": "프록시 캐시 비우기",
        "en": "Clear Proxy Cache",
        "ja": "プロキシキャッシュを消去",
    },

    "menu.view": {"ko": "보기(&V)", "en": "&View", "ja": "表示(&V)"},
    "menu.toggle_inspector": {"ko": "출력 설정 패널", "en": "Output Settings Panel", "ja": "出力設定パネル"},
    "menu.theme": {"ko": "테마 (Theme)", "en": "Theme", "ja": "テーマ (Theme)"},
    "menu.theme_dark": {"ko": "스튜디오 다크 (Dark)", "en": "Studio Dark", "ja": "スタジオダーク (Dark)"},
    "menu.theme_light": {"ko": "파스텔 라이트 (Light)", "en": "Pastel Light", "ja": "パステルライト (Light)"},
    "menu.zoom_in": {"ko": "타임라인 확대", "en": "Zoom In Timeline", "ja": "タイムラインを拡大"},
    "menu.zoom_out": {"ko": "타임라인 축소", "en": "Zoom Out Timeline", "ja": "タイムラインを縮小"},
    "menu.zoom_fit": {"ko": "타임라인 전체 맞추기", "en": "Fit Timeline to View", "ja": "タイムラインを全体表示"},
    "pane.close_tip": {"ko": "패널 닫기 (Tab)", "en": "Close Panel (Tab)", "ja": "パネルを閉じる (Tab)"},

    "menu.render": {"ko": "렌더(&R)", "en": "&Render", "ja": "レンダー(&R)"},
    "menu.render_start": {
        "ko": "렌더링 확인 및 시작...",
        "en": "Confirm & Start Render...",
        "ja": "レンダリング確認と開始...",
    },
    "menu.opt_render": {
        "ko": "최적화 렌더 (실험)",
        "en": "Optimized Render (Experimental)",
        "ja": "最適化レンダー (実験的)",
    },
    "menu.out_file": {"ko": "출력 파일 경로 지정...", "en": "Set Output File Path...", "ja": "出力ファイルパスを指定..."},
    "menu.out_dir": {"ko": "출력 폴더 변경...", "en": "Change Output Folder...", "ja": "出力フォルダを変更..."},
    "menu.open_out_dir": {"ko": "출력 폴더 열기", "en": "Open Output Folder", "ja": "出力フォルダを開く"},

    "menu.help": {"ko": "도움말(&H)", "en": "&Help", "ja": "ヘルプ(&H)"},
    "menu.diag": {"ko": "하드웨어 및 인코더 진단", "en": "Hardware & Encoder Diagnostics", "ja": "ハードウェア・エンコーダー診断"},
    "menu.check_upd": {"ko": "업데이트 확인...", "en": "Check for Updates...", "ja": "アップデートを確認..."},
    "menu.about": {"ko": "프로그램 정보", "en": "About ADOFAI PV Renderer", "ja": "バージョン情報"},

    # ---------------- Viewport & Panes ----------------
    "vp.header": {"ko": "뷰어", "en": "Viewer", "ja": "ビューアー"},
    "vp.header_file": {"ko": "뷰어: {name}", "en": "Viewer: {name}", "ja": "ビューアー: {name}"},
    "vp.mode_fit": {"ko": "맞춤 (16:9)", "en": "Fit (16:9)", "ja": "フィット (16:9)"},
    "vp.mode_proxy": {"ko": "프록시 {w}×{h} · {fps}fps", "en": "Proxy {w}×{h} · {fps}fps", "ja": "プロキシ {w}×{h} · {fps}fps"},
    "vp.mode_none": {"ko": "프록시 없음", "en": "No Proxy", "ja": "プロキシなし"},
    "vp.mode_none_spec": {"ko": "프록시 없음 ({h}p · {fps}fps)", "en": "No Proxy ({h}p · {fps}fps)", "ja": "プロキシなし ({h}p · {fps}fps)"},
    "vp.empty_title": {"ko": "레벨을 불러와 주세요", "en": "Open a Level", "ja": "レベルを開いてください"},
    "vp.empty_sub": {
        "ko": ".adofai, .zip 파일 또는 레벨 폴더를 드래그하거나 열기 버튼으로 불러올 수 있습니다.",
        "en": "Drop a .adofai file, .zip archive, or level folder here, or click Open Level.",
        "ja": ".adofai・.zipファイルまたはフォルダをドロップするか、開くボタンで読み込めます。",
    },
    "vp.btn_open": {"ko": "레벨 열기...", "en": "Open Level...", "ja": "レベルを開く..."},
    "vp.btn_preview": {
        "ko": "프록시 생성",
        "en": "Generate Proxy",
        "ja": "プロキシ生成",
    },
    "vp.preview_hint": {
        "ko": "타임라인 구간 트림용 프록시 영상을 생성할 수 있습니다.",
        "en": "Generate a lightweight proxy video for accurate timeline scrubbing & trimming.",
        "ja": "タイムライン範囲選択用のプロキシ映像を生成できます。",
    },
    "vp.gen_title": {
        "ko": "프록시 생성 중...",
        "en": "Generating Proxy...",
        "ja": "プロキシを生成中...",
    },
    "vp.gen_sub_default": {
        "ko": "프록시 프레임을 생성하는 중입니다.",
        "en": "Rendering proxy video frames...",
        "ja": "プロキシフレームを生成しています...",
    },
    "vp.gen_engine_prep": {
        "ko": "프록시 렌더 엔진 준비 중...",
        "en": "Preparing proxy render engine...",
        "ja": "プロキシレンダーエンジンを準備中...",
    },
    "vp.level_detail": {
        "ko": "BPM {bpm:g}, 타일 {tiles:,}, 장식 {decos:,}, 필터 {filts}종",
        "en": "BPM {bpm:g}, Tiles {tiles:,}, Decorations {decos:,}, Filters {filts}",
        "ja": "BPM {bpm:g}、タイル {tiles:,}、装飾 {decos:,}、フィルター {filts}種",
    },

    # ---------------- Inspector ----------------
    "insp.header": {"ko": "출력 설정", "en": "Output Settings", "ja": "出力設定"},
    "insp.tab_out": {"ko": "출력", "en": "Output", "ja": "出力"},
    "insp.tab_seq": {"ko": "행성 · 시퀀스", "en": "Planets & Seq", "ja": "惑星・シーケンス"},
    "insp.no_level": {"ko": "레벨 미선택", "en": "No Level Loaded", "ja": "レベル未選択"},
    "insp.no_level_sub": {
        "ko": "레벨 열기 또는 드래그로 .adofai · .zip · 폴더를 불러오세요",
        "en": "Open or drop a .adofai file, .zip archive, or level folder",
        "ja": "開くメニューまたはドロップで .adofai・.zip・フォルダを読み込みます",
    },
    "insp.audio_label": {"ko": "오디오: {bgm}", "en": "Audio: {bgm}", "ja": "音声: {bgm}"},
    "insp.no_bgm": {"ko": "BGM 없음", "en": "No BGM", "ja": "BGMなし"},
    "insp.res": {"ko": "해상도", "en": "Resolution", "ja": "解像度"},
    "insp.fps": {"ko": "프레임", "en": "Frame Rate", "ja": "フレーム"},
    "insp.fps_tip": {
        "ko": "24~240 사이의 값(예: 120, 144, 240)을 직접 입력할 수 있습니다 (Enter로 적용)",
        "en": "Enter any frame rate between 24 and 240 (e.g. 60, 120, 144, 240; press Enter)",
        "ja": "24〜240の範囲(例: 60, 120, 144, 240)を直接入力できます (Enterで適用)",
    },
    "insp.fmt": {"ko": "포맷", "en": "Format", "ja": "形式"},
    "insp.codec": {"ko": "코덱", "en": "Codec", "ja": "コーデック"},
    "insp.bitrate": {"ko": "비트레이트", "en": "Bitrate", "ja": "ビットレート"},
    "insp.br_high": {"ko": "높음 (CQ 15)", "en": "High (CQ 15)", "ja": "高 (CQ 15)"},
    "insp.br_mid": {"ko": "중간 (CQ 19 · 권장)", "en": "Medium (CQ 19 · Rec)", "ja": "中 (CQ 19・推奨)"},
    "insp.br_low": {"ko": "낮음 (CQ 24)", "en": "Low (CQ 24)", "ja": "低 (CQ 24)"},
    "insp.audio": {"ko": "오디오", "en": "Audio", "ja": "音声"},
    "insp.bgm": {"ko": "배경음악", "en": "BGM", "ja": "BGM"},
    "insp.hitsound": {"ko": "히트사운드", "en": "Hitsounds", "ja": "ヒット音"},
    "insp.range": {"ko": "렌더 구간", "en": "Render Range", "ja": "レンダー範囲"},
    "insp.reset_full": {"ko": "전체 복원", "en": "Reset Full", "ja": "全体に戻す"},
    "insp.start": {"ko": "시작", "en": "Start", "ja": "開始"},
    "insp.end": {"ko": "종료", "en": "End", "ja": "終了"},
    "insp.filename": {"ko": "파일명", "en": "File", "ja": "ファイル名"},
    "insp.folder": {"ko": "폴더", "en": "Folder", "ja": "フォルダ"},
    "insp.folder_tip": {"ko": "출력 폴더 변경", "en": "Change output folder", "ja": "出力フォルダを変更"},
    "insp.est_dur": {"ko": "출력 길이", "en": "Duration", "ja": "出力長さ"},
    "insp.est_size": {"ko": "예상 용량", "en": "Est. Size", "ja": "予想サイズ"},
    "insp.est_time": {"ko": "예상 시간", "en": "Est. Time", "ja": "予想時間"},
    "insp.size_mb": {"ko": "약 {v:.0f} MB", "en": "~{v:.0f} MB", "ja": "約 {v:.0f} MB"},
    "insp.size_gb": {"ko": "약 {v:.2f} GB", "en": "~{v:.2f} GB", "ja": "約 {v:.2f} GB"},
    "insp.planet_hdr": {"ko": "행성 색상 및 불투명도", "en": "Planet Color & Opacity", "ja": "惑星の色と不透明度"},
    "insp.fire": {"ko": "불", "en": "Fire", "ja": "火"},
    "insp.ice": {"ko": "얼음", "en": "Ice", "ja": "氷"},
    "insp.fire_tip": {
        "ko": "불 행성 색상 및 불투명도 설정 (클릭하여 편집)",
        "en": "Edit Fire planet color and opacity",
        "ja": "火の惑星の色と不透明度を編集",
    },
    "insp.ice_tip": {
        "ko": "얼음 행성 색상 및 불투명도 설정 (클릭하여 편집)",
        "en": "Edit Ice planet color and opacity",
        "ja": "氷の惑星の色と不透明度を編集",
    },
    "insp.planet_reset_btn": {
        "ko": "기본값 복원 (순수 빨강/파랑 100%)",
        "en": "Restore Defaults (Pure Red / Blue 100%)",
        "ja": "初期値に戻す (純赤・純青 100%)",
    },
    "insp.seq_hdr": {"ko": "시퀀스", "en": "Sequence", "ja": "シーケンス"},
    "insp.end_pad": {"ko": "종료 여백", "en": "End Padding", "ja": "終了余白"},
    "insp.sec_suffix": {"ko": " 초", "en": " s", "ja": " 秒"},
    "insp.hide_cd": {"ko": "시작 카운트다운 숨기기", "en": "Hide start countdown", "ja": "開始カウントダウンを非表示"},
    "insp.btn_adv": {"ko": "세부 설정...", "en": "Settings...", "ja": "詳細設定..."},
    "insp.btn_adv_tip": {
        "ko": "하드웨어·GPU/RAM/CPU 상한·저사양 안전장치·최적화 렌더 설정 (Ctrl+,)",
        "en": "Hardware, GPU/RAM/CPU limits, low-spec safeguards & quality settings (Ctrl+,)",
        "ja": "ハードウェア・GPU/RAM/CPU上限・画質・最適化設定 (Ctrl+,)",
    },
    "insp.btn_render": {"ko": "렌더링...", "en": "Render...", "ja": "レンダリング..."},

    # ---------------- Timeline & Transport ----------------
    "tl.header": {"ko": "타임라인", "en": "Timeline", "ja": "タイムライン"},
    "tl.full_range": {"ko": "전체 구간", "en": "Full Range", "ja": "全体範囲"},
    "tl.range_full": {"ko": "전체 구간 (00:00.0 – {span})", "en": "Full Range (00:00.0 – {span})", "ja": "全体範囲 (00:00.0 – {span})"},
    "tl.range_trim": {"ko": "구간 트림 {s} – {e}", "en": "Trimmed {s} – {e}", "ja": "範囲トリム {s} – {e}"},
    "tl.frame_num": {"ko": "{n:,} 프레임", "en": "{n:,} f", "ja": "{n:,} f"},
    "tl.track_v1": {"ko": "V1 영상", "en": "V1 Video", "ja": "V1 映像"},
    "tl.track_fx": {"ko": "FX 효과", "en": "FX Events", "ja": "FX 効果"},
    "tl.track_a1": {"ko": "A1 오디오", "en": "A1 Audio", "ja": "A1 音声"},
    "tl.tip_start": {"ko": "처음으로 (Home)", "en": "Jump to Start (Home)", "ja": "先頭へ移動 (Home)"},
    "tl.tip_step_back": {"ko": "이전 프레임 (Left)", "en": "Previous Frame (Left)", "ja": "前のフレーム (Left)"},
    "tl.tip_play": {"ko": "재생 / 일시정지 (Space)", "en": "Play / Pause (Space)", "ja": "再生 / 一時停止 (Space)"},
    "tl.tip_step_fwd": {"ko": "다음 프레임 (Right)", "en": "Next Frame (Right)", "ja": "次のフレーム (Right)"},
    "tl.tip_end": {"ko": "끝으로 (End)", "en": "Jump to End (End)", "ja": "末尾へ移動 (End)"},
    "tl.tip_mark_in": {"ko": "트림 시작점 지정 (I)", "en": "Mark In (I)", "ja": "開始点マーク (I)"},
    "tl.tip_mark_out": {"ko": "트림 종료점 지정 (O)", "en": "Mark Out (O)", "ja": "終了点マーク (O)"},
    "tl.tip_mute": {"ko": "프록시 오디오 음소거", "en": "Mute Proxy Audio", "ja": "プロキシ音声ミュート"},
    "tl.tip_vol": {"ko": "프록시 볼륨", "en": "Proxy Volume", "ja": "プロキシ音量"},
    "tl.tip_zoom": {"ko": "타임라인 확대/축소 (휠 스크롤 지원)", "en": "Timeline Zoom (Mouse Wheel)", "ja": "タイムライン拡大・縮小 (ホイール対応)"},
    "tl.btn_fit": {"ko": "맞춤", "en": "Fit", "ja": "全体"},

    # ---------------- Status Bar & Common Messages ----------------
    "sb.ready": {"ko": "준비", "en": "Ready", "ja": "準備完了"},
    "sb.rendering": {"ko": "렌더링 중", "en": "Rendering", "ja": "レンダリング中"},
    "sb.previewing": {"ko": "프록시 생성 중", "en": "Building Proxy", "ja": "プロキシ生成中"},
    "sb.steam_ok": {"ko": "  Steam ADOFAI 연결됨  ", "en": "  Steam ADOFAI Connected  ", "ja": "  Steam ADOFAI 接続済み  "},
    "sb.steam_none": {"ko": "  ADOFAI 경로 미지정  ", "en": "  ADOFAI Path Not Set  ", "ja": "  ADOFAI パス未指定  "},
    "sb.opt_on": {"ko": "  최적화 렌더(실험) 활성  ", "en": "  Optimized Render (Exp) ON  ", "ja": "  最適化レンダー(実験) ON  "},
    "sb.chart_info": {
        "ko": "  타일 {tiles:,}  장식 {decos:,}  필터 {filts}  ",
        "en": "  Tiles {tiles:,}  Decos {decos:,}  Filters {filts}  ",
        "ja": "  タイル {tiles:,}  装飾 {decos:,}  フィルター {filts}  ",
    },
    "sb.no_level": {"ko": "  레벨 없음  ", "en": "  No Level  ", "ja": "  レベルなし  "},
    "sb.preview_loaded": {"ko": "프록시가 로드되었습니다.", "en": "Proxy loaded.", "ja": "プロキシを読み込みました。"},
    "sb.proxy_updated": {"ko": "프록시 설정이 적용되었습니다 ({w}×{h} · {fps}fps).", "en": "Proxy settings updated ({w}×{h} · {fps}fps).", "ja": "プロキシ設定を適用しました ({w}×{h} · {fps}fps)。"},
    "sb.render_done": {"ko": "렌더링 완료: {path}", "en": "Render complete: {path}", "ja": "レンダリング完了: {path}"},
    "sb.checking_upd": {"ko": "업데이트를 확인하는 중...", "en": "Checking for updates...", "ja": "アップデートを確認中..."},

    # ---------------- Dialogs & Message Boxes ----------------
    "dlg.open_title": {"ko": "ADOFAI 레벨 열기", "en": "Open ADOFAI Level", "ja": "ADOFAIレベルを開く"},
    "dlg.open_filter": {
        "ko": "ADOFAI 레벨 (*.adofai *.zip);;모든 파일 (*)",
        "en": "ADOFAI Level (*.adofai *.zip);;All Files (*)",
        "ja": "ADOFAIレベル (*.adofai *.zip);;すべてのファイル (*)",
    },
    "dlg.load_fail_title": {"ko": "레벨 불러오기 실패", "en": "Failed to Load Level", "ja": "レベルの読み込みに失敗"},
    "dlg.no_level_title": {"ko": "레벨 미선택", "en": "No Level Selected", "ja": "レベル未選択"},
    "dlg.no_level_preview_msg": {
        "ko": "먼저 .adofai 레벨을 열어주세요.",
        "en": "Please open an ADOFAI level first.",
        "ja": "先にADOFAIレベルを開いてください。",
    },
    "dlg.no_level_render_msg": {
        "ko": "먼저 렌더링할 .adofai 레벨을 열어주세요.",
        "en": "Please open an ADOFAI level to render first.",
        "ja": "先にレンダリングするレベルを開いてください。",
    },
    "dlg.preview_fail_title": {"ko": "프록시 생성 실패", "en": "Proxy Generation Failed", "ja": "プロキシ生成失敗"},
    "dlg.preview_fail_default": {
        "ko": "프록시를 생성하지 못했습니다.",
        "en": "Could not generate the proxy.",
        "ja": "プロキシを生成できませんでした。",
    },
    "dlg.steam_dir_title": {"ko": "Steam ADOFAI 폴더 선택", "en": "Select Steam ADOFAI Folder", "ja": "Steam ADOFAIフォルダを選択"},
    "dlg.out_file_title": {"ko": "출력 파일 경로 지정", "en": "Choose Output File Path", "ja": "出力ファイルパスを選択"},
    "dlg.out_dir_title": {"ko": "출력 폴더 지정", "en": "Choose Output Folder", "ja": "出力フォルダを選択"},
    "dlg.diag_title": {"ko": "하드웨어 및 인코더 진단", "en": "Hardware & Encoder Diagnostics", "ja": "ハードウェア・エンコーダー診断"},
    "dlg.diag_none": {"ko": "  (없음)", "en": "  (None)", "ja": "  (なし)"},
    "dlg.diag_undetected": {"ko": "(미감지)", "en": "(Not detected)", "ja": "(未検出)"},
    "dlg.diag_body": {
        "ko": "Steam ADOFAI: {adofai}\nFFmpeg: {ffmpeg}\n감지된 GPU:\n{gpus}\n검증된 인코더: {encs}\n권장 백엔드: {rec}",
        "en": "Steam ADOFAI: {adofai}\nFFmpeg: {ffmpeg}\nDetected GPUs:\n{gpus}\nVerified Encoders: {encs}\nRecommended Backend: {rec}",
        "ja": "Steam ADOFAI: {adofai}\nFFmpeg: {ffmpeg}\n検出されたGPU:\n{gpus}\n検証済みエンコーダー: {encs}\n推奨バックエンド: {rec}",
    },
    "dlg.about_body": {
        "ko": "ADOFAI PV Renderer v{ver}\n\n결정론적 오프라인 렌더링 및 실제 인게임 셰이더·필터·히트사운드 합성 도구.",
        "en": "ADOFAI PV Renderer v{ver}\n\nDeterministic offline PV renderer with authentic in-game shaders, filters, and hitsound mixing.",
        "ja": "ADOFAI PV Renderer v{ver}\n\nゲーム本来のシェーダー・フィルター・ヒット音合成に対応した決定論的オフラインPVレンダラー。",
    },

    # Updates
    "upd.title": {"ko": "업데이트", "en": "Updates", "ja": "アップデート"},
    "upd.fail_title": {"ko": "업데이트 확인 실패", "en": "Update Check Failed", "ja": "アップデート確認失敗"},
    "upd.fail_msg": {
        "ko": "업데이트 정보를 가져오지 못했습니다.\n{err}",
        "en": "Failed to fetch update info.\n{err}",
        "ja": "アップデート情報を取得できませんでした。\n{err}",
    },
    "upd.latest": {
        "ko": "현재 최신 버전입니다 (v{ver}).",
        "en": "You are already on the latest version (v{ver}).",
        "ja": "最新バージョンです (v{ver})。",
    },
    "upd.avail_title": {"ko": "업데이트 가능", "en": "Update Available", "ja": "アップデート利用可能"},
    "upd.avail_text": {
        "ko": "새 버전 v{new} 이(가) 있습니다. (현재 v{cur})",
        "en": "A new version v{new} is available (current: v{cur}).",
        "ja": "新しいバージョン v{new} が利用可能です (現在: v{cur})。",
    },
    "upd.no_notes": {"ko": "릴리스 노트가 없습니다.", "en": "No release notes provided.", "ja": "リリースノートはありません。"},
    "upd.btn_now": {"ko": "지금 업데이트", "en": "Update Now", "ja": "今すぐアップデート"},
    "upd.btn_page": {"ko": "릴리스 페이지", "en": "Release Page", "ja": "リリースページ"},
    "upd.btn_skip": {"ko": "이 버전 건너뛰기", "en": "Skip This Version", "ja": "このバージョンをスキップ"},
    "upd.btn_later": {"ko": "나중에", "en": "Later", "ja": "後で"},
    "upd.dl_msg": {
        "ko": "v{ver} 설치 파일을 내려받는 중...",
        "en": "Downloading v{ver} installer...",
        "ja": "v{ver} インストーラーをダウンロード中...",
    },
    "upd.dl_fail": {"ko": "업데이트 실패", "en": "Update Failed", "ja": "アップデート失敗"},
    "upd.busy_msg": {
        "ko": "렌더링 중에는 설치할 수 없습니다.\n끝난 뒤 직접 실행해 주세요:\n{path}",
        "en": "Cannot install while rendering.\nPlease run the installer after completion:\n{path}",
        "ja": "レンダリング中はインストールできません。\n完了後に手動で実行してください:\n{path}",
    },
    "upd.install_title": {"ko": "업데이트 설치", "en": "Install Update", "ja": "アップデートをインストール"},
    "upd.install_confirm": {
        "ko": "설치 프로그램을 실행하면 앱이 종료되고 업데이트 후 다시 시작됩니다. 계속할까요?",
        "en": "Running the installer will close the app and restart it after updating. Continue?",
        "ja": "インストーラーを実行するとアプリが終了し、更新後に再起動します。続行しますか？",
    },

    # Common Buttons
    "btn.ok": {"ko": "확인", "en": "OK", "ja": "OK"},
    "btn.apply": {"ko": "적용", "en": "Apply", "ja": "適用"},
    "btn.save": {"ko": "저장", "en": "Save", "ja": "保存"},
    "btn.cancel": {"ko": "취소", "en": "Cancel", "ja": "キャンセル"},
    "btn.close": {"ko": "닫기", "en": "Close", "ja": "閉じる"},
    "btn.restore_defaults": {"ko": "기본값 복원", "en": "Restore Defaults", "ja": "初期値に戻す"},

    # Planet Dialog
    "planet.title": {
        "ko": "행성(공) 색상 및 불투명도 설정",
        "en": "Planet Color & Opacity Settings",
        "ja": "惑星の色と不透明度設定",
    },
    "planet.fire": {"ko": "불 행성 (1번 공)", "en": "Fire Planet (Planet 1)", "ja": "火の惑星 (1番)"},
    "planet.ice": {"ko": "얼음 행성 (2번 공)", "en": "Ice Planet (Planet 2)", "ja": "氷の惑星 (2番)"},
    "planet.opacity": {"ko": "불투명도", "en": "Opacity", "ja": "不透明度"},
    "planet.pick_color": {"ko": "행성 색상 선택", "en": "Select Planet Color", "ja": "惑星の色を選択"},
    "planet.note": {
        "ko": "기본값: 불투명도 100% 순수 빨강(#FF0000) · 순수 파랑(#0000FF). 3구체 레벨은 두 색상 사이로 자동 보간됩니다.",
        "en": "Default: Pure Red (#FF0000) & Pure Blue (#0000FF) at 100% opacity. Multi-planet levels interpolate automatically.",
        "ja": "初期値: 不透明度100% 純赤(#FF0000)・純青(#0000FF)。3惑星レベルは2色の間を自動補間します。",
    },

    # Sequence Dialog
    "seq.title": {"ko": "시퀀스 및 구간 설정", "en": "Sequence & Range Settings", "ja": "シーケンスと範囲設定"},
    "seq.start": {"ko": "시작 시점 (초):", "en": "Start time (s):", "ja": "開始時刻 (秒):"},
    "seq.end": {"ko": "종료 시점 (0 = 끝까지):", "en": "End time (0 = full):", "ja": "終了時刻 (0 = 最後まで):"},
    "seq.pad": {"ko": "클리어 후 여백:", "en": "End padding:", "ja": "クリア後の余白:"},
    "seq.hide_cd": {
        "ko": "시작 카운트다운(3-2-1) 숨김 (제목·판정·Auto 아이콘은 항상 숨김)",
        "en": "Hide start countdown (3-2-1) (title, judgments & Auto icon are always hidden)",
        "ja": "開始カウントダウン(3-2-1)を非表示 (タイトル・判定・Autoアイコンは常に非表示)",
    },
    "seq.reset_full": {"ko": "전체 구간으로 초기화", "en": "Reset to Full", "ja": "全体範囲にリセット"},

    # Proxy Settings Dialog
    "proxy.title": {"ko": "프록시 설정", "en": "Proxy Settings", "ja": "プロキシ設定"},
    "proxy.res": {"ko": "프록시 해상도:", "en": "Proxy Resolution:", "ja": "プロキシ解像度:"},
    "proxy.fps": {"ko": "프록시 프레임:", "en": "Proxy Frame Rate:", "ja": "プロキシフレーム:"},
    "proxy.fps_tip": {
        "ko": "5~60 fps 범위에서 원하는 프록시 프레임을 설정할 수 있습니다.",
        "en": "Set proxy frame rate between 5 and 60 fps.",
        "ja": "5〜60 fpsの範囲でプロキシのフレームレートを設定できます。",
    },
    "proxy.quick": {"ko": "빠른 프리셋:", "en": "Quick Presets:", "ja": "クイックプリセット:"},
    "proxy.preset_ultra_fast": {"ko": "초고속 (144p · 5fps)", "en": "Ultra Fast (144p · 5fps)", "ja": "超高速 (144p · 5fps)"},
    "proxy.preset_std": {"ko": "표준 (270p · 12fps)", "en": "Standard (270p · 12fps)", "ja": "標準 (270p · 12fps)"},
    "proxy.preset_smooth": {"ko": "부드러움 (480p · 30fps)", "en": "Smooth (480p · 30fps)", "ja": "滑らか (480p · 30fps)"},
    "proxy.preset_hd": {"ko": "고화질 (720p · 60fps)", "en": "HD (720p · 60fps)", "ja": "高画質 (720p · 60fps)"},
    "proxy.note": {
        "ko": "해상도(144p~720p)와 프레임(5~60fps)이 낮을수록 프록시 생성이 빨라지며, 최종 렌더링 화질에는 영향을 주지 않습니다.",
        "en": "Lower resolution (144p–720p) and frame rate (5–60 fps) build proxies faster and never affect final render quality.",
        "ja": "解像度(144p〜720p)やフレーム(5〜60fps)を下げるとプロキシ生成が高速になります。最終レンダリング画質には影響しません。",
    },

    # Export / Preferences Dialog
    "exp.title": {
        "ko": "세부 설정 (하드웨어 · 오디오)",
        "en": "Settings (Hardware · Audio)",
        "ja": "詳細設定 (ハードウェア・音声)",
    },
    "exp.tab_hw": {"ko": "하드웨어 · 리소스", "en": "Hardware & Resources", "ja": "ハードウェア・リソース"},
    "exp.tab_qa": {"ko": "오디오 믹싱", "en": "Audio Mixing", "ja": "音声ミキシング"},
    "exp.gpu_backend": {"ko": "GPU 가속 백엔드:", "en": "GPU Backend:", "ja": "GPUバックエンド:"},
    "exp.gpu_auto": {
        "ko": "자동 (NVIDIA CUDA 우선 → 내장 GPU → CPU)",
        "en": "Auto (NVIDIA CUDA → iGPU → CPU)",
        "ja": "自動 (NVIDIA CUDA優先 → 内蔵GPU → CPU)",
    },
    "exp.gpu_nvenc": {"ko": "NVIDIA CUDA / NVENC", "en": "NVIDIA CUDA / NVENC", "ja": "NVIDIA CUDA / NVENC"},
    "exp.gpu_igpu": {
        "ko": "내장 그래픽 (Intel QSV / AMD AMF)",
        "en": "Integrated GPU (Intel QSV / AMD AMF)",
        "ja": "内蔵グラフィックス (Intel QSV / AMD AMF)",
    },
    "exp.gpu_cpu": {
        "ko": "CPU 소프트웨어 (libx264 / libx265)",
        "en": "CPU Software (libx264 / libx265)",
        "ja": "CPUソフトウェア (libx264 / libx265)",
    },
    "exp.gpu_cap": {"ko": "최대 GPU 사용 상한:", "en": "Max GPU Usage Cap:", "ja": "最大GPU使用上限:"},
    "exp.ram_cap": {"ko": "최대 RAM 사용 상한:", "en": "Max RAM Usage Cap:", "ja": "最大RAM使用上限:"},
    "exp.cpu_cores": {"ko": "최대 점유 CPU 코어:", "en": "Max CPU Cores:", "ja": "最大使用CPUコア:"},
    "exp.cpu_auto": {
        "ko": "자동 (전체 {n} 스레드 활용)",
        "en": "Auto (all {n} threads)",
        "ja": "自動 (全 {n} スレッド使用)",
    },
    "exp.cpu_n": {"ko": "최대 {c} 코어", "en": "Up to {c} cores", "ja": "最大 {c} コア"},
    "exp.lowspec": {"ko": "저사양 안전 우회:", "en": "Low-Spec Safeguard:", "ja": "低スペック安全フォールバック:"},
    "exp.lowspec_auto": {
        "ko": "자동 감지 (권장 · 오류 시 동기 캡처/CPU 자동 우회)",
        "en": "Auto (Recommended · fallback to sync/CPU on error)",
        "ja": "自動検出 (推奨・エラー時に同期/CPUへ自動切替)",
    },
    "exp.lowspec_force": {
        "ko": "강제 활성화 (저VRAM 단일 슬롯 + 동기 ReadPixels)",
        "en": "Force ON (low-VRAM single slot + sync ReadPixels)",
        "ja": "強制有効 (省VRAM単一スロット + 同期ReadPixels)",
    },
    "exp.lowspec_off": {
        "ko": "표준 고속 모드 (실패 시에만 안전 우회)",
        "en": "Standard Fast Mode (fallback only on failure)",
        "ja": "標準高速モード (失敗時のみフォールバック)",
    },
    "exp.opt_chk": {
        "ko": "최적화 렌더 (실험): 불투명도 0%·화면 밖 장식 제외 및 고해상도 장식 LOD 프록시 교체",
        "en": "Optimized Render (Experimental): Cull 0%-opacity/offscreen decos & LOD proxies for large textures",
        "ja": "最適化レンダー (実験的): 不透明度0%・画面外装飾のカリングと高解像度装飾のLOD置換",
    },
    "exp.hw_note": {
        "ko": "기본값: GPU 90% · RAM 80% · CPU 자동. 최적화 렌더(실험)는 512px 이상 고해상도 장식의 멀티 해상도 프록시를 사전 생성해 화면 픽셀 크기가 작을 때만 자동 교체하며(1.35× 나이퀴스트 마진), 최종 결과물 화질은 유지하면서 병목을 줄입니다.",
        "en": "Defaults: GPU 90% · RAM 80% · CPU Auto. Optimized Render (Experimental) builds mipmap LOD proxies for 512px+ decorations with a 1.35x Nyquist margin to speed up heavy charts without quality loss.",
        "ja": "初期値: GPU 90%・RAM 80%・CPU自動。最適化レンダー(実験的)は512px以上の高解像度装飾にLODプロキシを生成し、画質を維持しながら負荷を軽減します。",
    },
    "exp.qual_label": {"ko": "인코딩 화질:", "en": "Encoding Quality:", "ja": "エンコード画質:"},
    "exp.qual_ultra": {"ko": "최고 화질 (CQ 15 · PV 마스터)", "en": "Ultra (CQ 15 · PV Master)", "ja": "最高画質 (CQ 15・PVマスター)"},
    "exp.qual_high": {"ko": "고화질 (CQ 19 · 권장)", "en": "High (CQ 19 · Recommended)", "ja": "高画質 (CQ 19・推奨)"},
    "exp.qual_bal": {"ko": "표준 용량 (CQ 23)", "en": "Balanced (CQ 23)", "ja": "標準容量 (CQ 23)"},
    "exp.bgm_vol": {"ko": "배경음악 (BGM) 볼륨:", "en": "BGM Volume:", "ja": "BGM音量:"},
    "exp.hit_vol": {"ko": "인게임 히트사운드 볼륨:", "en": "Hitsound Volume:", "ja": "ヒット音音量:"},
    "exp.abit": {"ko": "오디오 비트레이트:", "en": "Audio Bitrate:", "ja": "音声ビットレート:"},

    # Render Confirm Dialog
    "conf.title": {"ko": "최종 렌더링 설정 확인", "en": "Confirm Render Settings", "ja": "最終レンダリング設定の確認"},
    "conf.hdr": {
        "ko": "렌더링을 시작하기 전에 최종 설정을 확인하세요",
        "en": "Review your final render settings before starting",
        "ja": "レンダリングを開始する前に最終設定を確認してください",
    },
    "conf.est_time": {"ko": "예상 소요 시간", "en": "Estimated Time", "ja": "予想所要時間"},
    "conf.est_clk": {"ko": "예상 완료 시각", "en": "Estimated Finish", "ja": "予想完了時刻"},
    "conf.range_trim": {
        "ko": "구간 트림 ({s} – {e} · {dur:.1f}초 · {frames:,}프레임)",
        "en": "Trimmed ({s} – {e} · {dur:.1f}s · {frames:,} frames)",
        "ja": "範囲トリム ({s} – {e} · {dur:.1f}秒 · {frames:,}フレーム)",
    },
    "conf.range_full": {
        "ko": "전체 렌더링 ({dur:.1f}초 · {frames:,}프레임)",
        "en": "Full Level ({dur:.1f}s · {frames:,} frames)",
        "ja": "全体レンダリング ({dur:.1f}秒 · {frames:,}フレーム)",
    },
    "conf.end_word": {"ko": "끝", "en": "End", "ja": "末尾"},
    "conf.q_ultra": {"ko": "높음 (CQ 15)", "en": "High (CQ 15)", "ja": "高 (CQ 15)"},
    "conf.q_high": {"ko": "중간 (CQ 19)", "en": "Medium (CQ 19)", "ja": "中 (CQ 19)"},
    "conf.q_bal": {"ko": "낮음 (CQ 24)", "en": "Low (CQ 24)", "ja": "低 (CQ 24)"},
    "conf.bgm_on": {"ko": "BGM {v}%", "en": "BGM {v}%", "ja": "BGM {v}%"},
    "conf.bgm_off": {"ko": "BGM 끔", "en": "BGM Off", "ja": "BGM オフ"},
    "conf.hit_on": {"ko": "히트사운드 {v}%", "en": "Hitsound {v}%", "ja": "ヒット音 {v}%"},
    "conf.hit_off": {"ko": "히트사운드 끔", "en": "Hitsound Off", "ja": "ヒット音 オフ"},
    "conf.cpu_n": {"ko": "CPU {n}코어", "en": "CPU {n} cores", "ja": "CPU {n}コア"},
    "conf.cpu_auto": {"ko": "CPU 자동", "en": "CPU Auto", "ja": "CPU 自動"},
    "conf.ls_auto": {"ko": "안전 폴백 자동", "en": "Auto fallback", "ja": "自動フォールバック"},
    "conf.ls_force": {"ko": "저사양 안전 강제", "en": "Low-spec forced", "ja": "低スペック強制"},
    "conf.ls_off": {"ko": "표준 고속", "en": "Standard fast", "ja": "標準高速"},
    "conf.opt_tag": {"ko": " · 최적화 렌더(실험) ON", "en": " · Optimized Render ON", "ja": " · 最適化レンダー ON"},
    "conf.row_level": {"ko": "레벨", "en": "Level", "ja": "レベル"},
    "conf.row_range": {"ko": "렌더 구간", "en": "Range", "ja": "範囲"},
    "conf.row_video": {"ko": "비디오 스펙", "en": "Video", "ja": "映像"},
    "conf.row_audio": {"ko": "오디오", "en": "Audio", "ja": "音声"},
    "conf.row_hw": {"ko": "하드웨어", "en": "Hardware", "ja": "ハードウェア"},
    "conf.row_out": {"ko": "출력 파일", "en": "Output", "ja": "出力先"},
    "conf.row_planets": {"ko": "행성 색상", "en": "Planets", "ja": "惑星色"},
    "conf.btn_edit": {"ko": "설정 수정...", "en": "Edit Settings...", "ja": "設定を変更..."},
    "conf.btn_start": {"ko": "렌더링 시작", "en": "Start Render", "ja": "レンダリング開始"},

    # Render Card Dialog
    "rc.title_running": {"ko": "렌더링 진행 중", "en": "Rendering in Progress", "ja": "レンダリング中"},
    "rc.title_done": {"ko": "렌더링 완료", "en": "Render Complete", "ja": "レンダリング完了"},
    "rc.title_stop": {"ko": "렌더링 중단", "en": "Render Stopped", "ja": "レンダリング中断"},
    "rc.metric_pct": {"ko": "진행률", "en": "Progress", "ja": "進捗"},
    "rc.metric_rem": {"ko": "예상 남은 시간", "en": "Time Remaining", "ja": "残り予想時間"},
    "rc.metric_eta": {"ko": "예상 종료 시각", "en": "Estimated Finish", "ja": "完了予想時刻"},
    "rc.detail_init": {
        "ko": "오프라인 렌더 백엔드를 시작하는 중입니다...",
        "en": "Starting offline render backend...",
        "ja": "オフラインレンダーバックエンドを起動中...",
    },
    "rc.btn_log_show": {"ko": "콘솔 로그 ▾", "en": "Console Log ▾", "ja": "コンソールログ ▾"},
    "rc.btn_log_hide": {"ko": "콘솔 로그 ▴", "en": "Console Log ▴", "ja": "コンソールログ ▴"},
    "rc.btn_open_dir": {"ko": "출력 폴더 열기", "en": "Open Output Folder", "ja": "出力フォルダを開く"},
    "rc.btn_cancel": {"ko": "취소 (Esc)", "en": "Cancel (Esc)", "ja": "キャンセル (Esc)"},
    "rc.btn_cancelling": {"ko": "중단 중...", "en": "Cancelling...", "ja": "中断中..."},
    "rc.cancel_sent": {"ko": "취소 요청을 전송했습니다...", "en": "Cancellation requested...", "ja": "キャンセル要求を送信しました..."},
    "rc.finishing": {"ko": "마무리 중", "en": "Finishing", "ja": "仕上げ中"},
    "rc.done_word": {"ko": "완료", "en": "Done", "ja": "完了"},
    "rc.stopped_word": {"ko": "중단됨", "en": "Stopped", "ja": "中断"},
    "rc.stopped_default": {"ko": "렌더링이 중단되었습니다.", "en": "Rendering was stopped.", "ja": "レンダリングが中断されました。"},
    "rc.frames_done": {"ko": "{n:,}프레임 완료", "en": "{n:,} frames done", "ja": "{n:,}フレーム完了"},
    "rc.done_detail": {
        "ko": "{frames:,}프레임 ({sec:.1f}s) · {enc}{fb}",
        "en": "{frames:,} frames ({sec:.1f}s) · {enc}{fb}",
        "ja": "{frames:,}フレーム ({sec:.1f}s) · {enc}{fb}",
    },
    "rc.v_launching": {"ko": "렌더 엔진 실행 중...", "en": "Launching render engine...", "ja": "レンダーエンジン起動中..."},
    "rc.v_loading_scene": {"ko": "게임 씬 초기화 중...", "en": "Initializing game scene...", "ja": "ゲームシーン初期化中..."},
    "rc.v_loading_level": {"ko": "레벨 · 장식 로딩 중...", "en": "Loading level & decorations...", "ja": "レベル・装飾を読み込み中..."},
    "rc.v_loading_level_opt": {"ko": "레벨 · 최적화 프록시 준비 중...", "en": "Preparing level & LOD proxies...", "ja": "レベル・最適化プロキシ準備中..."},
    "rc.v_starting_playback": {"ko": "가상 클럭 동기화 중...", "en": "Syncing virtual clock...", "ja": "仮想クロック同期中..."},
    "rc.v_seeking": {"ko": "시작 시점 탐색 중...", "en": "Seeking to start time...", "ja": "開始位置へシーク中..."},
    "rc.v_rendering": {"ko": "프레임 렌더링 중...", "en": "Rendering frames...", "ja": "フレーム描画中..."},
    "rc.v_done": {"ko": "인코더 스트림 정리 중...", "en": "Flushing encoder stream...", "ja": "エンコーダー終了処理中..."},
    "rc.v_mixing_audio": {"ko": "오디오 정밀 믹싱 중...", "en": "Mixing offline audio...", "ja": "音声オフラインミキシング中..."},
    "rc.v_muxing": {"ko": "최종 영상 합성 중...", "en": "Muxing final video...", "ja": "最終動画を合成中..."},
    "rc.v_completed": {"ko": "렌더링 완료", "en": "Render complete", "ja": "レンダリング完了"},
    "rc.v_cancelled": {"ko": "렌더링 중단됨", "en": "Render cancelled", "ja": "レンダリング中断"},
    "rc.v_working": {"ko": "작업 처리 중...", "en": "Processing...", "ja": "処理中..."},
    "rc.h_launching": {"ko": "Steam ADOFAI 초기화", "en": "Init Steam ADOFAI", "ja": "Steam ADOFAI初期化"},
    "rc.h_loading_scene": {"ko": "오프스크린 RT 준비", "en": "Prepare offscreen RT", "ja": "オフスクリーンRT準備"},
    "rc.h_loading_level": {"ko": "장식 · 셰이더 로드", "en": "Load decos & shaders", "ja": "装飾・シェーダー読込"},
    "rc.h_loading_level_opt": {"ko": "장식 컬링 · LOD 인덱싱", "en": "Deco culling & LOD indexing", "ja": "カリング・LOD構築"},
    "rc.h_starting_playback": {"ko": "결정론적 CFR 고정", "en": "Lock deterministic CFR", "ja": "決定論的CFR固定"},
    "rc.h_done": {"ko": "인코더 버퍼 플러시", "en": "Flush encoder buffer", "ja": "バッファフラッシュ"},
    "rc.h_mixing_audio": {"ko": "원곡 · 히트사운드 합성", "en": "Mix BGM & hitsounds", "ja": "BGM・ヒット音合成"},
    "rc.h_muxing": {"ko": "MP4/MKV 컨테이너 출력", "en": "Write MP4/MKV container", "ja": "MP4/MKVコンテナ出力"},
    "rc.esc_cancel": {"ko": "esc to cancel", "en": "esc to cancel", "ja": "Escでキャンセル"},
}


def tr(key: str, **kwargs) -> str:
    lang = get_language()
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("ko") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
