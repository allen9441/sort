"""XRD Excel loading, time conversion and peak detection (no Streamlit dependency)."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter


MIN_ANALYSIS_Q = 0.01


@dataclass
class Spectra:
    q: np.ndarray
    intensity: np.ndarray  # rows: Q; columns: spectra
    filenames: list[str]
    seconds: np.ndarray


@dataclass(frozen=True)
class PeakSettings:
    tolerance: float = 0.01
    relative_prominence: float = 0.03
    absolute_prominence: float = 0.0
    noise_multiplier: float = 5.0
    smoothing_window: int = 5  # 1 disables smoothing; otherwise odd >= 3

    def validate(self):
        for name in ('tolerance', 'relative_prominence', 'absolute_prominence', 'noise_multiplier'):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f'{name} 必須為有限的非負數。')
        if self.relative_prominence > 1:
            raise ValueError('相對突出度需介於 0 與 1。')
        if (not isinstance(self.smoothing_window, int)
                or self.smoothing_window < 1 or self.smoothing_window % 2 != 1):
            raise ValueError('平滑視窗必須是正奇數；1 表示不平滑。')


@dataclass
class Analysis:
    presence: pd.DataFrame  # nullable Boolean, indexed by seconds
    details: pd.DataFrame
    processed: np.ndarray
    peaks: list[np.ndarray]


def parse_q_values(text: str) -> list[float]:
    tokens = re.split(r'[\s,，;；]+', text.strip().strip('[]').strip())
    try:
        values = list(dict.fromkeys(float(t) for t in tokens if t))
    except ValueError as exc:
        raise ValueError('Q list 請輸入數字，並以逗號或空白分隔。') from exc
    if not values or not all(np.isfinite(v) and v >= 0 for v in values):
        raise ValueError('請輸入至少一個有限、非負的 Q 值。')
    return values


def seconds_from_filename(filename: str, block_size: int = 150) -> int:
    """Accept e.g. 01_0.dat, NKU1_02_1.dat; reject averaged/1M files."""
    if not isinstance(block_size, int) or block_size < 1:
        raise ValueError('每段秒數必須是正整數。')
    name = str(filename).replace('\\', '/').rsplit('/', 1)[-1]
    match = re.search(r'(?:^|_)(\d+)_(\d+)(?:\.dat)?$', name, re.IGNORECASE)
    if not match:
        raise ValueError(f'無法由檔名解析時間：{filename}（應為 …_01_0.dat）')
    block, offset = map(int, match.groups())
    if block < 1 or not 0 <= offset < block_size:
        raise ValueError(f'檔名段號或段內秒數超出範圍：{filename}')
    return (block - 1) * block_size + offset


def load_excel(source: str | Path | BytesIO, sheet_name: str | int = 0,
               block_size: int = 150) -> Spectra:
    """First row is header; a Q column and one intensity column per .dat file."""
    frame = pd.read_excel(source, sheet_name=sheet_name, engine='openpyxl')
    frame = frame.dropna(how='all')
    q_columns = [c for c in frame.columns if str(c).strip().casefold() == 'q']
    if len(q_columns) != 1:
        raise ValueError('Excel 需包含唯一的 Q 欄，第一列為欄名。')
    q_column = q_columns[0]
    columns = [c for c in frame.columns if c != q_column]
    if not columns:
        raise ValueError('Excel 沒有圖譜欄位。')
    filenames = [str(c).strip() for c in columns]
    seconds = np.array([seconds_from_filename(c, block_size) for c in filenames])
    if len(np.unique(seconds)) != len(seconds):
        raise ValueError('多個圖譜對應相同秒數；請將不同樣品／量測系列分開分析。')
    q_values = pd.to_numeric(frame[q_column], errors='coerce').to_numpy(dtype=float)
    if not np.isfinite(q_values).all() or np.any(q_values < 0):
        raise ValueError('Q 必須為有限的非負數。')
    numeric = frame.loc[q_values >= MIN_ANALYSIS_Q, [q_column, *columns]].apply(
        pd.to_numeric, errors='coerce')
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        row, col = np.argwhere(~np.isfinite(values))[0]
        raise ValueError(f'資料含空值或非有限數字：欄 {numeric.columns[col]}，Excel 列 {numeric.index[row] + 2}。')
    if len(values) < 3:
        raise ValueError(f'Q ≥ {MIN_ANALYSIS_Q:g} 的區域每條圖譜至少需要 3 個 Q 點。')
    q_order = np.argsort(values[:, 0])
    q = values[q_order, 0]
    if np.any(q < 0) or np.any(np.diff(q) <= 0):
        raise ValueError('Q 必須非負且不可重複。')
    time_order = np.argsort(seconds)
    return Spectra(q, values[q_order, 1:][:, time_order],
                   [filenames[i] for i in time_order], seconds[time_order])


def analyze(spectra: Spectra, q_values: list[float],
            settings: PeakSettings = PeakSettings()) -> Analysis:
    settings.validate()
    targets = np.asarray(q_values, dtype=float)
    if (targets.ndim != 1 or len(targets) == 0 or not np.isfinite(targets).all()
            or np.any(targets < 0) or len(np.unique(targets)) != len(targets)):
        raise ValueError('Q list 需為不重複的有限非負數。')
    if settings.smoothing_window > len(spectra.q):
        raise ValueError('平滑視窗不可大於 Q 資料點數。')
    processed = spectra.intensity.copy()
    if settings.smoothing_window > 1:
        processed = savgol_filter(processed, settings.smoothing_window, 2, axis=0)
    flags = {float(q): [] for q in targets}
    # The local range for each target is inclusive of both Q tolerance limits.
    windows = [(int(np.searchsorted(spectra.q, target - settings.tolerance)),
                int(np.searchsorted(spectra.q, target + settings.tolerance, side='right')))
               for target in targets]
    details, all_peaks = [], []
    for col, (second, filename) in enumerate(zip(spectra.seconds, spectra.filenames)):
        y = processed[:, col]
        # Robust noise estimate from successive raw intensity differences.
        diff = np.diff(spectra.intensity[:, col])
        noise = np.median(np.abs(diff - np.median(diff))) / (0.67448975 * np.sqrt(2))
        base_threshold = max(settings.absolute_prominence, settings.noise_multiplier * noise)
        peaks, props = find_peaks(y, prominence=base_threshold)
        qualified = np.zeros(len(peaks), dtype=bool)
        for target, (start, stop) in zip(targets, windows):
            covered = spectra.q[0] <= target <= spectra.q[-1]
            local_span = np.ptp(y[start:stop]) if stop > start else 0.0
            threshold = max(base_threshold, settings.relative_prominence * local_span)
            candidates = np.flatnonzero(np.abs(spectra.q[peaks] - target) <= settings.tolerance)
            matches = candidates[props['prominences'][candidates] >= threshold]
            found = covered and len(matches) > 0
            if found:
                qualified[matches] = True
            flags[float(target)].append(bool(found) if covered else pd.NA)
            # Record strongest qualifying peak when the tolerance contains multiple peaks.
            best = matches[np.argmax(props['prominences'][matches])] if found else None
            peak = peaks[best] if found else None
            details.append({'秒數': int(second), '檔名': filename, '目標 Q': float(target),
                            '判定': 'v' if found else ('x' if covered else '無資料'),
                            'peak Q': spectra.q[peak] if found else np.nan,
                            'peak 強度（處理後）': y[peak] if found else np.nan,
                            '突出度': props['prominences'][best] if found else np.nan,
                            '局部強度全距': local_span if covered else np.nan,
                            '突出度門檻': threshold if covered else np.nan})
        all_peaks.append(peaks[qualified])
    presence = pd.DataFrame({q: pd.array(v, dtype='boolean') for q, v in flags.items()},
                            index=pd.Index(spectra.seconds, name='秒數'))
    return Analysis(presence, pd.DataFrame(details), processed, all_peaks)


def presence_correlation(table: pd.DataFrame) -> pd.DataFrame:
    """Pearson/phi correlation of peak presence, using pairwise known times.

    A constant or insufficiently observed peak has undefined correlation (NaN).
    """
    binary = table.astype('Float64')
    return binary.corr(method='pearson', min_periods=2)


def filter_presence(table: pd.DataFrame, required=(), excluded=(), mode='全部') -> pd.DataFrame:
    """Unknown cells never satisfy either presence or absence conditions."""
    if mode not in ('全部', '任一'):
        raise ValueError('篩選模式應為「全部」或「任一」。')
    required, excluded = list(required), list(excluded)
    keep = pd.Series(True, index=table.index)
    if required:
        present = table[required].fillna(False)
        keep &= present.all(axis=1) if mode == '全部' else present.any(axis=1)
    if excluded:
        keep &= (~table[excluded]).fillna(False).all(axis=1)
    return table.loc[keep]


def display_table(table: pd.DataFrame) -> pd.DataFrame:
    return table.astype(object).map(lambda v: '無資料' if pd.isna(v) else ('v' if v else 'x'))


def export_excel(result: Analysis, filtered: pd.DataFrame,
                 settings: PeakSettings) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        display_table(result.presence).to_excel(writer, sheet_name='全部時間')
        display_table(filtered).to_excel(writer, sheet_name='篩選結果')
        result.details.to_excel(writer, sheet_name='Peak 明細', index=False)
        presence_correlation(result.presence).to_excel(writer, sheet_name='Peak 相關矩陣')
        pd.DataFrame(list(vars(settings).items()), columns=['參數', '值']).to_excel(
            writer, sheet_name='判定參數', index=False)
        for sheet in writer.book.worksheets:
            sheet.freeze_panes = 'B2'
            sheet.auto_filter.ref = sheet.dimensions
    return buffer.getvalue()
