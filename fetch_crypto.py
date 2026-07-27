#!/usr/bin/env python3
"""
暗号資産テーマトラッカー用 データ生成スクリプト
------------------------------------------------------------
  ランキング/時価総額/カテゴリ … CoinGecko(無料)
  価格履歴                     … Yahoo Finance(yfinance, 一括取得)

  テーマ指数 = 構成銘柄を初日=1に正規化して平均(等加重)
  x = 長期(60日)の対BTC超過リターン(対数, %)
  y = 短期(3日)リターン(%)
を直近21日ぶん計算して crypto.json に書き出します。

2種類の分類を同時に出力します:
  manual … 意味のあるテーマを指定(日本語名つき)
  auto   … 時価総額上位のカテゴリを機械的に採用

使い方:
    pip install yfinance requests
    python fetch_crypto.py
環境変数(任意):
    COINGECKO_API_KEY  … Demoキーを入れると安定します(無料登録で取得可)
    TOP_N              … 対象とする時価総額の順位(既定 500)
"""

import json
import os
import time
import numpy as np
import requests
import yfinance as yf

BENCH_SYM = "BTC"          # ベンチマーク(BTC建てで見る)
BENCH_TK = "BTC-USD"
LOOKBACK = 60              # 長期ルックバック(日)
SHORT = 3                  # 短期リターンの日数(暗号資産は変動が大きいので株の5日より短く)
POINTS = 21                # 最新 + 過去20日
ROWS = 180                 # RRG計算に使う直近日数
SPARK_POINTS = 52
TOP_N = int(os.getenv("TOP_N", "500"))
PER_THEME = 8              # 1テーマあたりの最大銘柄数
AUTO_THEMES = 18           # 自動分類で採用するカテゴリ数

CG = "https://api.coingecko.com/api/v3"
KEY = os.getenv("COINGECKO_API_KEY", "").strip()
PALETTE = ["pink", "blue", "green", "yellow", "red", "purple", "gray"]

# 値動きが無い/原資産の複製にあたるものは除外(RRGでは意味を持たないため)
DENY = {
    "USDT", "USDC", "DAI", "FDUSD", "USDE", "TUSD", "USDS", "PYUSD", "BUSD", "USD1",
    "USDD", "FRAX", "LUSD", "GUSD", "EURC", "USDF", "BUIDL", "RLUSD", "USDY",
    "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "RETH", "WBETH", "BTCB",
    "SOLVBTC", "LBTC", "CBETH", "METH", "EZETH", "RSETH", "SUSDE", "SUSDS", "WBNB",
    "TBTC", "SWETH", "OSETH", "ANKRETH", "STSOL", "MSOL", "JITOSOL", "BSC-USD",
}

# 手動テーマ: CoinGeckoのカテゴリ名に含まれるキーワードで解決する
# (IDを直書きすると改名で壊れるため、名前照合にしている)
MANUAL_DEFS = [
    ("L1（レイヤー1）", ["layer 1", "layer-1"]),
    ("L2（レイヤー2）", ["layer 2", "layer-2"]),
    ("DeFi", ["decentralized finance"]),
    ("DEX", ["decentralized exchange"]),
    ("ミーム", ["meme"]),
    ("AI", ["artificial intelligence"]),
    ("RWA", ["real world asset"]),
    ("DePIN", ["depin", "decentralized physical"]),
    ("ゲーム", ["gaming"]),
    ("取引所トークン", ["centralized exchange", "exchange-based", "exchange token"]),
    ("オラクル", ["oracle"]),
    ("プライバシー", ["privacy"]),
    ("リキステ", ["liquid staking"]),
    ("決済", ["payment"]),
    ("ストレージ", ["storage"]),
    ("NFT", ["nft"]),
    ("ブリッジ", ["bridge"]),
    ("予測市場", ["prediction market"]),
    ("ZK", ["zero knowledge", "zero-knowledge"]),
    ("SocialFi", ["socialfi", "social"]),
]

# 自動分類から外すカテゴリ(広すぎる/値動きが無い)
AUTO_SKIP_WORDS = ["ecosystem", "stablecoin", "portfolio", "index", "wrapped",
                   "tokenized", "asset-backed", "all categories", "cryptocurrency"]


def cg_get(path, params=None, tries=4):
    """CoinGecko GET。レート制限に配慮して待ちを入れ、429はリトライする。"""
    headers = {"accept": "application/json"}
    if KEY:
        headers["x-cg-demo-api-key"] = KEY
    for i in range(tries):
        try:
            r = requests.get(CG + path, params=params or {}, headers=headers, timeout=30)
            if r.status_code == 429:
                wait = 15 * (i + 1)
                print(f"  レート制限。{wait}秒待機します…")
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(1.5 if KEY else 4.0)   # キー無しは毎分5〜15回なので長めに待つ
            return r.json()
        except requests.RequestException as e:
            if i == tries - 1:
                raise
            print(f"  再試行 {i+1}/{tries}: {e}")
            time.sleep(5)
    return None


def yahoo_ticker(sym):
    return f"{sym.upper()}-USD"


def fetch_universe():
    """時価総額 TOP_N の銘柄を取得。"""
    out = {}
    pages = (TOP_N + 249) // 250
    for p in range(1, pages + 1):
        data = cg_get("/coins/markets", {
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": 250, "page": p, "sparkline": "false",
        })
        for c in data or []:
            sym = (c.get("symbol") or "").upper()
            if not sym or sym in DENY:
                continue
            out[c["id"]] = {
                "id": c["id"], "sym": sym, "name": c.get("name") or sym,
                "cap": c.get("market_cap") or 0, "rank": c.get("market_cap_rank") or 9999,
            }
        print(f"  ランキング {p}ページ目 取得 (累計 {len(out)} 銘柄)")
    return out


def fetch_categories():
    cats = cg_get("/coins/categories") or []
    return [c for c in cats if c.get("id") and c.get("name")]


def pick_manual(cats):
    """キーワード照合で手動テーマのカテゴリを決める。"""
    used, defs = set(), []
    for jp, keys in MANUAL_DEFS:
        hit = None
        for c in cats:
            nm = c["name"].lower()
            if c["id"] in used:
                continue
            if any(k in nm for k in keys):
                hit = c
                break
        if hit:
            used.add(hit["id"])
            defs.append((jp, hit["id"], hit["name"]))
        else:
            print(f"  [skip] カテゴリ未解決: {jp}")
    return defs


def pick_auto(cats):
    """時価総額の大きいカテゴリを機械的に採用。"""
    ranked = sorted(cats, key=lambda c: -(c.get("market_cap") or 0))
    out = []
    for c in ranked:
        nm = c["name"].lower()
        if any(w in nm for w in AUTO_SKIP_WORDS):
            continue
        out.append((c["name"], c["id"], c["name"]))
        if len(out) >= AUTO_THEMES:
            break
    return out


def members(cat_id, universe):
    """カテゴリの構成銘柄のうち、TOP_N に入るものを時価総額順に PER_THEME 件。"""
    data = cg_get("/coins/markets", {
        "vs_currency": "usd", "order": "market_cap_desc",
        "per_page": 50, "page": 1, "category": cat_id, "sparkline": "false",
    }) or []
    picks = []
    for c in data:
        u = universe.get(c["id"])
        if u and u["sym"] not in DENY:
            picks.append(u)
        if len(picks) >= PER_THEME:
            break
    return picks


def sparkline(series):
    s = np.asarray(series, dtype=float)
    s = s[np.isfinite(s)]
    if len(s) < 20:
        return None, None
    s = s[-365:]
    n = min(SPARK_POINTS, len(s))
    idx = np.unique(np.linspace(0, len(s) - 1, n).round().astype(int))
    base = float(s[idx[0]])
    if base == 0 or not np.isfinite(base):
        return None, None
    vals = [round((float(s[i]) / base - 1.0) * 100, 1) for i in idx]
    return vals, vals[-1]


def build(themedefs, universe, close, full, bench):
    """テーマ定義 -> RRG用の history / stocks を組み立てる。"""
    N = len(bench)
    lb = min(LOOKBACK, N - 2)
    win = max(2, min(POINTS, N - lb))
    start = N - win
    out = []
    for i, (jp, cat_id, en) in enumerate(themedefs):
        picks = members(cat_id, universe)
        comps, used = [], []
        for u in picks:
            tk = yahoo_ticker(u["sym"])
            if tk not in close.columns:
                continue
            s = close[tk].to_numpy()
            if np.isnan(s).any() or (s <= 0).any():
                continue
            comps.append(s)
            used.append(u)
        if len(comps) < 2:
            print(f"  [skip] {jp}: 有効銘柄 {len(comps)} 件")
            continue
        idx = np.zeros(N)
        for s in comps:
            idx += s / s[0]
        idx /= len(comps)

        hist = []
        for k in range(start, N):
            j = max(0, k - lb)
            m = max(0, k - SHORT)
            x = 100 * (np.log(idx[k] / idx[j]) - np.log(bench[k] / bench[j]))
            y = 100 * (idx[k] / idx[m] - 1)
            if np.isfinite(x) and np.isfinite(y):
                hist.append([round(float(x), 2), round(float(y), 2)])
        if len(hist) < 2:
            continue

        stocks = []
        for u in used:
            tk = yahoo_ticker(u["sym"])
            s = close[tk].to_numpy()

            def ret(days):
                j = len(s) - 1 - days
                if j < 0 or not np.isfinite(s[j]) or s[j] == 0:
                    return None
                return round(float(s[-1] / s[j] - 1) * 100, 1)

            st = {"t": u["sym"], "d1": ret(1), "d5": ret(7), "d63": ret(30)}
            sp, y1 = sparkline(full[tk].to_numpy()) if tk in full.columns else (None, None)
            if sp:
                st["spark"] = sp
                st["d252"] = y1
            cap = u["cap"]
            st["sz"] = "大型" if cap >= 1e10 else ("中型" if cap >= 1e9 else "小型")
            stocks.append(st)

        out.append({
            "name": jp, "color": PALETTE[i % len(PALETTE)], "history": hist,
            "tickers": [u["sym"] for u in used], "stocks": stocks,
            "cap": round(sum(u["cap"] for u in used) / 1e9, 1),
            "note": en,
        })
        print(f"  {jp}: {len(used)}銘柄  ({en})")
    return out


def main():
    print(f"CoinGecko からランキングを取得中… (TOP {TOP_N}"
          + (", APIキーあり)" if KEY else ", APIキー無し=低速)"))
    universe = fetch_universe()
    if not universe:
        raise SystemExit("ランキングを取得できませんでした")

    print("カテゴリ一覧を取得中…")
    cats = fetch_categories()
    print(f"  {len(cats)} カテゴリ")
    manual_defs = pick_manual(cats)
    auto_defs = pick_auto(cats)
    print(f"  手動テーマ {len(manual_defs)} / 自動テーマ {len(auto_defs)}")

    # 価格履歴は Yahoo から一括取得(1リクエスト)
    tickers = sorted({yahoo_ticker(u["sym"]) for u in universe.values()} | {BENCH_TK})
    print(f"Yahoo Finance から価格履歴を取得中… ({len(tickers)} 銘柄を一括)")
    raw = yf.download(tickers, period="14mo", interval="1d",
                      auto_adjust=True, progress=False, threads=True)
    full = (raw["Close"] if "Close" in raw else raw).sort_index().ffill()
    if BENCH_TK not in full.columns:
        raise SystemExit("BTC-USD を取得できませんでした")
    full = full[full[BENCH_TK].notna()]
    ok = int(full.notna().any().sum())
    print(f"  価格を取得できた銘柄: {ok} / {len(tickers)}")

    close = full.tail(ROWS)
    bench = close[BENCH_TK].to_numpy()
    if len(bench) < LOOKBACK + 5:
        raise SystemExit("日数が不足しています")

    print("手動テーマを構築中…")
    manual = build(manual_defs, universe, close, full, bench)
    print("自動テーマを構築中…")
    auto = build(auto_defs, universe, close, full, bench)

    payload = {
        "lastDate": str(close.index[-1].date()),
        "benchmark": BENCH_SYM,
        "shortDays": SHORT,
        "metricLabels": {"d252": "1年", "d63": "30日", "d5": "7日", "d1": "1日"},
        "setLabels": {"manual": "厳選テーマ", "auto": "自動分類"},
        "sets": {"manual": manual, "auto": auto},
    }
    with open("crypto.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize("crypto.json") / 1024
    print(f"\ncrypto.json を書き出しました: 厳選 {len(manual)} / 自動 {len(auto)} テーマ"
          f" / 最終日 {payload['lastDate']} / {size:.0f} KB")


if __name__ == "__main__":
    main()
