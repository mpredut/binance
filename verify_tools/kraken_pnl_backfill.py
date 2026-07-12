#!/usr/bin/env python3
"""One-shot / cron READ-ONLY: trage TOT istoricul TradesHistory de la Kraken (paginat pe
`ofs`) si scrie <root>/kraken_trades_full.json + un rezumat P&L realizat.

DE CE separat de kraken_cachemanager: acela tine intentionat o fereastra de 14 zile
(rate-limit Kraken, call greu) — corect pt TRADING (cititorul filtreaza oricum pe since_s).
Asta e DOAR pt ANALIZA P&L pe istoric complet, decuplat de flota. NU atinge cache-ul live.

Cheie: perechea _SPARE (fallback _BOT). Nonce = time_ns (nanosec) -> nu se ciocneste cu
procesele vii. Paginare blanda (sleep) ca sa nu atinga rate-limit-ul de cont.
"""
import os, sys, json, time, datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)                      # radacina repo (parintele verify_tools/)
KD = os.path.join(ROOT, 'kraken')
OUT = os.path.join(ROOT, 'kraken_trades_full.json')


def env_value(folder, name):
    for fn in ('.env', 'config.env'):
        p = os.path.join(folder, fn)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            if k.strip() == name:
                return v.strip().strip('"').strip("'") or None
    return None


def _client():
    key = env_value(KD, 'KRAKEN_API_KEY_SPARE') or env_value(KD, 'KRAKEN_API_KEY_BOT')
    sec = env_value(KD, 'KRAKEN_API_SECRET_SPARE') or env_value(KD, 'KRAKEN_API_SECRET_BOT')
    if not key or not sec:
        sys.exit('[kraken_pnl] lipsesc cheile _SPARE/_BOT in kraken/.env')
    saved = list(sys.path)
    sys.modules.pop('common', None); sys.modules.pop('kraken_client', None)
    sys.path.insert(0, KD)
    from kraken_client import KrakenClient
    sys.path[:] = saved
    return KrakenClient(key, sec)


def fetch_all_trades(cli):
    all_trades, ofs, total, pages = {}, 0, None, 0
    while True:
        res = cli._private('TradesHistory', {'ofs': ofs}, fresh=True)
        batch = (res or {}).get('trades', {}) or {}
        if total is None:
            total = int((res or {}).get('count', 0))
        if not batch:
            break
        all_trades.update(batch)
        pages += 1; ofs += len(batch)
        if len(all_trades) >= total or len(batch) < 50:
            break
        time.sleep(2.0)                            # bland cu rate-limit-ul (cost 20/apel)
    return all_trades, total


def main():
    cli = _client()
    trades, total = fetch_all_trades(cli)
    by_sym = {}
    for txid, t in trades.items():
        typ = t.get('type', '')
        by_sym.setdefault(t.get('pair', '?'), []).append(dict(
            txid=txid, side='BUY' if typ == 'buy' else 'SELL',
            price=float(t.get('price', 0)), qty=float(t.get('vol', 0)),
            cost=float(t.get('cost', 0)), fee=float(t.get('fee', 0)),
            time=int(float(t.get('time', 0)) * 1000)))
    out = {'items': by_sym, 'source': 'TradesHistory full (read-only)',
           'total': total, 'generated': dt.datetime.now().isoformat()}
    json.dump(out, open(OUT, 'w'), indent=1)

    print(f'[kraken_pnl] {len(trades)}/{total} trades -> {OUT}')
    grand = 0.0
    for sym, tr in sorted(by_sym.items()):
        tr.sort(key=lambda x: x['time'])
        bq = sum(x['qty'] for x in tr if x['side'] == 'BUY')
        sq = sum(x['qty'] for x in tr if x['side'] == 'SELL')
        bv = sum(x['cost'] for x in tr if x['side'] == 'BUY')
        sv = sum(x['cost'] for x in tr if x['side'] == 'SELL')
        fee = sum(x['fee'] for x in tr)
        avg_buy = bv / bq if bq else 0
        realized = sv - avg_buy * sq - fee
        grand += realized
        print(f'  {sym:<12} realized={realized:+9.2f} USD  (net_qty={bq-sq:+.4f}, {len(tr)} trades)')
    print(f'[kraken_pnl] TOTAL realized (istoric complet) = {grand:+.2f} USD')


if __name__ == '__main__':
    main()
