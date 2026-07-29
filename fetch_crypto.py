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
import re
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
AUTO_THEMES = 16           # 自動分類で採用するカテゴリ数
MIN_MEMBERS = 3            # これ未満の銘柄しか無いテーマは捨てる
OVERLAP_MAX = 0.6          # 既出テーマと構成銘柄がこれ以上重なったら捨てる

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
AUTO_SKIP_WORDS = [
    # 広すぎる / 他テーマと丸かぶり
    "ecosystem", "smart contract platform", "cryptocurrency", "all categories",
    "layer 0",
    # 値動きが無い / 原資産の複製
    "stablecoin", "wrapped", "tokenized", "asset-backed", "staking derivative",
    # 投資家・ファンド・上場ラベル(テーマではない)
    "portfolio", "index", "capital", "ventures", "backed", "launchpad", "launchpool",
    "ico", "ido", "presale", "airdrop",
    # 出自・法的ラベル
    "made in", "alleged", "securities", "usa", "china", "korea", "japan",
    "elon", "celebrity", "meme-ish",
    # コンセンサス方式(テーマではない)
    "proof of", "pos", "pow",
    # 特定チェーン固有
    "native", "ethereum", "solana", "avalanche", "polygon", "arbitrum", "optimism",
    "base", "sui", "aptos", "ton", "tron", "cardano", "polkadot", "cosmos", "near",
    "algorand", "fantom", "sonic", "berachain", "hyperliquid", "bnb",
]


# 単体だと汎用的すぎるが、複合語なら有効なもの(例: Bridge Governance は残す)
AUTO_SKIP_EXACT = {"governance", "protocol", "token", "coin", "native", "base"}


def blocked(name):
    """除外語判定。名前そのものが汎用語なら除外、複数語はそのまま部分一致、
    1語は語境界(複数形も含む)で判定する。"""
    n = name.lower().strip()
    if n in AUTO_SKIP_EXACT:
        return True
    for w in AUTO_SKIP_WORDS:
        if " " in w or "-" in w:
            if w in n:
                return True
        elif re.search(r"\b" + re.escape(w) + r"s?\b", n):   # 複数形も拾う
            return True
    return False


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
    return [(c["name"], c["id"], c["name"]) for c in ranked if not blocked(c["name"])]


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


def resolve(defs, universe, want=None, dedup=False, max_fetch=40):
    """カテゴリ定義 -> 構成銘柄。dedup=True なら既出テーマと重なるカテゴリを捨てる。"""
    out, seen, fetched = [], [], 0
    for jp, cid, en in defs:
        if want is not None and len(out) >= want:
            break
        if fetched >= max_fetch:
            break
        picks = members(cid, universe)
        fetched += 1
        if len(picks) < MIN_MEMBERS:
            print(f"  [skip] {jp}: 構成銘柄 {len(picks)} 件のみ")
            continue
        ids = {u["id"] for u in picks}
        if dedup and any(len(ids & s) / len(ids) > OVERLAP_MAX for s in seen):
            print(f"  [skip] {jp}: 既出テーマと構成銘柄が重複")
            continue
        seen.append(ids)
        out.append((jp, en, picks))
    return out


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


def build(resolved, close, full, bench):
    """解決済みテーマ -> RRG用の history / stocks を組み立てる。"""
    N = len(bench)
    lb = min(LOOKBACK, N - 2)
    win = max(2, min(POINTS, N - lb))
    start = N - win
    out = []
    for i, (jp, en, picks) in enumerate(resolved):
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
        if len(comps) < MIN_MEMBERS:
            print(f"  [skip] {jp}: 価格を取得できた銘柄 {len(comps)} 件のみ")
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

    print("厳選テーマの構成銘柄を解決中…")
    manual_res = resolve(manual_defs, universe)
    print("自動テーマの構成銘柄を解決中…(重複カテゴリは除外)")
    auto_res = resolve(auto_defs, universe, want=AUTO_THEMES, dedup=True)

    print("厳選テーマを構築中…")
    manual = build(manual_res, close, full, bench)
    print("自動テーマを構築中…")
    auto = build(auto_res, close, full, bench)

    payload = {
        "lastDate": str(close.index[-1].date()),
        "benchmark": BENCH_SYM,
        "shortDays": SHORT,
        "metricLabels": {"d252": "1年", "d63": "30日", "d5": "7日", "d1": "1日"},
        "title": "暗号資産テーマトラッカー",
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
