#!/bin/bash

set -u
shopt -s nullglob

DEBUG_DIR="./debug"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not available."
    exit 1
fi

if ! command -v magick >/dev/null 2>&1; then
    echo "Error: magick (ImageMagick) is not available."
    exit 1
fi

if [ ! -d "$DEBUG_DIR" ]; then
    echo "Error: $DEBUG_DIR directory does not exist."
    exit 1
fi

INPUT_FILES=("$DEBUG_DIR"/capture-[0-9][0-9][0-9][0-9].png)

if [ ${#INPUT_FILES[@]} -eq 0 ]; then
    echo "Error: no files matched $DEBUG_DIR/capture-HHMM.png"
    exit 1
fi

TOTAL=${#INPUT_FILES[@]}
SUCCESS=0
FAILURE=0
FAILED_FILES=()

for INPUT_IMG in "${INPUT_FILES[@]}"; do
    BASE_NAME=$(basename "$INPUT_IMG")
    HHMM="${BASE_NAME#capture-}"
    HHMM="${HHMM%.png}"
    OUTPUT_IMG="$DEBUG_DIR/roi-right20-dynamic-${HHMM}.png"

    READ_ROI=$(python3 - "$INPUT_IMG" <<'PY'
import sys

try:
    from gmma_5m_analyzer import image_size, quadrant_rects, inner_plot_rect, extract_5m_metrics

    img = sys.argv[1]
    w, h = image_size(img)
    q5 = quadrant_rects(w, h)["5m"]
    rx, ry, rw, rh = inner_plot_rect(q5)
    m, status = extract_5m_metrics(img)

    if m is None:
        print(f"ERROR: {status}")
    else:
        abs_x = rx + m.analysis_window_start_x
        abs_y = ry
        crop_w = m.analysis_window_width
        crop_h = rh
        print(f"{crop_w} {crop_h} {abs_x} {abs_y}")
except Exception as e:
    print(f"ERROR: {str(e)}")
PY
)

    if [[ $READ_ROI == ERROR* ]]; then
        echo "Failed to calculate ROI for $BASE_NAME: $READ_ROI"
        FAILED_FILES+=("$BASE_NAME")
        FAILURE=$((FAILURE + 1))
        continue
    fi

    read -r CROP_W CROP_H ABS_X ABS_Y <<< "$READ_ROI"
    if [ -z "${CROP_W:-}" ] || [ -z "${CROP_H:-}" ] || [ -z "${ABS_X:-}" ] || [ -z "${ABS_Y:-}" ]; then
        echo "Failed to parse ROI for $BASE_NAME: $READ_ROI"
        FAILED_FILES+=("$BASE_NAME")
        FAILURE=$((FAILURE + 1))
        continue
    fi

    echo "Executing: magick $INPUT_IMG -crop ${CROP_W}x${CROP_H}+${ABS_X}+${ABS_Y} +repage $OUTPUT_IMG"
    if magick "$INPUT_IMG" -crop "${CROP_W}x${CROP_H}+${ABS_X}+${ABS_Y}" +repage "$OUTPUT_IMG"; then
        echo "Success: $OUTPUT_IMG has been generated."
        SUCCESS=$((SUCCESS + 1))
    else
        echo "Error: ImageMagick failed for $BASE_NAME."
        FAILED_FILES+=("$BASE_NAME")
        FAILURE=$((FAILURE + 1))
    fi
done

echo
echo "=== Summary ==="
echo "Total files  : $TOTAL"
echo "Succeeded    : $SUCCESS"
echo "Failed       : $FAILURE"

if [ $FAILURE -gt 0 ]; then
    echo "Failed files:"
    for FAILED in "${FAILED_FILES[@]}"; do
        echo "  - $FAILED"
    done
    exit 1
fi

exit 0
