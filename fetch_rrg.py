#!/usr/bin/env python3
"""
米国株テーマトラッカー用 データ生成スクリプト（バスケット版）
------------------------------------------------------------
各テーマを「個別株の等加重バスケット」として合成し、
  テーマ指数 = 構成銘柄を初日=1に正規化して平均
  x = 長期(60営業日)の対SPY超過リターン(対数, %)
  y = 5日リターン(%)
を直近21営業日ぶん計算して data.json に書き出します。
生成された data.json を、ビューアの「CSV / JSON 読込」から読み込んでください。

使い方:
    pip install yfinance
    python fetch_rrg.py
"""

import json
import os
import numpy as np
import yfinance as yf

BENCH = "SPY"      # ベンチマーク(S&P500)
LOOKBACK = 60      # 長期ルックバック(営業日)
POINTS = 21        # 最新 + 過去20日
WITH_CAP = os.getenv("WITH_CAP", "true").strip().lower() == "true"   # 時価総額(バブルサイズ用)も取得。環境変数 WITH_CAP=false で無効化(高速)。

# テーマ = 等加重の個別株バスケット。ティッカーは 2026 年時点（例: Block=XYZ, Fiserv=FISV）。
# ビューアの THEME_DEFS と同一。自由に編集可。
THEME_DEFS = [
    {"name": "半導体", "color": "pink", "tickers": ["NVDA", "AVGO", "AMD", "TSM", "QCOM", "MU", "LRCX", "AMAT", "KLAC", "ARM", "MRVL"]},
    {"name": "ソフトウェア", "color": "pink", "tickers": ["MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTU", "PANW", "CRWD", "SNOW", "DDOG"]},
    {"name": "データセンター", "color": "pink", "tickers": ["EQIX", "DLR", "VRT", "ANET", "SMCI", "CRDO", "CIEN"]},
    {"name": "AIプロバイダ", "color": "pink", "tickers": ["NVDA", "PLTR", "SNOW", "AI", "GOOGL", "META"]},
    {"name": "エッジAI", "color": "pink", "tickers": ["QCOM", "ARM", "AVGO", "LSCC", "MCHP", "SYNA"]},
    {"name": "医療", "color": "pink", "tickers": ["UNH", "ELV", "CI", "HUM", "CVS", "CNC", "MOH"]},
    {"name": "フィンテック", "color": "blue", "tickers": ["PYPL", "XYZ", "COIN", "SOFI", "AFRM", "HOOD", "NU", "TOST"]},
    {"name": "決済", "color": "blue", "tickers": ["V", "MA", "AXP", "PYPL", "FISV", "GPN"]},
    {"name": "資産運用", "color": "blue", "tickers": ["BLK", "BX", "KKR", "APO", "SCHW", "MS", "GS"]},
    {"name": "生活必需品", "color": "blue", "tickers": ["PG", "KO", "PEP", "COST", "WMT", "CL", "MDLZ", "KMB"]},
    {"name": "公益", "color": "blue", "tickers": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "SRE"]},
    {"name": "保険", "color": "green", "tickers": ["PGR", "TRV", "ALL", "CB", "AIG", "MET", "PRU", "AFL"]},
    {"name": "医薬品", "color": "green", "tickers": ["LLY", "MRK", "PFE", "ABBV", "BMY", "AMGN", "GILD", "JNJ"]},
    {"name": "ロボティクス", "color": "green", "tickers": ["ISRG", "ROK", "TER", "ZBRA", "EMR", "PATH"]},
    {"name": "防衛・宇宙", "color": "green", "tickers": ["LMT", "RTX", "NOC", "GD", "LHX", "BA", "HII", "LDOS"]},
    {"name": "物流", "color": "green", "tickers": ["UPS", "FDX", "ODFL", "JBHT", "CHRW", "XPO", "UNP", "CSX", "NSC"]},
    {"name": "資本財", "color": "green", "tickers": ["CAT", "DE", "HON", "GE", "MMM", "ETN", "ITW", "PH"]},
    {"name": "設備インフラ", "color": "green", "tickers": ["PWR", "ETN", "VRT", "PH", "EMR", "URI"]},
    {"name": "一般消費財", "color": "yellow", "tickers": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG"]},
    {"name": "通販", "color": "yellow", "tickers": ["AMZN", "SHOP", "MELI", "EBAY", "ETSY", "CHWY", "W"]},
    {"name": "レストラン", "color": "yellow", "tickers": ["MCD", "SBUX", "CMG", "YUM", "DRI", "QSR", "WING", "CAVA"]},
    {"name": "エンタメ", "color": "yellow", "tickers": ["NFLX", "DIS", "WBD", "SPOT", "EA", "TTWO", "RBLX", "LYV"]},
    {"name": "自動車", "color": "yellow", "tickers": ["TSLA", "GM", "F", "RIVN", "LCID", "TM"]},
    {"name": "中国", "color": "yellow", "tickers": ["BABA", "PDD", "JD", "BIDU", "NTES", "LI", "NIO", "BILI"]},
    {"name": "エネルギー", "color": "red", "tickers": ["XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "VLO", "OXY"]},
    {"name": "天然ガス", "color": "red", "tickers": ["EQT", "LNG", "AR", "RRC", "CTRA", "KMI", "WMB", "OKE"]},
    {"name": "素材", "color": "purple", "tickers": ["FCX", "NUE", "STLD", "NEM", "AA", "X", "CLF", "MP"]},
    {"name": "貴金属", "color": "purple", "tickers": ["NEM", "GOLD", "AEM", "WPM", "FNV", "RGLD"]},
    {"name": "バッテリー", "color": "purple", "tickers": ["ALB", "QS", "ENVX", "PLUG"]},
    {"name": "不動産", "color": "gray", "tickers": ["PLD", "AMT", "EQIX", "SPG", "O", "WELL", "PSA", "CCI"]},
]


def fetch_caps(tickers):
    """各ティッカーの時価総額(USD)を取得。取れないものは None。yfinance の
    fast_info を使用（銘柄数が多いと時間がかかり、たまに欠損します）。"""
    caps = {}
    try:
        bundle = yf.Tickers(" ".join(tickers))
    except Exception:
        bundle = None
    for tk in tickers:
        c = None
        try:
            fi = bundle.tickers[tk].fast_info if bundle else yf.Ticker(tk).fast_info
            for key in ("market_cap", "marketCap"):
                try:
                    v = fi[key]
                except Exception:
                    v = getattr(fi, key, None)
                if v:
                    c = float(v)
                    break
        except Exception:
            c = None
        caps[tk] = c
    return caps


def main():
    uniq = sorted({t for d in THEME_DEFS for t in d["tickers"]} | {BENCH})
    raw = yf.download(uniq, period="8mo", auto_adjust=True, progress=False)
    close = (raw["Close"] if "Close" in raw else raw).sort_index().ffill()
    close = close[close[BENCH].notna()]

    if BENCH not in close.columns:
        raise SystemExit("SPY の取得に失敗しました。ネットワークを確認してください。")

    B = close[BENCH].to_numpy()
    N = len(close)
    if N < 8:
        raise SystemExit("営業日数が不足しています。period を長くしてください。")

    lb = min(LOOKBACK, N - 2)
    win = max(2, min(POINTS, N - lb))
    start = N - win

    out = []
    dropped = []
    for d in THEME_DEFS:
        comps, used = [], []
        for tk in d["tickers"]:
            if tk in close.columns:
                s = close[tk].to_numpy()
                if not np.isnan(s).any():   # full coverage over the window
                    comps.append(s / s[0])  # normalise to first date = 1
                    used.append(tk)
        if not comps:
            dropped.append(d["name"])
            continue
        idx = np.mean(comps, axis=0)        # equal-weight theme index
        hist = []
        for i in range(start, N):
            j = max(0, i - lb)
            k = max(0, i - 5)
            x = 100.0 * (np.log(idx[i] / idx[j]) - np.log(B[i] / B[j]))
            y = 100.0 * (idx[i] / idx[k] - 1.0)
            if np.isfinite(x) and np.isfinite(y):
                hist.append([round(float(x), 2), round(float(y), 2)])
        if len(hist) >= 2:
            stocks = []
            for tk in used:
                s = close[tk].to_numpy()
                def ret(days):
                    j = len(s) - 1 - days
                    if j < 0 or not np.isfinite(s[j]) or s[j] == 0:
                        return None
                    return round(float(s[-1] / s[j] - 1) * 100, 1)
                stocks.append({"t": tk, "d1": ret(1), "d5": ret(5), "d63": ret(63)})
            out.append({"name": d["name"], "color": d["color"], "history": hist, "tickers": used, "stocks": stocks})

    if WITH_CAP and out:
        used_all = sorted({tk for th in out for tk in th["tickers"]})
        print(f"時価総額を取得中… ({len(used_all)} 銘柄) 少し時間がかかります")
        caps = fetch_caps(used_all)
        for th in out:
            vals = [caps[tk] for tk in th["tickers"] if caps.get(tk)]
            if vals:
                th["cap"] = round(sum(vals) / 1e9, 1)   # 構成銘柄の時価総額合計（十億ドル）
            for st in th.get("stocks", []):
                c = caps.get(st["t"])
                if c:
                    st["sz"] = "大型" if c >= 2e11 else ("中型" if c >= 1e10 else "小型")
        have = sum(1 for th in out if "cap" in th)
        print(f"時価総額を付与: {have}/{len(out)} テーマ"
              + ("（全テーマ揃ったのでビューアでバブルサイズが有効になります）" if have == len(out)
                 else "（一部欠損のためサイズは均一のままになります）"))

    payload = {"lastDate": str(close.index[-1].date()), "themes": out}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    print(f"data.json を書き出しました: {len(out)} テーマ / {win} 点 / 最終日 {payload['lastDate']}")
    if dropped:
        print("データ不足でスキップ:", ", ".join(dropped))


if __name__ == "__main__":
    main()
