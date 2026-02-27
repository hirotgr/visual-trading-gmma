#!/usr/bin/env python3
"""Run GMMA 5m analyzer once or as a persistent 5-minute monitor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gmma_5m_analyzer import analyze_once, probe_layout


ALIGN_OFFSET_SECONDS = 10
SOURCE_WAIT_TIMEOUT_SECONDS = 30
SOURCE_WAIT_POLL_SECONDS = 1.0


def append_jsonl(log_path: str, payload: dict) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.write("\n")


def current_source_mtime(image_path: str) -> Optional[float]:
    if not os.path.exists(image_path):
        return None
    return os.path.getmtime(image_path)


def next_aligned_run_ts(now_epoch: float, interval: int = 300, offset: int = 10) -> float:
    # Align to wall-clock boundary: HH:MM where MM%5==0, then add +offset seconds.
    base = (int(now_epoch) // interval) * interval
    candidate = base + offset
    if now_epoch >= candidate:
        candidate += interval
    return float(candidate)


def sleep_until(target_epoch: float) -> None:
    while True:
        remain = target_epoch - time.time()
        if remain <= 0:
            return
        time.sleep(min(remain, 1.0))


def wait_for_source_update(
    image_path: str,
    previous_mtime: Optional[float],
    timeout: int = 30,
    poll: float = 1.0,
) -> bool:
    deadline = time.time() + timeout
    while True:
        mtime = current_source_mtime(image_path)
        if mtime is not None:
            if previous_mtime is None or mtime > previous_mtime:
                return True
        if time.time() >= deadline:
            return False
        time.sleep(min(poll, max(0.05, deadline - time.time())))


def backup_capture_before_sleep(image_path: str) -> None:
    src = Path(image_path)
    if not src.exists() or src.name != "capture.png":
        return

    hhmm = datetime.now().strftime("%H%M")
    renamed = src.with_name(f"capture-{hhmm}.png")
    src.replace(renamed)

    debug_dir = Path("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(renamed), str(debug_dir / renamed.name))


def run_cycle(image_path: str, state_path: str, log_path: str, last_mtime: Optional[float]) -> Optional[float]:
    current_mtime = None
    if os.path.exists(image_path):
        current_mtime = os.path.getmtime(image_path)

    result = analyze_once(image_path=image_path, state_path=state_path)
    payload = {
        "ts": result.ts,
        "state": result.state,
        "confidence": round(result.confidence, 4),
        "metrics": result.metrics,
        "source_mtime": result.source_mtime,
        "note": result.note,
    }

    if current_mtime is not None and last_mtime is not None and current_mtime == last_mtime:
        payload["note"] = f"{payload['note']} / source_mtime未更新"

    append_jsonl(log_path, payload)
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return current_mtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze capture.png with GMMA and report 5m trend every interval."
    )
    parser.add_argument("--image", default="./capture.png", help="Path to capture PNG.")
    parser.add_argument("--log", default="./trend-report.log", help="Path to JSONL log file.")
    parser.add_argument(
        "--state",
        default="./state/gmma-state.json",
        help="Path to state JSON file.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="Loop interval in seconds (default: 300).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle only and exit.",
    )
    parser.add_argument(
        "--probe-layout",
        action="store_true",
        help="Check 4-chart layout and GMMA presence before analysis.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.probe_layout:
        try:
            layout = probe_layout(args.image)
            print(json.dumps({"layout_probe": layout}, ensure_ascii=False, indent=2), flush=True)
        except Exception as exc:  # pylint: disable=broad-except
            print(
                json.dumps(
                    {
                        "layout_probe_error": str(exc),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    if args.once:
        run_cycle(
            image_path=args.image,
            state_path=args.state,
            log_path=args.log,
            last_mtime=None,
        )
        return 0

    last_mtime = current_source_mtime(args.image)

    while True:
        target = next_aligned_run_ts(
            now_epoch=time.time(),
            interval=args.interval_seconds,
            offset=ALIGN_OFFSET_SECONDS,
        )
        # コメントアウトを外すと ./debug/ に capture.png のバックアップを保存
        # backup_capture_before_sleep(args.image)
        sleep_until(target)
        wait_for_source_update(
            image_path=args.image,
            previous_mtime=last_mtime,
            timeout=SOURCE_WAIT_TIMEOUT_SECONDS,
            poll=SOURCE_WAIT_POLL_SECONDS,
        )
        last_mtime = run_cycle(
            image_path=args.image,
            state_path=args.state,
            log_path=args.log,
            last_mtime=last_mtime,
        )


if __name__ == "__main__":
    sys.exit(main())
