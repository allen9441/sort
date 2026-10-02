"""Run with: uv run --no-project streamlit run app.py"""
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from xrd_backend import (MIN_ANALYSIS_Q, PeakSettings, analyze, display_table, export_excel,
                         filter_presence, load_excel, parse_q_values,
                         presence_counts)


st.set_page_config(page_title='XRD Peak 時間分析', layout='wide')
st.title('XRD Peak 時間分析')
st.caption(f'僅分析 Q ≥ {MIN_ANALYSIS_Q:g} · v = 有 peak · x = 未偵測到 peak · 無資料 = 目標 Q 超出分析範圍')


@st.cache_data(show_spinner=False, max_entries=3)
def read_spectra(content, sheet, block_size):
    return load_excel(BytesIO(content), sheet_name=sheet, block_size=block_size)


@st.cache_data(show_spinner=False, max_entries=3)
def sheet_names(content):
    with pd.ExcelFile(BytesIO(content), engine='openpyxl') as workbook:
        return workbook.sheet_names


with st.sidebar:
    st.header('資料來源')
    source = st.radio('選檔方式', ['本機 raw data', '上傳 Excel'])
    content = None
    label = ''
    if source == '上傳 Excel':
        uploaded = st.file_uploader('上傳 all.xlsx 或 all_data.xlsx', type=['xlsx'])
        if uploaded is not None:
            content, label = uploaded.getvalue(), uploaded.name
    else:
        root = Path(__file__).resolve().parent
        paths = sorted(set(root.glob('raw data/*/all.xlsx')) |
                       set(root.glob('raw data/*/all_data.xlsx')))
        if paths:
            chosen = st.selectbox('選擇樣品', paths,
                                  format_func=lambda p: str(p.relative_to(root)))
            content, label = chosen.read_bytes(), str(chosen.relative_to(root))
        else:
            st.info('未找到本機資料，請改用上傳 Excel。')
    if content is None:
        st.info('請先選擇資料。')
        st.stop()
    try:
        sheet = st.selectbox('工作表', sheet_names(content))
    except Exception as exc:
        st.error(f'無法讀取 Excel：{exc}')
        st.stop()
    with st.form('analysis_settings'):
        st.subheader('分析參數')
        q_text = st.text_area('Q list（逗號或空白分隔）', '0.23, 0.34, 0.56')
        tolerance = st.number_input('Q 容許誤差 ±', min_value=0.0, value=0.01,
                                    step=0.001, format='%.5f')
        relative = st.number_input('相對突出度（目標 Q ± 容許誤差內強度全距的比例）',
                                   min_value=0.0, max_value=1.0, value=0.03, step=0.01)
        absolute = st.number_input('絕對突出度下限（強度單位）',
                                   min_value=0.0, value=0.0, step=0.0001, format='%.6f')
        noise = st.number_input('雜訊倍數下限', min_value=0.0, value=5.0, step=0.5)
        smoothing = st.selectbox('平滑視窗（點數；1 = 不平滑）', [1, 3, 5, 7, 9, 11, 15, 21], index=2)
        block_size = st.number_input('每段秒數', min_value=1, value=150, step=1)
        submitted = st.form_submit_button('開始分析', type='primary')

# Changing the source invalidates old results. Form settings apply only on submit.
source_key = (label, content, sheet)
if st.session_state.get('source_key') != source_key:
    st.session_state.pop('analysis_bundle', None)
    st.session_state.source_key = source_key

if submitted:
    st.session_state.pop('analysis_bundle', None)
    try:
        targets = parse_q_values(q_text)
        settings = PeakSettings(tolerance, relative, absolute, noise, int(smoothing))
        with st.spinner('讀取圖譜並分析 peaks…'):
            spectra = read_spectra(content, sheet, int(block_size))
            result = analyze(spectra, targets, settings)
        st.session_state.analysis_bundle = (spectra, result, settings, int(block_size))
    except Exception as exc:
        st.error(f'分析失敗：{exc}')

if 'analysis_bundle' not in st.session_state:
    st.info('設定 Q list 後按「開始分析」。Excel 第一列需包含 Q 與各 .dat 檔名欄位。')
    st.stop()

spectra, result, settings, used_block_size = st.session_state.analysis_bundle
targets = list(result.presence.columns)
st.write(f'資料：{label} / {sheet}｜{len(spectra.seconds):,} 個時間點｜'
         f'Q 範圍：{spectra.q[0]:.6g}–{spectra.q[-1]:.6g}')
st.caption(f'目前結果：Q 誤差 ±{settings.tolerance:g}；平滑 {settings.smoothing_window} 點；'
           f'相對突出度 {settings.relative_prominence:g}；絕對突出度 {settings.absolute_prominence:g}；'
           f'雜訊倍數 {settings.noise_multiplier:g}；每段秒數參數 {used_block_size}，換算秒數乘以 3。修改參數後請重新分析。')
outside = [q for q in targets if q < spectra.q[0] or q > spectra.q[-1]]
if outside:
    st.warning(f'以下 Q 超出分析範圍，標記為無資料：{outside}')

st.subheader('依 peak 組合篩選時間')
left, right = st.columns(2)
with left:
    required = st.multiselect('需要有 peak 的 Q', targets)
    mode = st.radio('有 peak 條件', ['全部', '任一'], horizontal=True)
with right:
    excluded = st.multiselect('需要沒有 peak 的 Q（全部符合）', targets)
    min_time, max_time = int(spectra.seconds.min()), int(spectra.seconds.max())
    times = st.slider('秒數範圍', min_time, max_time, (min_time, max_time)) if min_time < max_time else (min_time, max_time)
if set(required) & set(excluded):
    st.warning('同一 Q 同時要求有 peak 與無 peak；請確認條件。')
filtered = filter_presence(result.presence, required, excluded, mode)
filtered = filtered.loc[(filtered.index >= times[0]) & (filtered.index <= times[1])]
shown = display_table(filtered)
st.write(f'符合條件：{len(filtered):,} / {len(result.presence):,} 個時間點')
st.dataframe(shown, width='stretch')
c1, c2 = st.columns(2)
c1.download_button('下載篩選表格 CSV', shown.to_csv().encode('utf-8-sig'),
                   file_name='xrd_peak_filtered.csv', mime='text/csv')
c2.download_button('下載 Excel（全部、篩選、明細、參數）',
                   export_excel(result, filtered, settings), file_name='xrd_peaks.xlsx',
                   mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

st.subheader('圖譜與 peak 標記')
if filtered.empty:
    st.info('沒有符合篩選條件的時間點，請調整條件。')
else:
    second = st.selectbox('檢視秒數', filtered.index.tolist())
    col = int(np.flatnonzero(spectra.seconds == second)[0])
    fig = go.Figure()
    raw = spectra.intensity[:, col]
    processed = result.processed[:, col]
    floor = min(float(raw.min()), float(processed.min()))
    span = max(float(raw.max()), float(processed.max())) - floor
    epsilon = max(span * 1e-6, np.finfo(float).eps * max(abs(floor), 1.0))
    raw_display = raw - floor + epsilon
    processed_display = processed - floor + epsilon
    fig.add_trace(go.Scatter(x=spectra.q, y=raw_display, customdata=raw,
                             name='原始圖譜', line=dict(color='#94a3b8', width=1),
                             hovertemplate='Q=%{x:.5g}<br>原始強度=%{customdata:.6g}'
                                           '<br>圖表強度=%{y:.6g}<extra>%{fullData.name}</extra>'))
    fig.add_trace(go.Scatter(x=spectra.q, y=processed_display, customdata=processed,
                             name='判定使用圖譜',
                             hovertemplate='Q=%{x:.5g}<br>處理後強度=%{customdata:.6g}'
                                           '<br>圖表強度=%{y:.6g}<extra>%{fullData.name}</extra>'))
    peaks = result.peaks[col]
    matched = np.zeros(len(peaks), dtype=bool)
    for q in targets:
        if spectra.q[0] <= q <= spectra.q[-1]:
            matched |= np.abs(spectra.q[peaks] - q) <= settings.tolerance
            fig.add_vrect(x0=max(spectra.q[0], q-settings.tolerance),
                          x1=min(spectra.q[-1], q+settings.tolerance),
                          fillcolor='orange', opacity=0.12, line_width=0)
            fig.add_vline(x=q, line_dash='dot', line_color='orange')
    marked = peaks[matched]
    fig.add_trace(go.Scatter(x=spectra.q[marked],
                             y=processed_display[marked], customdata=processed[marked],
                             mode='markers', marker=dict(color='red', size=10),
                             name='目標附近的 peak',
                             hovertemplate='Q=%{x:.5g}<br>處理後強度=%{customdata:.6g}'
                                           '<br>圖表強度=%{y:.6g}<extra>%{fullData.name}</extra>'))
    fig.update_layout(xaxis_title='Q', yaxis_title='平移後強度 (log)',
                      yaxis_type='log', title=spectra.filenames[col])
    fig.update_xaxes(range=[float(spectra.q[0]), float(spectra.q[-1])])
    st.plotly_chart(fig, width='stretch')
    st.caption(f'本張圖的顯示強度 = 原強度 − 最低強度（{floor:.6g}）+ {epsilon:.3g}；'
               '原始與處理後曲線共用此基準，峰值判定使用未平移的強度。')
    st.dataframe(result.details[result.details['秒數'] == second], hide_index=True, width='stretch')

st.subheader('Peak 出現次數矩陣')
st.caption('對角線為各 peak 的出現次數；列 A、欄 B 的非對角線為 A 與 B 同時出現的次數。'
           '以全部時間點計算，無資料不計入出現次數。色階固定為 0～1200，1200 次以上皆為最深色。')
counts = presence_counts(result.presence)
st.dataframe(counts.style.format('{:d}'), width='stretch')
st.download_button('下載 Peak 出現次數矩陣 CSV',
                   counts.to_csv().encode('utf-8-sig'),
                   file_name='xrd_peak_presence_counts.csv', mime='text/csv')
labels = [f'{q:g}' for q in counts.columns]
values = counts.to_numpy(dtype=np.int64)
texts = [[str(v) for v in row] for row in values]
heatmap = go.Figure(go.Heatmap(z=values, x=labels, y=labels,
                                zmin=0, zmax=1200, colorscale='Blues',
                                colorbar=dict(title='出現次數'),
                                hovertemplate='A Peak Q=%{y}<br>B Peak Q=%{x}'
                                              '<br>出現次數=%{z:d}<extra></extra>'))
for row, y in enumerate(labels):
    for col, x in enumerate(labels):
        value = values[row, col]
        heatmap.add_annotation(x=x, y=y, text=texts[row][col], showarrow=False,
                               font=dict(color='white' if value > 780
                                         else '#17202a'))
heatmap.update_layout(xaxis_title='B：目標 Peak Q', yaxis_title='A：目標 Peak Q',
                      yaxis=dict(autorange=True),
                      height=max(400, 65 * len(labels) + 160))
st.plotly_chart(heatmap, width='stretch')
st.download_button('下載 Peak 出現次數熱圖 HTML',
                   heatmap.to_html(include_plotlyjs=True, full_html=True).encode('utf-8'),
                   file_name='xrd_peak_presence_counts.html', mime='text/html')
with st.expander('判定方法與資料格式'):
    st.markdown(f'''
    - Excel 第一列為欄名：`Q, 樣品_01_0.dat, 樣品_01_1.dat, …`，各欄為對應強度。
    - 秒數 = `3 × [(段號 − 1) × 每段秒數 + 段內秒數]`；每段秒數參數預設為 150，換算後每段 450 秒。
    - Q < {MIN_ANALYSIS_Q:g} 的資料不參與計算或作圖。圖表按最低強度平移後，以 log 縱軸顯示。
    - 先選擇性使用 Savitzky–Golay 平滑，再於保留的圖譜找局部極大值。
    - 每個目標 Q 的突出度門檻取三者最大值：絕對下限、相對比例 × 該 Q ± 容許誤差內的處理後強度全距、雜訊倍數 × 雜訊估計。
      雜訊估計使用保留圖譜的原始相鄰強度差 MAD / (0.67448975 × √2)。
    - Peak 位置與目標 Q 的距離 ≤ 容許誤差即標記 v；區間內沒有合格 peak 則為 x。
    - 圖譜兩端點不判定為 peak；「無資料」不符合有 peak 或無 peak 的篩選条件。
    - 自動判定對參數敏感，請用圖譜檢查並依實驗訊號調整；x 代表此設定下未偵測到 peak。
    ''')
