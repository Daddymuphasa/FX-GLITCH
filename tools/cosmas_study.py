"""Study Cosmas TG signals using a copied Telethon session (live desk keeps the original)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path('/app')
DATA = ROOT / 'data'
OUT = DATA / 'cosmas_study.json'
ENV = ROOT / '.env'
SESSION_SRC = DATA / 'telegram.session'
SESSION_COPY = DATA / 'telegram_study.session'

def load_env():
    for line in ENV.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env()

from fxglitch.signal_copy import parse_signal
from fxglitch.agent.pairs import map_to_binance
from fxglitch.venues.bitunix import Bitunix

CHAT = -1002004401199
LIMIT = 800

def parse_iso(s):
    if not s: return None
    try:
        return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception:
        return None

def candle_ts(c):
    ts = getattr(c, 'open_time', None)
    if ts is None:
        ts = getattr(c, 'time', None)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if ts is None:
        raise ValueError('no candle time')
    val = float(ts)
    if val > 1e12:
        val /= 1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc)

def score_path(direction, entry, stop, tps, candles):
    if not candles or entry is None or stop is None or not tps:
        return {'outcome': 'unknown', 'reason': 'missing data'}
    tp = tps[0]
    for ts, high, low, close in candles:
        if direction == 'LONG':
            hit_sl = low <= stop
            hit_tp = high >= tp
            if hit_sl and hit_tp:
                return {'outcome': 'ambiguous', 'at': ts.isoformat(), 'close': close}
            if hit_sl:
                return {'outcome': 'sl', 'at': ts.isoformat(), 'close': close}
            if hit_tp:
                return {'outcome': 'tp', 'at': ts.isoformat(), 'close': close}
        else:
            hit_sl = high >= stop
            hit_tp = low <= tp
            if hit_sl and hit_tp:
                return {'outcome': 'ambiguous', 'at': ts.isoformat(), 'close': close}
            if hit_sl:
                return {'outcome': 'sl', 'at': ts.isoformat(), 'close': close}
            if hit_tp:
                return {'outcome': 'tp', 'at': ts.isoformat(), 'close': close}
    last = candles[-1]
    risk = abs(entry - stop) or 1e-9
    if direction == 'LONG':
        mtm = (last[3] - entry) / risk
    else:
        mtm = (entry - last[3]) / risk
    return {'outcome': 'open_or_expired', 'at': last[0].isoformat(), 'close': last[3], 'r_multiple': mtm}

async def fetch_messages():
    from telethon import TelegramClient
    shutil.copy2(SESSION_SRC, SESSION_COPY)
    # ignore journal
    api_id = int(os.environ['TELEGRAM_API_ID'])
    api_hash = os.environ['TELEGRAM_API_HASH']
    client = __import__('fxglitch.tgnet',fromlist=['x']).make_client(str(SESSION_COPY), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit('study session not authorized')
    rows = []
    async for msg in client.iter_messages(CHAT, limit=LIMIT):
        text = (msg.message or '').strip()
        if not text:
            continue
        low = text.lower()
        if not any(k in low for k in ('usdt', 'long', 'short', 'tp', 'sl', 'leverage', 'entry')):
            continue
        rows.append({
            'id': f'{CHAT}:{msg.id}',
            'date': msg.date.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if msg.date else None,
            'raw': text[:2000],
        })
    await client.disconnect()
    return rows

def avg(xs):
    return sum(xs)/len(xs) if xs else None

def main():
    msgs = asyncio.run(fetch_messages())
    venue = Bitunix()
    studied = []
    outcomes = Counter()
    styles = Counter()
    dirs = Counter()
    lev_bucket = Counter()
    rr_list = []
    stop_pcts = []
    tp_pcts = []
    winners = []
    losers = []
    errors = Counter()

    for m in msgs:
        sig = parse_signal(m['raw'])
        direction = sig.direction.value if sig.direction else None
        entry = float(sig.entry) if sig.entry not in (None, '') else None
        stop = float(sig.stop_loss) if sig.stop_loss not in (None, '') else None
        tps = [float(x) for x in (sig.take_profits or [])] if sig.take_profits else []
        if sig.take_profit is not None and float(sig.take_profit) not in tps:
            tps = [float(sig.take_profit)] + tps
        lev = float(sig.leverage) if sig.leverage not in (None, '') else None
        pair = map_to_binance(sig.symbol, None)
        symbol = pair.binance if pair else None
        if not symbol:
            mm = re.search(r'([A-Za-z0-9]+)\s*/?\s*usdt', m['raw'], re.I)
            if mm:
                symbol = mm.group(1).upper() + 'USDT'
        low = m['raw'].lower()
        style = 'unknown'
        if 'scalp' in low: style = 'scalping'
        elif 'swing' in low: style = 'swing'
        elif 'night' in low: style = 'night'
        elif 'research' in low: style = 'research'
        cmp_entry = bool(getattr(sig, 'entry_is_market', False)) or (entry is None and 'cmp' in low)
        planned_rr = None
        if entry and stop and tps:
            risk = abs(entry - stop)
            reward = abs(tps[0] - entry)
            planned_rr = (reward / risk) if risk else None
        posted = parse_iso(m['date'])
        result = {'outcome': 'unscored'}
        mark_at_post = None
        if symbol and direction and stop and tps and posted:
            try:
                candles_raw = venue.candles(symbol, '15m', limit=300)
                seq = []
                for c in candles_raw:
                    seq.append((candle_ts(c), float(c.high), float(c.low), float(c.close)))
                seq.sort(key=lambda x: x[0])
                after = [c for c in seq if c[0] >= posted]
                before = [c for c in seq if c[0] < posted]
                if after:
                    mark_at_post = after[0][3]
                    path = after
                elif before:
                    mark_at_post = before[-1][3]
                    path = []
                else:
                    path = []
                use_entry = entry if entry is not None else mark_at_post
                if use_entry is not None:
                    if planned_rr is None and stop and tps:
                        risk = abs(use_entry - stop)
                        reward = abs(tps[0] - use_entry)
                        planned_rr = (reward / risk) if risk else None
                    result = score_path(direction, use_entry, stop, tps, path)
                    entry = use_entry
            except Exception as e:
                result = {'outcome': 'error', 'reason': f'{type(e).__name__}: {e}'}
                errors[type(e).__name__] += 1

        row = {
            'id': m['id'], 'date': m['date'], 'symbol': symbol, 'direction': direction,
            'entry': entry, 'stop': stop, 'tps': tps, 'leverage': lev, 'cmp': cmp_entry,
            'style': style, 'planned_rr': planned_rr, 'mark_at_post': mark_at_post,
            'result': result, 'raw': m['raw'][:400],
        }
        studied.append(row)
        outcomes[result.get('outcome','?')] += 1
        if direction: dirs[direction] += 1
        styles[style] += 1
        if lev is not None:
            if lev <= 7: lev_bucket['<=7x'] += 1
            elif lev <= 15: lev_bucket['8-15x'] += 1
            else: lev_bucket['>15x'] += 1
        if planned_rr is not None: rr_list.append(planned_rr)
        if entry and stop: stop_pcts.append(abs(entry-stop)/entry*100.0)
        if entry and tps: tp_pcts.append(abs(tps[0]-entry)/entry*100.0)
        if result.get('outcome') == 'tp': winners.append(row)
        elif result.get('outcome') == 'sl': losers.append(row)

    summary = {
        'studied_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'messages_fetched': len(msgs),
        'signals_parsed': len(studied),
        'outcomes': dict(outcomes),
        'errors': dict(errors),
        'directions': dict(dirs),
        'styles': dict(styles),
        'leverage_buckets': dict(lev_bucket),
        'avg_planned_rr': avg(rr_list),
        'median_planned_rr': median(rr_list) if rr_list else None,
        'avg_stop_pct': avg(stop_pcts),
        'avg_tp_pct': avg(tp_pcts),
        'win_count': len(winners),
        'loss_count': len(losers),
        'win_rate_resolved': (len(winners)/(len(winners)+len(losers))) if (winners or losers) else None,
        'winner_symbols': Counter(w['symbol'] for w in winners).most_common(20),
        'loser_symbols': Counter(w['symbol'] for w in losers).most_common(20),
        'winner_avg_rr': avg([w['planned_rr'] for w in winners if w.get('planned_rr') is not None]),
        'loser_avg_rr': avg([w['planned_rr'] for w in losers if w.get('planned_rr') is not None]),
        'winner_avg_stop_pct': avg([abs(w['entry']-w['stop'])/w['entry']*100 for w in winners if w.get('entry') and w.get('stop')]),
        'winner_avg_tp_pct': avg([abs(w['tps'][0]-w['entry'])/w['entry']*100 for w in winners if w.get('entry') and w.get('tps')]),
        'winner_styles': dict(Counter(w['style'] for w in winners)),
        'winner_dirs': dict(Counter(w['direction'] for w in winners)),
        'winner_lev_avg': avg([w['leverage'] for w in winners if w.get('leverage') is not None]),
        'cmp_share': sum(1 for s in studied if s['cmp'])/len(studied) if studied else 0,
        'sample_winners': [{k:w[k] for k in ('id','date','symbol','direction','entry','stop','tps','leverage','planned_rr','style')} for w in winners[:12]],
        'sample_losers': [{k:w[k] for k in ('id','date','symbol','direction','entry','stop','tps','leverage','planned_rr','style')} for w in losers[:12]],
    }
    notes = []
    if summary['win_rate_resolved'] is not None:
        notes.append(f"Resolved TP-hit rate {summary['win_rate_resolved']*100:.1f}% ({summary['win_count']} tp / {summary['loss_count']} sl) on 15m first-touch")
    if summary['winner_avg_rr'] is not None:
        notes.append(f"Winner planned RR avg {summary['winner_avg_rr']:.2f}; loser avg {summary['loser_avg_rr']}")
    if summary['avg_stop_pct'] is not None:
        notes.append(f"Avg stop {summary['avg_stop_pct']:.2f}% / avg TP {summary['avg_tp_pct']:.2f}% from entry")
    notes.append(f"CMP entries {summary['cmp_share']*100:.0f}%; styles {summary['styles']}; dirs {summary['directions']}")
    summary['notes'] = notes
    OUT.write_text(json.dumps({'summary': summary, 'signals': studied}, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2, default=str))
    print('WROTE', OUT)

if __name__ == '__main__':
    main()