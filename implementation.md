# implementation.md

## 目的

`capture.png` を5分ごとに解析し、右下の5分足チャートのGMMA状態を次の5分類で判定してログ出力する。

- 新規上昇トレンド発生
- 上昇トレンド継続
- トレンドなし、またはトレンド減衰
- 新規下落トレンド発生
- 下落トレンド継続

## 実装ファイル

- `gmma_5m_analyzer.py`
- `run_gmma_report.py`
- `start-gmma-monitor.sh`

## 依存関係

- `python3`
- `magick` (ImageMagick)

画像デコードとピクセル取得は `magick ... txt:-` を使って実施する。OpenCVやPillowには依存しない。

## 全体アーキテクチャ

1. `run_gmma_report.py` が常駐ループを管理する。
2. 各サイクルで `gmma_5m_analyzer.py` の `analyze_once()` を呼ぶ。
3. 解析結果を `trend-report.log` にJSONLで追記する。
4. 前回状態は `state/gmma-state.json` に保存し、新規/継続判定に利用する。

## 画像レイアウト認識

### 4分割の座標定義

`image_size()` で画像サイズを取得し、`quadrant_rects()` で次を固定分割する。

- `daily`: 左上
- `1h`: 右上
- `15m`: 左下
- `5m`: 右下

### チャート内有効領域（ROI）

`inner_plot_rect()` で各象限の内側領域を抽出する。

- 左余白除外: `+6%`
- 上ヘッダ除外: `+8%`
- 幅: `84%`
- 高さ: `82%`

このROIに対して色マスクを適用する。

## 色マスク（HSV）

`classify_color_masks()` でRGB→HSV変換後に分類する。`V < 0.22` は背景として無視する。

- 短期GMMA（ピンク系）: `H 330-350`, `S >= 0.20`
- 長期GMMA（オレンジ系）: `H 20-45`, `S >= 0.25`
- 陽線系（シアン）: `H 165-200`, `S >= 0.20`, `V >= 0.30`（補助計数）
- 陰線系（赤）: `H 345-15` (wrap), `S >= 0.35`, `V >= 0.35`（補助計数）
- 陽線/陰線色は互換性維持のため計数するが、ライン運用では主判定条件に含めない。

### チャートUIノイズの抑制（必須）

- TradingView では、チャートエリアを右クリックし、`Settings > Events` の `Economic News`、`Latest news` などのイベント表示を非表示にする。
- これらのアイコンは画像内で短期群色（ピンク系）などと干渉し、色マスク抽出時のノイズとして扱われ、トレンド判定に影響する可能性がある。
- `Chart Value` は非表示にする。OHLC等の文字列表示が色マスク抽出と干渉する可能性がある。
- `Indicator Title/Input,Value` は非表示にする。インジケータ名や各値の文字列表示がノイズとなる可能性がある。
- Y軸の `Label` は非表示にする。右端近傍の文字表示が代表線抽出に干渉する可能性がある。
- `Price Line` は表示可能（シアン推奨）。ただし短期/長期の色相帯と重ならない設定にする。
  - 短期GMMA: ピンク帯 `H 330-350`
  - 長期GMMA: オレンジ帯 `H 20-45`
  - Price Line: シアン帯 `H 170-200`（目安）
- 他のチャートソフトを使用する場合も、解析対象外のイベント/マーカー/バッジ/グリッド線等の視覚要素は非表示にする。

## 4チャート認識チェック

`probe_layout()` は各象限で次を計数して認識可否を出す。

- `short_pixels`
- `long_pixels`
- `bullish_pixels`
- `bearish_pixels`

1象限の `recognized` 条件は以下（ライン最適化）。

- `short_pixels >= 1200`
- `long_pixels >= 1200`

`bullish_pixels` と `bearish_pixels` は互換性維持のため計数を残すが、`recognized` 判定には使わない。

全象限が真なら `recognized_all = true`。

## 5分足メトリクス抽出

`extract_5m_metrics()` では右下象限のみを対象にする。

1. 5分足ROIを取得。
2. ROI全幅で短期/長期GMMA列データを収集しつつ、黒背景（Right Bar Spacing）/白文字（Y軸ラベル）/線色の列プロファイルを作る。
3. 列プロファイルから、実チャート描画領域の右端（`effective_plot_right_x`）を動的検出する。
4. 実効プロット幅の右端20%を評価区間にする（検出失敗時はROI右端を使う従来挙動へフォールバック）。
5. 評価区間の各x列で短期/長期マスクの `y` を集める。
6. 列代表値として `median(y)` を採用する。
7. 群内ばらつきとして `IQR(y)` を採用する。
8. 列代表列に線形回帰を当て、傾きを求める。

### 短期群代表線・長期群代表線の決定方法

- まずROI全幅で、短期色マスクに該当したピクセルの `y` を `short_cols_all[x]` に、長期色マスクに該当したピクセルの `y` を `long_cols_all[x]` に、列（x）単位で収集する。
- 同時に、各列について「線色（短期/長期/シアン価格線候補）」「白文字（Y軸ラベル候補）」「黒背景（Right Bar Spacing候補）」の画素数を計数し、列プロファイルを作る。
- 列プロファイルから `effective_plot_right_x`（実効プロット右端）を検出し、評価区間は `analysis_window_start_x <= x < analysis_window_end_x` とする。
- 各列の代表値は `median(y)` を使う。中央値は外れ値（他インジケータ、ノイズ、交差点付近の誤抽出）の影響を受けにくいため。
- 各列の群内ばらつきは `IQR(y)` を使う。四分位範囲により、線束の収束/拡散の程度を頑健に表せるため。
- `short_points = [(x, median_y)]` と `long_points = [(x, median_y)]` が、実装上の「短期群代表線」「長期群代表線」。
- 本実装は12本のEMAを個別追跡しない。短期群/長期群をそれぞれ1本の代表ライン（中央値ライン）に圧縮して判定している。

算出値:

- `short_slope`: 短期群代表線の傾き（線形回帰、`/roi_h` 正規化）。画像座標では `y` が下向きに増えるため、`負` は上向き（上昇寄り）、`正` は下向き（下落寄り）。
- `long_slope`: 長期群代表線の傾き（定義は `short_slope` と同様）。
- `short_mean_y`: 評価区間（実効プロット幅の右端20%）における短期群代表線の平均 `y`。値が小さいほど画面上側（価格高位）。
- `long_mean_y`: 評価区間（実効プロット幅の右端20%）における長期群代表線の平均 `y`。値が小さいほど画面上側。
- `group_gap = (long_mean_y - short_mean_y) / roi_h`: 群間の上下位置差。`正` なら短期群が長期群より上、`負` なら短期群が長期群より下。
- `short_spread`: 短期群の群内拡散度（各列IQRの平均を `roi_h` で正規化）。小さいほど収束。
- `long_spread`: 長期群の群内拡散度（定義は `short_spread` と同様）。
- `short_columns`: 評価区間（実効プロット幅の右端20%）で短期群代表点を構成できた列数（データ品質指標）。
- `long_columns`: 評価区間（実効プロット幅の右端20%）で長期群代表点を構成できた列数（データ品質指標）。
- `short_pixels_right`: 評価区間で検出された短期群ピクセル総数（抽出量）。
- `long_pixels_right`: 評価区間で検出された長期群ピクセル総数（抽出量）。
- `roi_width`: 解析ROI幅（正規化や列数解釈の基準）。
- `roi_height`: 解析ROI高さ（傾き/距離正規化の基準）。
- `effective_plot_right_x`: 解析ROI内で検出した実効プロット右端（排他的、ローカル座標）。
- `effective_plot_width`: 実効プロット幅（`effective_plot_right_x` と同値。ROI左端=0基準）。
- `analysis_window_start_x`: 評価区間の開始x（ローカル座標）。
- `analysis_window_end_x`: 評価区間の終了x（排他的、ローカル座標）。
- `analysis_window_width`: 評価区間の幅（列数解釈の補助）。

フェイル条件:

- `short_pixels_total < 1000` または `long_pixels_total < 1000`
- `short_points < 20` または `long_points < 20`

フェイル時は `metrics={}`、`state=トレンドなし、またはトレンド減衰`、`confidence=0.0`。

## 判定ロジック（バランス感度）

`trend_scores()` で上昇/下落スコアを計算する。

しきい値:

- `slope_th = 0.0012`
- `long_slope_th = 0.0005`
- `gap_th = 0.03`
- `spread_th = 0.075`

重み:

- `slope`: 0.35
- `gap`: 0.30
- `spread`: 0.20
- `long_slope`: 0.15

### しきい値変数の意味

- `slope_th`: 短期群傾きが有意に上向き/下向きかを判定する基準。
- `long_slope_th`: 長期群傾きの補助判定基準。短期群より遅い変化を見るため小さめに設定。
- `gap_th`: 短期群と長期群の上下分離（位置差）が有意かを判定する基準。
- `spread_th`: 短期群が「収束している」とみなすための拡散上限。

### 中間スコアの意味

- `up_slope_score` / `down_slope_score`: `short_slope` が上昇側/下落側のしきい値をどれだけ超えているかを0..1で表現。
- `up_gap_score` / `down_gap_score`: `group_gap` が上昇側（短期群が上）/下落側（短期群が下）にどれだけ離れているかを0..1で表現。
- `spread_score`: `short_spread` が小さい（収束）ほど高くなる0..1スコア。
- `up_long_score` / `down_long_score`: `long_slope` を使った補助トレンドスコア（長期群の向き）。

### 合成スコア

- `up_score = 0.35 * up_slope_score + 0.30 * up_gap_score + 0.20 * spread_score + 0.15 * up_long_score`
- `down_score = 0.35 * down_slope_score + 0.30 * down_gap_score + 0.20 * spread_score + 0.15 * down_long_score`

短期群の向きと群間位置関係を主軸にしつつ、群収束と長期群傾きで補強する重み配分。

方向決定:

- 上昇: `up_score >= 0.55` かつ `up_score > down_score + 0.08`
- 下落: `down_score >= 0.55` かつ `down_score > up_score + 0.08`
- それ以外: 中立

### 方向決定ロジック

- `>= 0.55` は絶対的な強さ条件。十分な証拠がない場合は方向を確定しない。
- `+ 0.08` は相対優位条件。上昇/下落が拮抗している局面での誤判定を抑える。

品質係数:

- `quality = min(short_columns/50,1)*0.5 + min(long_columns/50,1)*0.5`

最終信頼度:

- 上昇/下落時: `score * quality` を `0..1` にクランプ
- 中立時: `(1 - max_score*0.7) * quality` を `0..1` にクランプ

### `quality` と `confidence`

- `quality` は、代表線を構成できた列数に基づく信頼補正。列が少ない（抽出が弱い）ほど信頼度を下げる。
- 上昇/下落時は、方向スコア（`up_score` または `down_score`）に `quality` を掛けて最終 `confidence` とする。
- 中立時は、強い方向スコアが出ていないほど `confidence` を高くする式を使う。方向性が弱い状況を「中立として確からしい」と扱うため。

### コード対応箇所

- メトリクス抽出: `extract_5m_metrics()`
- スコア計算: `trend_scores()`
- 方向決定/信頼度/状態遷移: `classify_state()`

## 新規/継続の状態遷移

`classify_state(metrics, previous_state)` で前回状態を参照する。

- 上昇方向かつ前回が非上昇: `新規上昇トレンド発生`
- 上昇方向かつ前回が上昇系: `上昇トレンド継続`
- 下落方向かつ前回が非下落: `新規下落トレンド発生`
- 下落方向かつ前回が下落系: `下落トレンド継続`
- 方向なし: `トレンドなし、またはトレンド減衰`

## 状態ファイル

保存先: `state/gmma-state.json`

保存内容:

- `last_state`
- `updated_at` (UTC ISO8601)
- `source_mtime` (UTC ISO8601)
- `metrics`

初回実行時はファイル未存在でも動作し、実行後に自動生成される。

## 常駐実行ループ

`run_gmma_report.py` の `main()` が以下を実行する。

1. オプションで `--probe-layout` 実行。
2. `--once` 指定時のみ `run_cycle()` を即時1回実行して終了。
3. `--once` なしの場合、初回は次の5分境界+10秒まで待機。
4. 目標時刻到達後、`capture.png` の更新を最大30秒待機（1秒ポーリング）。
5. `run_cycle()` 実行後、次サイクルも同様に「次の5分境界+10秒」を再計算して待機。

`--interval-seconds` 既定値は `300`。

補助関数:

- `next_aligned_run_ts(now_epoch, interval=300, offset=10)`
- `sleep_until(target_epoch)`
- `wait_for_source_update(image_path, previous_mtime, timeout=30, poll=1.0)`

## ログ出力

`trend-report.log` に1行1JSONで追記する。1レコードの形式:

- `ts`
- `state`
- `confidence`
- `metrics`
- `source_mtime`
- `note`

同一画像の再解析で `source_mtime` が変わらない場合、`note` に `source_mtime未更新` を追記する。
更新待機タイムアウト（30秒）後も未更新なら、そのサイクルは解析を実行しつつこの `note` が付与される。

## エラーハンドリング

- 画像不在: 中立 + `confidence=0.0` + `note="画像ファイルが見つかりません"`
- ImageMagick実行失敗: 中立 + `confidence=0.0` + `note="画像解析失敗: ..."`
- 想定外例外: 中立 + `confidence=0.0` + `note="解析失敗: ..."`

## ROI切り出し変更（Right Bar Spacing利用）

今回の変更では、4分割レイアウトと `inner_plot_rect()` による固定比率ROI（base ROI）は維持しつつ、5分足メトリクス抽出時の評価区間（右端20%）だけを動的化した。

- 維持したもの:
  - 4分割（右下を5分足として使用）
  - `inner_plot_rect()` の固定比率ROI
  - `roi_h` 基準の正規化（傾き、gap、spread）
- 変更したもの:
  - `right_start = int(roi_w * 0.80)` の固定右端20%を廃止
  - ROI全幅の列プロファイル（線色/白文字/黒背景）から `effective_plot_right_x` を検出
  - `effective_plot_right_x` を基準に `analysis_window_start_x <= x < analysis_window_end_x` を決定
- 目的:
  - Y軸ラベル（白文字）の混入を避ける
  - `Right Bar Spacing` の黒スペースを使って、実チャート領域の右端を安定に推定する
- フォールバック:
  - `spacer`（黒スペース + ラベル）構造を検出できない場合は、線色の右端推定またはROI右端を使う従来挙動に戻す

## ROI切り出し確認手順（固定ROI + 動的 right20）

`capture.png` を使って、base ROI と動的 `right20` の切り出し結果を確認する手順。

### 1. 解析メトリクスから動的 right20 の座標を確認する

以下のワンライナーで、5分足ROI内の動的右端と評価区間のローカル座標を確認する。

```bash
python3 - <<'PY'
from gmma_5m_analyzer import extract_5m_metrics
m, status = extract_5m_metrics("./capture.png")
print("status =", status)
if m is None:
    raise SystemExit("metrics is None")
print("effective_plot_right_x =", m.effective_plot_right_x)
print("effective_plot_width   =", m.effective_plot_width)
print("analysis_window_start_x=", m.analysis_window_start_x)
print("analysis_window_end_x  =", m.analysis_window_end_x)
print("analysis_window_width  =", m.analysis_window_width)
print("roi_width, roi_height  =", m.roi_width, m.roi_height)
PY
```

確認ポイント:

- `effective_plot_right_x < roi_width` なら、ROI右端より左に実効プロット右端を検出できている（Right Bar Spacingを利用できている可能性が高い）
- `analysis_window_end_x <= effective_plot_right_x` を満たす

### 2. 固定ROI（base ROI）を切り出して確認する

現在の `capture.png` のサイズに対する固定ROIを確認する。
`capture.png` のサイズやレイアウトが変わる場合は、次の手順3のスクリプトで座標を算出する。

```bash
magick ./capture.png -crop 798x572+1007+752 +repage ./roi.png
```

### 3. 動的 right20 の絶対座標を算出する

`analysis_window_*` はROIローカル座標なので、画像全体座標へ変換して `magick -crop` に渡す。


```bash
python3 - <<'PY'
from gmma_5m_analyzer import image_size, quadrant_rects, inner_plot_rect, extract_5m_metrics

img = "./capture.png"
w, h = image_size(img)
q5 = quadrant_rects(w, h)["5m"]  # 右下=5m
rx, ry, rw, rh = inner_plot_rect(q5)
m, status = extract_5m_metrics(img)
if m is None:
    raise SystemExit(f"metrics is None: {status}")

abs_x = rx + m.analysis_window_start_x
abs_y = ry
crop_w = m.analysis_window_width
crop_h = rh

print("BASE_ROI =", (rx, ry, rw, rh))
print("DYNAMIC_RIGHT20 =", (abs_x, abs_y, crop_w, crop_h))
print(f"magick ./capture.png -crop {crop_w}x{crop_h}+{abs_x}+{abs_y} +repage ./roi-right20-dynamic.png")
PY
```


### 4. 動的 right20 を切り出す

手順3の出力コマンドを実行して、動的 `right20` を画像として確認する。

例（`capture.png` の確認時点の値）:

```bash
magick ./capture.png -crop 141x572+1575+752 +repage ./roi-right20-dynamic.png
```


### 5. 目視確認のチェック項目

- `roi-right20-dynamic.png` にY軸ラベル（白文字）が混入していない
- GMMAライン（紫/オレンジ）が十分に含まれている
- 価格ライン（シアン）が表示されている場合、評価区間内に含まれている
- 以前の固定 `right20` より、実チャート線の列数（`short_columns`, `long_columns`）が大幅に減っていない


### 6. ROI目視確認用画像ファイルの生成スクリプト

- `roi.sh` を実行すれば `capture.png` から `roi-right20-dynamic.png` を生成し、人間が目視可能
- `run_gmma_report.py` で `# backup_capture_before_sleep(args.image)` のコメントアウトを外すと `capture.png` が `./debug/capture-HHMM.png` に名前変更の上、バックアップされる
- `debug-roi.sh` を実行すると `./debug/capture-HHMM.png` に対応する `./debug/roi-right20-dynamic-HHMM.png` が生成される


## 実行コマンド

単発実行（認識チェック込み）:

```bash
python3 run_gmma_report.py --image ./capture.png --log ./trend-report.log --state ./state/gmma-state.json --probe-layout --once
```

常駐実行（5分ごと）:

```bash
python3 run_gmma_report.py --image ./capture.png --log ./trend-report.log --state ./state/gmma-state.json --interval-seconds 300
```

ラッパースクリプト:

```bash
./start-gmma-monitor.sh
```
