# XRD Peak 時間分析

兩個主要程式：`app.py` 是 Streamlit 前端；`xrd_backend.py` 是可獨立呼叫的運算後端。

## 使用 uv 環境

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv run --no-project streamlit run app.py
```

若 `.venv` 已存在，直接使用後兩個指令即可，不需重建。

## 操作

1. 選擇本機 `raw data/*/all.xlsx` 或 `all_data.xlsx`，也可上傳 Excel 並選工作表。
2. 輸入 Q list，例如 `0.23, 0.34, 0.56`，設定 Q 容許誤差與 peak 判定門檻。
3. 按「開始分析」，得到秒數 × Q 的表格：`v` 有 peak、`x` 未偵測到、`無資料` 超出量測範圍。
4. 選擇必須有／沒有 peak 的 Q，可要求全部或任一有 peak，並限制秒數範圍。
5. 檢視符合時間的圖譜（橘色區間是目標附近，紅點是合格 peak），下載 CSV 或 Excel。
6. 在頁面最後查看 peak 出現的相關矩陣與熱圖，或下載矩陣 CSV 與互動熱圖 HTML；Excel 也包含「Peak 相關矩陣」工作表。

Excel 包含全部時間、篩選結果、含來源檔名與實際 peak Q 的明細，以及判定參數。工作表有 Excel 自動篩選。

## 輸入格式與時間

第一列為表頭，`Q` 欄是 Q 軸，其他每一欄為一條圖譜的強度，欄名是原始 dat 檔名：

| Q | NKU1_01_0.dat | NKU1_01_149.dat | NKU1_02_0.dat |
|---|---|---|---|
| 0.20 | 0.1 | 0.2 | 0.1 |
| 0.21 | 0.2 | 0.3 | 0.4 |
| 0.22 | 0.1 | 0.2 | 0.2 |

秒數預設 `(段號 - 1) * 150 + 段內秒數`，所以 `01_0=0`、`01_149=149`、`02_0=150`、`02_1=151`。
Q 與時間會排序；重複 Q、重複時間、無法解析的檔名、空白／非數字強度會報錯，避免產生不可靠結果。
彙總檔 `01_0-149.dat` 和含 `1M` 的檔名不會被當作單一秒數。不同樣品需分開分析。
不需要另外上傳 dat，時間從 Excel 的欄名解析。

## Peak 定義

使用 `scipy.signal.find_peaks` 找局部極大值，可先套用二次 Savitzky–Golay 平滑。
突出度（prominence）門檻是下列三項最大值：

- 絕對突出度下限。
- 相對突出度比例 × 該圖譜平滑後強度最大值與最小值之差。
- 雜訊倍數 × `MAD(相鄰原始強度差) / (0.67448975 * sqrt(2))`。

Peak 的 Q 位置落在目標 ± 容許誤差內就標 `v`。先在整條圖譜偵測再匹配 Q，不會把搜尋區間邊界當成 peak。
圖譜本身兩端的點不判定為 peak；多個合格 peak 時，明細記錄突出度最高者。Q 單位需與檔案一致。
預設參數只是起始值，並非已針對樣品校準；需檢查圖譜調整。平滑使用資料點數，非 Q 寬度；Q 間距不均時請留意，必要時設為 1 關閉。
`無資料` 不符合「有 peak」或「沒有 peak」條件。量測範圍內沒有合格 peak 才是 `x`。

## Peak 出現相關性

以全部時間點的 `v=1`、`x=0` 計算每對 Q 的 Pearson 相關係數；對二元資料這就是 φ 係數。
`無資料` 只從涉及該 Q 的配對中排除。若有效共同時間點不足兩個，或任一 Q 在共同時間點中始終相同，該格為空值（相關係數無法定義）。
相關性根據分析時的 peak 判定計算，與後續時間／組合篩選無關。

## 後端獨立使用

```python
from xrd_backend import load_excel, analyze, PeakSettings, filter_presence, display_table

spectra = load_excel('raw data/NKU111/all_data.xlsx')
result = analyze(spectra, [0.23, 0.34, 0.56], PeakSettings(tolerance=0.01))
selected = filter_presence(result.presence, required=[0.23, 0.34], excluded=[0.56])
print(display_table(selected))
```

## 驗證

```bash
uv run --no-project python -m unittest discover -s tests -v
```
