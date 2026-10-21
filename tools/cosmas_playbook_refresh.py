import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from fxglitch.venues.bitunix import Bitunix

data = json.loads(Path('/app/data/cosmas_study.json').read_text())
sigs = data['signals']
venue = Bitunix()

def valid_geom(s):
    d, e, stop, tps = s.get('direction'), s.get('entry'), s.get('stop'), s.get('tps') or []
    if not d or e is None or stop is None or not tps: return False
    tp = tps[0]
    return (stop < e < tp) if d == 'LONG' else (stop > e > tp) if d == 'SHORT' else False

def parse_iso(s):
    try: return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception: return None

def candle_ts(c):
    ts = getattr(c, 'open_time', None) or getattr(c, 'time', None)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    val = float(ts)
    if val > 1e12: val /= 1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc)

def walk(direction, entry, stop, tp, candles, mode='full'):
    risk = abs(entry - stop) or 1e-12
    one_r = entry + risk if direction == 'LONG' else entry - risk
    target = tp if mode == 'full' else one_r
    for ts, high, low, close in candles:
        if direction == 'LONG':
            hit_sl = low <= stop
            hit_tp = high >= target
        else:
            hit_sl = high >= stop
            hit_tp = low <= target
        if hit_sl and hit_tp:
            return 'ambiguous'
        if hit_sl: return 'sl'
        if hit_tp: return 'tp' if mode=='full' else 'plus1R'
    return 'open'

valid = [s for s in sigs if valid_geom(s)]
rows = []
for s in valid:
    posted = parse_iso(s.get('date'))
    symbol = s.get('symbol')
    if not posted or not symbol: continue
    try:
        candles = venue.candles(symbol, '1h', limit=500)
        seq = sorted(((candle_ts(c), float(c.high), float(c.low), float(c.close)) for c in candles), key=lambda x: x[0])
        path = [c for c in seq if c[0] >= posted]
        full = walk(s['direction'], s['entry'], s['stop'], s['tps'][0], path, 'full')
        one = walk(s['direction'], s['entry'], s['stop'], s['tps'][0], path, '1r')
        risk = abs(s['entry']-s['stop'])
        rr = abs(s['tps'][0]-s['entry'])/risk if risk else None
        rows.append({**{k:s[k] for k in ('id','date','symbol','direction','entry','stop','tps','leverage','cmp','raw')},
                     'rr': rr, 'stop_pct': abs(s['entry']-s['stop'])/s['entry']*100,
                     'tp_pct': abs(s['tps'][0]-s['entry'])/s['entry']*100,
                     'full_outcome': full, 'one_r_outcome': one})
    except Exception as e:
        rows.append({'id': s.get('id'), 'symbol': symbol, 'error': f'{type(e).__name__}: {e}'})

ok = [r for r in rows if 'error' not in r]
full_tp = [r for r in ok if r['full_outcome']=='tp']
full_sl = [r for r in ok if r['full_outcome']=='sl']
one_win = [r for r in ok if r['one_r_outcome']=='plus1R']
one_sl = [r for r in ok if r['one_r_outcome']=='sl']

def avg(xs): return sum(xs)/len(xs) if xs else None

# structural habits from ALL valid posts (strategy voice)
habits = {
  'n_valid': len(ok),
  'cmp_share': sum(1 for r in ok if r.get('cmp'))/len(ok) if ok else 0,
  'dir_mix': dict(Counter(r['direction'] for r in ok)),
  'lev_avg': avg([r['leverage'] for r in ok if r.get('leverage') is not None]),
  'lev_mode_buckets': dict(Counter(('<=7' if (r.get('leverage') or 0)<=7 else '8-15' if r['leverage']<=15 else '>15') for r in ok if r.get('leverage') is not None)),
  'median_rr': median([r['rr'] for r in ok if r.get('rr') is not None]) if ok else None,
  'avg_rr': avg([r['rr'] for r in ok if r.get('rr') is not None]),
  'median_stop_pct': median([r['stop_pct'] for r in ok]) if ok else None,
  'median_tp_pct': median([r['tp_pct'] for r in ok]) if ok else None,
  'p25_stop_pct': sorted(r['stop_pct'] for r in ok)[max(0,len(ok)//4-1)] if ok else None,
  'p75_stop_pct': sorted(r['stop_pct'] for r in ok)[min(len(ok)-1, (3*len(ok)//4))] if ok else None,
  'scalp_share': sum(1 for r in ok if 'scalp' in (r.get('raw') or '').lower())/len(ok) if ok else 0,
  'full_tp': len(full_tp), 'full_sl': len(full_sl),
  'full_win_rate': len(full_tp)/(len(full_tp)+len(full_sl)) if (full_tp or full_sl) else None,
  'oneR_hits': len(one_win), 'oneR_sl': len(one_sl),
  'oneR_edge': len(one_win)/(len(one_win)+len(one_sl)) if (one_win or one_sl) else None,
  'oneR_avg_rr_on_winners': avg([r['rr'] for r in one_win if r.get('rr') is not None]),
  'symbols_most_posted': Counter(r['symbol'] for r in ok).most_common(12),
}

playbook = {
  'version': 1,
  'source': 'Cosmas TG -1002004401199',
  'method': 'Telethon history + Bitunix 1h first-touch scoring; valid SL/TP geometry only',
  'voice': {
    'entry': 'Mostly CMP / market (~71% of valid posts)',
    'leverage': 'Usually 7x, sometimes 10x (avg ~7.4); desk daredevil may floor to 15x',
    'stop': 'Invalidation often wide on alts (median tens of %); not tight microstructure stops',
    'tp': 'Asymmetric stretch targets — median RR high (moonshot/runner). Scalping tag still often has far TP',
    'direction': 'Near-balanced LONG/SHORT in valid sample',
    'tags': ['Scalping', 'Night research', 'Becareful of volatility'],
  },
  'edge_notes': {
    'full_tp_vs_sl': habits.get('full_win_rate'),
    'plus1R_before_minus1R': habits.get('oneR_edge'),
    'interpretation': 'Cosmas posts lottery-style TP distances; structural +1R before -1R is the realistic skill metric until longer history accumulates TP hits.',
  },
  'suggest_rules_v1': [
    'Entry: CMP',
    'Leverage: 7x-10x signal style (execution may use desk daredevil 15x)',
    'Geometry must be valid',
    'Stop: structural invalidation beyond recent swing (Cosmas-like width), not 0.2% scalp noise',
    'TP: primary runner target multi-R (aim median Cosmas RR band) + optional scale/BE plan at +1R',
    'Prefer symbols Cosmas actually trades / liquid USDT perps',
    'Reject live chase if mark R:R collapses below 1.0 for suggestions (0.5 only for auto-follow)',
    'Label scalping only when both stop% and tp% are relatively contained vs median',
  ],
  'habits': habits,
}

Path('/app/data/cosmas_playbook.json').write_text(json.dumps({
  'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
  'habits': habits,
  'playbook': playbook,
  'oneR_samples': one_win[:10] + one_sl[:10],
}, indent=2, default=str), encoding='utf-8')
print(json.dumps({'habits': habits, 'playbook_voice': playbook['voice'], 'suggest_rules_v1': playbook['suggest_rules_v1'], 'edge_notes': playbook['edge_notes']}, indent=2))