#!/usr/bin/env python3
"""GMMA analyzer for 4-chart capture PNG.

This module reads TradingView-like capture images and classifies the 5m chart
trend into:
  - 新規上昇トレンド発生
  - 上昇トレンド継続
  - トレンドなし、またはトレンド減衰
  - 新規下落トレンド発生
  - 下落トレンド継続
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


STATE_NEW_UP = "新規上昇トレンド発生"
STATE_UP_CONT = "上昇トレンド継続"
STATE_NEUTRAL = "トレンドなし、またはトレンド減衰"
STATE_NEW_DOWN = "新規下落トレンド発生"
STATE_DOWN_CONT = "下落トレンド継続"

UP_STATES = {STATE_NEW_UP, STATE_UP_CONT}
DOWN_STATES = {STATE_NEW_DOWN, STATE_DOWN_CONT}

PIXEL_RE = re.compile(r"^(\d+),(\d+): \((\d+),(\d+),(\d+)")


@dataclass
class QuadrantStats:
    name: str
    short_pixels: int
    long_pixels: int
    bullish_pixels: int
    bearish_pixels: int
    recognized: bool


@dataclass
class Metrics:
    short_slope: float
    long_slope: float
    group_gap: float
    short_spread: float
    long_spread: float
    short_mean_y: float
    long_mean_y: float
    short_columns: int
    long_columns: int
    short_pixels_right: int
    long_pixels_right: int
    roi_width: int
    roi_height: int
    effective_plot_right_x: int
    effective_plot_width: int
    analysis_window_start_x: int
    analysis_window_end_x: int
    analysis_window_width: int


@dataclass
class RightBoundaryDetectionResult:
    effective_plot_right_x: int
    source: str
    label_run_start: Optional[int]
    label_run_end: Optional[int]
    spacer_run_start: Optional[int]
    spacer_run_end: Optional[int]
    rightmost_line_x: Optional[int]


@dataclass
class AnalysisResult:
    ts: str
    state: str
    confidence: float
    metrics: Dict[str, float]
    source_mtime: Optional[str]
    note: str


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def in_hue_range(hue: float, low: float, high: float) -> bool:
    if low <= high:
        return low <= hue <= high
    return hue >= low or hue <= high


def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    rf = r / 255.0
    gf = g / 255.0
    bf = b / 255.0
    cmax = max(rf, gf, bf)
    cmin = min(rf, gf, bf)
    delta = cmax - cmin

    if delta == 0:
        hue = 0.0
    elif cmax == rf:
        hue = (60.0 * ((gf - bf) / delta) + 360.0) % 360.0
    elif cmax == gf:
        hue = (60.0 * ((bf - rf) / delta) + 120.0) % 360.0
    else:
        hue = (60.0 * ((rf - gf) / delta) + 240.0) % 360.0

    sat = 0.0 if cmax == 0 else delta / cmax
    val = cmax
    return hue, sat, val


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_iso_from_epoch(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def run_magick(args: List[str]) -> str:
    completed = subprocess.run(
        ["magick", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def image_size(image_path: str) -> Tuple[int, int]:
    out = run_magick(["identify", "-format", "%w %h", image_path]).strip()
    width, height = out.split()
    return int(width), int(height)


def quadrant_rects(width: int, height: int) -> Dict[str, Tuple[int, int, int, int]]:
    half_w = width // 2
    half_h = height // 2
    return {
        "daily": (0, 0, half_w, half_h),
        "1h": (half_w, 0, width - half_w, half_h),
        "15m": (0, half_h, half_w, height - half_h),
        "5m": (half_w, half_h, width - half_w, height - half_h),
    }


def inner_plot_rect(rect: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    x, y, w, h = rect
    # Ignore top header / bottom timeline / right axis labels.
    ix = x + int(w * 0.06)
    iy = y + int(h * 0.08)
    iw = max(1, int(w * 0.84))
    ih = max(1, int(h * 0.82))
    return ix, iy, iw, ih


def pixel_stream_for_crop(
    image_path: str, crop_rect: Tuple[int, int, int, int]
) -> Iterable[Tuple[int, int, int, int, int]]:
    x, y, w, h = crop_rect
    out = run_magick([image_path, "-crop", f"{w}x{h}+{x}+{y}", "+repage", "txt:-"])
    for line in out.splitlines():
        match = PIXEL_RE.match(line)
        if not match:
            continue
        px, py, r, g, b = map(int, match.groups())
        yield px, py, r, g, b


def classify_color_masks(hue: float, sat: float, val: float) -> Dict[str, bool]:
    if val < 0.22:
        return {
            "short": False,
            "long": False,
            "bullish": False,
            "bearish": False,
        }

    short = in_hue_range(hue, 330.0, 350.0) and sat >= 0.20
    long = in_hue_range(hue, 20.0, 45.0) and sat >= 0.25
    bullish = in_hue_range(hue, 165.0, 200.0) and sat >= 0.20 and val >= 0.30
    bearish = in_hue_range(hue, 345.0, 15.0) and sat >= 0.35 and val >= 0.35
    return {
        "short": short,
        "long": long,
        "bullish": bullish,
        "bearish": bearish,
    }


def is_white_label_pixel(hue: float, sat: float, val: float) -> bool:
    del hue
    return val >= 0.70 and sat <= 0.20


def is_dark_bg_pixel(hue: float, sat: float, val: float) -> bool:
    del hue
    return val <= 0.10 and sat <= 0.35


def true_runs(flags: List[bool], start: int = 0, end: Optional[int] = None) -> List[Tuple[int, int]]:
    if end is None:
        end = len(flags)
    runs: List[Tuple[int, int]] = []
    run_start: Optional[int] = None
    for idx in range(max(0, start), min(len(flags), end)):
        if flags[idx]:
            if run_start is None:
                run_start = idx
            continue
        if run_start is not None:
            runs.append((run_start, idx))
            run_start = None
    if run_start is not None:
        runs.append((run_start, min(len(flags), end)))
    return runs


def detect_effective_plot_right(
    roi_w: int,
    roi_h: int,
    line_color_count: List[int],
    white_label_count: List[int],
    dark_count: List[int],
) -> RightBoundaryDetectionResult:
    line_px_min = 2
    white_px_min = max(2, roi_h // 150)
    dark_ratio_min = 0.92
    spacer_line_px_max = 1
    spacer_white_px_max = 1
    min_label_run_width_px = max(4, roi_w // 160)
    min_spacer_run_width_px = max(8, roi_w // 60)
    min_effective_plot_width_px = max(200, int(roi_w * 0.55))

    search_start = int(roi_w * 0.55)
    dark_ratio = [count / float(roi_h) for count in dark_count]
    line_present = [count >= line_px_min for count in line_color_count]
    white_present = [count >= white_px_min for count in white_label_count]
    spacer_candidate = [
        dark_ratio[x] >= dark_ratio_min
        and line_color_count[x] <= spacer_line_px_max
        and white_label_count[x] <= spacer_white_px_max
        for x in range(roi_w)
    ]

    rightmost_line_x: Optional[int] = None
    for x in range(roi_w - 1, -1, -1):
        if line_present[x]:
            rightmost_line_x = x
            break

    label_run: Optional[Tuple[int, int]] = None
    for run_start, run_end in reversed(true_runs(white_present, start=search_start)):
        if (run_end - run_start) >= min_label_run_width_px:
            label_run = (run_start, run_end)
            break

    spacer_run: Optional[Tuple[int, int]] = None
    if label_run is not None:
        for run_start, run_end in reversed(true_runs(spacer_candidate, start=search_start, end=label_run[0])):
            if (run_end - run_start) >= min_spacer_run_width_px:
                spacer_run = (run_start, run_end)
                break

    if (
        label_run is not None
        and spacer_run is not None
        and (rightmost_line_x is not None and rightmost_line_x < spacer_run[0])
        and spacer_run[0] >= min_effective_plot_width_px
    ):
        return RightBoundaryDetectionResult(
            effective_plot_right_x=spacer_run[0],
            source="spacer",
            label_run_start=label_run[0],
            label_run_end=label_run[1],
            spacer_run_start=spacer_run[0],
            spacer_run_end=spacer_run[1],
            rightmost_line_x=rightmost_line_x,
        )

    if rightmost_line_x is not None:
        fallback_x = rightmost_line_x + 1
        if (
            fallback_x >= min_effective_plot_width_px
            and (roi_w - fallback_x) >= min_spacer_run_width_px
        ):
            return RightBoundaryDetectionResult(
                effective_plot_right_x=fallback_x,
                source="line_fallback",
                label_run_start=label_run[0] if label_run else None,
                label_run_end=label_run[1] if label_run else None,
                spacer_run_start=spacer_run[0] if spacer_run else None,
                spacer_run_end=spacer_run[1] if spacer_run else None,
                rightmost_line_x=rightmost_line_x,
            )

    return RightBoundaryDetectionResult(
        effective_plot_right_x=roi_w,
        source="roi_fallback",
        label_run_start=label_run[0] if label_run else None,
        label_run_end=label_run[1] if label_run else None,
        spacer_run_start=spacer_run[0] if spacer_run else None,
        spacer_run_end=spacer_run[1] if spacer_run else None,
        rightmost_line_x=rightmost_line_x,
    )


def inspect_quadrant(image_path: str, name: str, rect: Tuple[int, int, int, int]) -> QuadrantStats:
    crop = inner_plot_rect(rect)
    short_pixels = 0
    long_pixels = 0
    bullish_pixels = 0
    bearish_pixels = 0

    for _, _, r, g, b in pixel_stream_for_crop(image_path, crop):
        hue, sat, val = rgb_to_hsv(r, g, b)
        masks = classify_color_masks(hue, sat, val)
        short_pixels += int(masks["short"])
        long_pixels += int(masks["long"])
        bullish_pixels += int(masks["bullish"])
        bearish_pixels += int(masks["bearish"])

    recognized = short_pixels >= 1200 and long_pixels >= 1200
    return QuadrantStats(
        name=name,
        short_pixels=short_pixels,
        long_pixels=long_pixels,
        bullish_pixels=bullish_pixels,
        bearish_pixels=bearish_pixels,
        recognized=recognized,
    )


def probe_layout(image_path: str) -> Dict[str, object]:
    width, height = image_size(image_path)
    rects = quadrant_rects(width, height)
    results = {}
    recognized_all = True

    for name in ("daily", "1h", "15m", "5m"):
        stats = inspect_quadrant(image_path, name, rects[name])
        results[name] = asdict(stats)
        recognized_all = recognized_all and stats.recognized

    return {
        "image_size": {"width": width, "height": height},
        "quadrants": results,
        "recognized_all": recognized_all,
    }


def median(values: List[int]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def iqr(values: List[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    q1_idx = int((n - 1) * 0.25)
    q3_idx = int((n - 1) * 0.75)
    return float(ordered[q3_idx] - ordered[q1_idx])


def linear_regression_slope(points: List[Tuple[int, float]]) -> float:
    if len(points) < 2:
        return 0.0

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    numer = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return numer / denom


def extract_5m_metrics(image_path: str) -> Tuple[Optional[Metrics], str]:
    width, height = image_size(image_path)
    rect = quadrant_rects(width, height)["5m"]
    crop = inner_plot_rect(rect)
    _, _, roi_w, roi_h = crop

    short_cols_all: Dict[int, List[int]] = {}
    long_cols_all: Dict[int, List[int]] = {}
    short_pixels_total = 0
    long_pixels_total = 0
    line_color_count = [0] * roi_w
    white_label_count = [0] * roi_w
    dark_count = [0] * roi_w

    for x, y, r, g, b in pixel_stream_for_crop(image_path, crop):
        hue, sat, val = rgb_to_hsv(r, g, b)
        masks = classify_color_masks(hue, sat, val)
        if masks["short"]:
            short_pixels_total += 1
            short_cols_all.setdefault(x, []).append(y)
        if masks["long"]:
            long_pixels_total += 1
            long_cols_all.setdefault(x, []).append(y)

        if masks["short"] or masks["long"] or masks["bullish"]:
            line_color_count[x] += 1
        if is_white_label_pixel(hue, sat, val):
            white_label_count[x] += 1
        if is_dark_bg_pixel(hue, sat, val):
            dark_count[x] += 1

    try:
        boundary = detect_effective_plot_right(
            roi_w=roi_w,
            roi_h=roi_h,
            line_color_count=line_color_count,
            white_label_count=white_label_count,
            dark_count=dark_count,
        )
    except Exception:  # pylint: disable=broad-except
        boundary = RightBoundaryDetectionResult(
            effective_plot_right_x=roi_w,
            source="roi_fallback",
            label_run_start=None,
            label_run_end=None,
            spacer_run_start=None,
            spacer_run_end=None,
            rightmost_line_x=None,
        )

    effective_plot_right_x = clamp(boundary.effective_plot_right_x, 1, roi_w)
    effective_plot_right_x = int(effective_plot_right_x)
    effective_plot_width = effective_plot_right_x
    window_width = max(20, int(effective_plot_width * 0.20))
    right_start = max(0, effective_plot_right_x - window_width)
    analysis_window_end_x = effective_plot_right_x

    short_points = []
    long_points = []
    short_spreads = []
    long_spreads = []
    short_pixels_right = 0
    long_pixels_right = 0

    for x in sorted(short_cols_all):
        if not (right_start <= x < analysis_window_end_x):
            continue
        ys = short_cols_all[x]
        short_points.append((x, median(ys)))
        short_spreads.append(iqr(ys))
        short_pixels_right += len(ys)
    for x in sorted(long_cols_all):
        if not (right_start <= x < analysis_window_end_x):
            continue
        ys = long_cols_all[x]
        long_points.append((x, median(ys)))
        long_spreads.append(iqr(ys))
        long_pixels_right += len(ys)

    if short_pixels_total < 1000 or long_pixels_total < 1000:
        return None, "GMMA色抽出が不足（短期/長期ピクセル不足）"
    if len(short_points) < 20 or len(long_points) < 20:
        return None, "右端20%でGMMA代表線が不足（列数不足）"

    short_slope = linear_regression_slope(short_points) / roi_h
    long_slope = linear_regression_slope(long_points) / roi_h
    short_mean_y = sum(y for _, y in short_points) / len(short_points)
    long_mean_y = sum(y for _, y in long_points) / len(long_points)
    group_gap = (long_mean_y - short_mean_y) / roi_h
    short_spread = (
        (sum(short_spreads) / len(short_spreads)) / roi_h if short_spreads else 0.0
    )
    long_spread = (
        (sum(long_spreads) / len(long_spreads)) / roi_h if long_spreads else 0.0
    )

    metrics = Metrics(
        short_slope=short_slope,
        long_slope=long_slope,
        group_gap=group_gap,
        short_spread=short_spread,
        long_spread=long_spread,
        short_mean_y=short_mean_y,
        long_mean_y=long_mean_y,
        short_columns=len(short_points),
        long_columns=len(long_points),
        short_pixels_right=short_pixels_right,
        long_pixels_right=long_pixels_right,
        roi_width=roi_w,
        roi_height=roi_h,
        effective_plot_right_x=effective_plot_right_x,
        effective_plot_width=effective_plot_width,
        analysis_window_start_x=right_start,
        analysis_window_end_x=analysis_window_end_x,
        analysis_window_width=analysis_window_end_x - right_start,
    )
    return metrics, "ok"


def trend_scores(metrics: Metrics) -> Tuple[float, float]:
    # Balanced sensitivity defaults.
    slope_th = 0.0012
    long_slope_th = 0.0005
    gap_th = 0.03
    spread_th = 0.075

    up_slope_score = clamp((-metrics.short_slope - slope_th) / (slope_th * 2.0), 0.0, 1.0)
    down_slope_score = clamp((metrics.short_slope - slope_th) / (slope_th * 2.0), 0.0, 1.0)
    up_gap_score = clamp((metrics.group_gap - gap_th) / (gap_th * 2.0), 0.0, 1.0)
    down_gap_score = clamp((-metrics.group_gap - gap_th) / (gap_th * 2.0), 0.0, 1.0)
    spread_score = clamp((spread_th - metrics.short_spread) / spread_th, 0.0, 1.0)
    up_long_score = clamp((-metrics.long_slope - long_slope_th) / (long_slope_th * 2.0), 0.0, 1.0)
    down_long_score = clamp((metrics.long_slope - long_slope_th) / (long_slope_th * 2.0), 0.0, 1.0)

    up_score = (
        0.35 * up_slope_score
        + 0.30 * up_gap_score
        + 0.20 * spread_score
        + 0.15 * up_long_score
    )
    down_score = (
        0.35 * down_slope_score
        + 0.30 * down_gap_score
        + 0.20 * spread_score
        + 0.15 * down_long_score
    )
    return up_score, down_score


def classify_state(metrics: Metrics, previous_state: Optional[str]) -> Tuple[str, float, str]:
    up_score, down_score = trend_scores(metrics)
    quality = clamp(
        min(metrics.short_columns / 50.0, 1.0) * 0.5
        + min(metrics.long_columns / 50.0, 1.0) * 0.5,
        0.0,
        1.0,
    )

    direction = "neutral"
    if up_score >= 0.55 and up_score > down_score + 0.08:
        direction = "up"
    elif down_score >= 0.55 and down_score > up_score + 0.08:
        direction = "down"

    if direction == "up":
        state = STATE_UP_CONT if previous_state in UP_STATES else STATE_NEW_UP
        confidence = clamp(up_score * quality, 0.0, 1.0)
        note = (
            "短期群上向き/短期群が長期群より上（価格的に高位）/群収束で上昇判定"
            if state == STATE_NEW_UP
            else "上昇条件継続"
        )
        return state, confidence, note

    if direction == "down":
        state = STATE_DOWN_CONT if previous_state in DOWN_STATES else STATE_NEW_DOWN
        confidence = clamp(down_score * quality, 0.0, 1.0)
        note = (
            "短期群下向き/短期群が長期群より下（価格的に低位）/群収束で下落判定"
            if state == STATE_NEW_DOWN
            else "下落条件継続"
        )
        return state, confidence, note

    confidence = clamp((1.0 - max(up_score, down_score) * 0.7) * quality, 0.0, 1.0)
    note = "上昇/下落の優位条件を満たさず"
    return STATE_NEUTRAL, confidence, note


def load_previous_state(state_path: str) -> Optional[str]:
    path = Path(state_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    state = data.get("last_state")
    return state if isinstance(state, str) else None


def save_state(state_path: str, state: str, source_mtime: Optional[float], metrics: Dict[str, float]) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_state": state,
        "updated_at": now_iso(),
        "source_mtime": safe_iso_from_epoch(source_mtime),
        "metrics": metrics,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze_once(image_path: str, state_path: str) -> AnalysisResult:
    ts = now_iso()
    try:
        source_mtime_epoch = os.path.getmtime(image_path)
    except OSError:
        return AnalysisResult(
            ts=ts,
            state=STATE_NEUTRAL,
            confidence=0.0,
            metrics={},
            source_mtime=None,
            note="画像ファイルが見つかりません",
        )

    try:
        metrics, status_note = extract_5m_metrics(image_path)
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() or str(exc)
        return AnalysisResult(
            ts=ts,
            state=STATE_NEUTRAL,
            confidence=0.0,
            metrics={},
            source_mtime=safe_iso_from_epoch(source_mtime_epoch),
            note=f"画像解析失敗: {err}",
        )
    except Exception as exc:  # pylint: disable=broad-except
        return AnalysisResult(
            ts=ts,
            state=STATE_NEUTRAL,
            confidence=0.0,
            metrics={},
            source_mtime=safe_iso_from_epoch(source_mtime_epoch),
            note=f"解析失敗: {exc}",
        )

    if metrics is None:
        result = AnalysisResult(
            ts=ts,
            state=STATE_NEUTRAL,
            confidence=0.0,
            metrics={},
            source_mtime=safe_iso_from_epoch(source_mtime_epoch),
            note=status_note,
        )
        save_state(state_path, result.state, source_mtime_epoch, result.metrics)
        return result

    previous_state = load_previous_state(state_path)
    state, confidence, note = classify_state(metrics, previous_state)
    metrics_map = asdict(metrics)
    save_state(state_path, state, source_mtime_epoch, metrics_map)
    return AnalysisResult(
        ts=ts,
        state=state,
        confidence=round(confidence, 4),
        metrics=metrics_map,
        source_mtime=safe_iso_from_epoch(source_mtime_epoch),
        note=note,
    )
