#!/bin/bash

# 入力ファイルの確認
INPUT_IMG="./capture.png"
OUTPUT_IMG="./roi-right20-dynamic.png"

if [ ! -f "$INPUT_IMG" ]; then
    echo "Error: $INPUT_IMG does not exist."
    exit 1
fi

# Pythonで座標を計算し、結果を変数に格納
# 出力を "width height x y" の形式で受け取る
READ_ROI=$(python3 - <<'PY'
try:
    from gmma_5m_analyzer import image_size, quadrant_rects, inner_plot_rect, extract_5m_metrics
    img = "./capture.png"
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
        # magick crop形式: width height x y
        print(f"{crop_w} {crop_h} {abs_x} {abs_y}")
except Exception as e:
    print(f"ERROR: {str(e)}")
PY
)

# エラーチェック
if [[ $READ_ROI == ERROR* ]]; then
    echo "Failed to calculate ROI: $READ_ROI"
    exit 1
fi

# スペース区切りの値を配列に格納
read -r CROP_W CROP_H ABS_X ABS_Y <<< "$READ_ROI"

# ImageMagickの実行
echo "Executing: magick $INPUT_IMG -crop ${CROP_W}x${CROP_H}+${ABS_X}+${ABS_Y} +repage $OUTPUT_IMG"
magick "$INPUT_IMG" -crop "${CROP_W}x${CROP_H}+${ABS_X}+${ABS_Y}" +repage "$OUTPUT_IMG"

if [ $? -eq 0 ]; then
    echo "Success: $OUTPUT_IMG has been generated."
else
    echo "Error: ImageMagick failed."
    exit 1
fi
