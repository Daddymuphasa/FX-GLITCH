"""Deep Cosmas strategy knowledge builder -> /app/data/cosmas_strategy_knowledge.json"""
from __future__ import annotations
import json, os, re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median, mean

ROOT = Path('/app')
DATA = ROOT / 'data'
STUDY = DATA / 'cosmas_study.json'
OUT = DATA / 'cosmas_strategy_knowledge.json'
PLAY = DATA / 'cosmas_playbook.json'

ECOSYSTEM = {
  # majors
  'BTCUSDT': ['bitcoin','L1','major'], 'ETHUSDT': ['ethereum','L1','major'],
  'SOLUSDT': ['solana','L1','major'], 'BNBUSDT': ['bnb','L1','major'],
  'XRPUSDT': ['ripple','L1','major'], 'DOGEUSDT': ['meme','major'],
  'ADAUSDT': ['cardano','L1'], 'AVAXUSDT': ['avalanche','L1'],
  'DOTUSDT': ['polkadot','L1'], 'LINKUSDT': ['chainlink','oracle','eth-ecosystem'],
  'NEARUSDT': ['near','L1'], 'APTUSDT': ['aptos','L1'], 'SUIUSDT': ['sui','L1'],
  'INJUSDT': ['injective','defi','cosmos-adjacent'],
  'WIFUSDT': ['solana','meme'], 'BONKUSDT': ['solana','meme'], 'BOMEUSDT': ['solana','meme'],
  'PENGUUSDT': ['solana','meme'], 'POPCATUSDT': ['solana','meme'],
  'RAYUSDT': ['solana','defi','dex'], 'JUPUSDT': ['solana','defi'],
  'PUMPUSDT': ['solana','meme','launchpad'],
  'ENAUSDT': ['ethereum','defi','ethena'], 'ONDOUSDT': ['ethereum','rwa'],
  'WLDUSDT': ['ethereum','ai','worldcoin'], 'ARKMUSDT': ['ai'],
  'FETUSDT': ['ai'], 'RENDERUSDT': ['ai','gpu'],
  'TIAUSDT': ['celestia','modular','data-availability'],
  'SEIUSDT': ['sei','L1'], 'STXUSDT': ['bitcoin','stacks','l2'],
  'ORDIUSDT': ['bitcoin','ordinals'], '1000SATSUSDT': ['bitcoin','ordinals'],
  'OPUSDT': ['ethereum','l2','optimism'], 'ARBUSDT': ['ethereum','l2','arbitrum'],
  'MATICUSDT': ['ethereum','l2','polygon'], 'POLUSDT': ['ethereum','l2','polygon'],
  'QNTUSDT': ['interop','enterprise'], 'XAUUSDT': ['commodity','gold','macro'],
  'USUSDT': ['meme','new-listing'], 'PIPPINUSDT': ['meme','new-listing'],
  'MARSCOINUSDT': ['meme','new-listing'], 'SKRUSDT': ['new-listing'],
  'TRUTHUSDT': ['new-listing','meme'], 'TUTUSDT': ['new-listing'],
  'ACTUSDT': ['ai','meme'], 'CYSUSDT': ['new-listing'], 'BLESSUSDT': ['new-listing'],
  'BTRUSDT': ['new-listing'], 'PROMUSDT': ['new-listing'], 'PIEVERSEUSDT': ['new-listing'],
  'ONEUSDT': ['harmony','L1'], 'MUBARAKUSDT': ['meme','new-listing'],
  'DRIFTUSDT': ['solana','defi'], 'BTWUSDT': ['new-listing'],
}

RM_PHRASES = [
  (r'profit\s*taking|take\s*profit|secure', 'profit_taking'),
  (r'calculated\s*risk', 'calculated_risk'),
  (r'manipulation', 'manipulation_warning'),
  (r'becareful of volatility|careful of volatility|volatility', 'volatility_warning'),
  (r"don't control market|dont control market|we don't control", 'no_control_market'),
  (r'scalp', 'scalping_tag'),
  (r'night', 'night_session'),
  (r'research', 'research_tag'),
  (r'leverage', 'mentions_leverage'),
  (r'entry\s*cmp|cmp', 'cmp_entry'),
  (r'bitunix', 'venue_bitunix'),
  (r'fund|deposit', 'funding_call'),
  (r'sl\b|stop', 'mentions_sl'),
  (r'tp\b|take profit', 'mentions_tp'),
]

def parse_iso(s):
    try: return datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception: return None

def candle_ts(c):
    ts = getattr(c,'open_time',None) or getattr(c,'time',None)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    val=float(ts)
    if val>1e12: val/=1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc)

def valid_geom(s):
    d,e,stop,tps=s.get('direction'),s.get('entry'),s.get('stop'),s.get('tps') or []
    if not d or e is None or stop is None or not tps: return False
    tp=tps[0]
    return (stop < e < tp) if d=='LONG' else (stop > e > tp) if d=='SHORT' else False

def classify_msg(raw, parsed_ok):
    low=(raw or '').lower()
    if 'bitunix.com/register' in low and not parsed_ok:
        return 'promo'
    if re.search(r'\b(tp|sl)\b', low) and re.search(r'usdt|long|short|position', low):
        return 'trade_setup' if parsed_ok else 'partial_setup'
    if 'profit' in low and ('secure' in low or 'taking' in low):
        return 'risk_coaching'
    if 'upcoming' in low or 'to zero' in low or 'cooking' in low:
        return 'bias_teaser'
    if 'community' in low or 't.me/' in low:
        return 'community'
    return 'other'

def pct(a,b):
    if not b: return None
    return (a-b)/b*100.0

def main():
    from fxglitch.venues.bitunix import Bitunix
    study=json.loads(STUDY.read_text(encoding='utf-8'))
    sigs=study['signals']
    play=json.loads(PLAY.read_text(encoding='utf-8')) if PLAY.exists() else {}
    venue=Bitunix()

    # BTC/ETH context series
    btc=sorted(((candle_ts(c), float(c.close)) for c in venue.candles('BTCUSDT','1h',limit=500)), key=lambda x:x[0])
    eth=sorted(((candle_ts(c), float(c.close)) for c in venue.candles('ETHUSDT','1h',limit=500)), key=lambda x:x[0])

    def ret_around(series, t, hours):
        if not t or not series: return None
        # closest close at/before t, and at/before t+hours
        before=[p for p in series if p[0]<=t]
        after=[p for p in series if p[0]<=t+timedelta(hours=hours)]
        if not before or not after: return None
        a,b=before[-1][1], after[-1][1]
        return pct(b,a)

    def ret_before(series, t, hours):
        if not t or not series: return None
        at=[p for p in series if p[0]<=t]
        prev=[p for p in series if p[0]<=t-timedelta(hours=hours)]
        if not at or not prev: return None
        return pct(at[-1][1], prev[-1][1])

    msg_types=Counter(); rm_hits=Counter(); templates=Counter()
    ecos=Counter(); eco_dirs=defaultdict(Counter)
    valid=[]; invalid_geom=[]; coaching=[]
    btc_buckets=Counter(); eth_buckets=Counter()
    aligned_with_btc=Counter()  # trade direction vs prior BTC move

    for s in sigs:
        raw=s.get('raw') or ''
        low=raw.lower()
        parsed_ok=bool(s.get('direction') and s.get('stop') is not None and (s.get('tps') or []))
        mtype=classify_msg(raw, parsed_ok)
        msg_types[mtype]+=1
        for pat,name in RM_PHRASES:
            if re.search(pat, low, re.I):
                rm_hits[name]+=1
        if mtype=='risk_coaching':
            coaching.append({'date':s.get('date'), 'raw':raw[:400]})

        # template fingerprint for setups
        if s.get('direction'):
            parts=[]
            parts.append(s['direction'])
            parts.append('CMP' if s.get('cmp') else ('ENTRY' if s.get('entry') is not None else 'NOENTRY'))
            parts.append('SL' if s.get('stop') is not None else 'NOSL')
            parts.append('TP' if s.get('tps') else 'NOTP')
            lev=s.get('leverage')
            parts.append(f"LEV{int(lev)}" if lev else 'NOLEV')
            if 'scalp' in low: parts.append('SCALP')
            if 'night' in low: parts.append('NIGHT')
            if 'research' in low: parts.append('RESEARCH')
            if 'volatility' in low: parts.append('VOLWARN')
            templates['+'.join(parts)] += 1

        sym=s.get('symbol')
        tags=ECOSYSTEM.get(sym, ['unknown/new-listing']) if sym else []
        for t in tags:
            ecos[t]+=1
            if s.get('direction'):
                eco_dirs[t][s['direction']]+=1

        if valid_geom(s):
            posted=parse_iso(s.get('date'))
            btc_pre=ret_before(btc, posted, 24)
            eth_pre=ret_before(eth, posted, 24)
            btc_post=ret_around(btc, posted, 24)
            eth_post=ret_around(eth, posted, 24)
            def bucket(x):
                if x is None: return 'na'
                if x<=-3: return 'btc_down_gt3'
                if x< -0.5: return 'btc_down'
                if x< 0.5: return 'btc_flat'
                if x< 3: return 'btc_up'
                return 'btc_up_gt3'
            bb=bucket(btc_pre); eb=bucket(eth_pre).replace('btc','eth')
            btc_buckets[bb]+=1; eth_buckets[eb]+=1
            # alignment: SHORT into BTC weakness / LONG into BTC strength over prior 24h
            align='unknown'
            if btc_pre is not None and s['direction']:
                if s['direction']=='SHORT' and btc_pre < -0.5: align='with_btc_trend'
                elif s['direction']=='LONG' and btc_pre > 0.5: align='with_btc_trend'
                elif s['direction']=='SHORT' and btc_pre > 0.5: align='counter_btc'
                elif s['direction']=='LONG' and btc_pre < -0.5: align='counter_btc'
                else: align='btc_chop'
            aligned_with_btc[align]+=1
            risk=abs(s['entry']-s['stop']) or 1e-12
            rr=abs(s['tps'][0]-s['entry'])/risk
            valid.append({
                'id':s.get('id'),'date':s.get('date'),'symbol':sym,'direction':s.get('direction'),
                'entry':s.get('entry'),'stop':s.get('stop'),'tp':s.get('tps')[0],'tps':s.get('tps'),
                'leverage':s.get('leverage'),'cmp':s.get('cmp'),
                'stop_pct':abs(s['entry']-s['stop'])/s['entry']*100,
                'tp_pct':abs(s['tps'][0]-s['entry'])/s['entry']*100,
                'rr':rr,'ecosystem':tags,
                'btc_ret_24h_before':btc_pre,'eth_ret_24h_before':eth_pre,
                'btc_ret_24h_after':btc_post,'eth_ret_24h_after':eth_post,
                'btc_align':align,'raw':raw[:500],
                'one_r_outcome': None,
            })
        elif parsed_ok:
            invalid_geom.append({'date':s.get('date'),'symbol':sym,'direction':s.get('direction'),'raw':raw[:240]})

    # merge oneR outcomes from playbook samples if present
    habits=(play.get('habits') or {})
    one_edge=habits.get('oneR_edge')

    def avg(xs):
        xs=[x for x in xs if x is not None]
        return mean(xs) if xs else None

    # risk model inferred
    stop_pcts=[v['stop_pct'] for v in valid]
    tp_pcts=[v['tp_pct'] for v in valid]
    rrs=[v['rr'] for v in valid]
    levs=[v['leverage'] for v in valid if v.get('leverage') is not None]

    # news/macro proxy: cluster days with many signals + large BTC move
    by_day=defaultdict(list)
    for v in valid:
        day=(v['date'] or '')[:10]
        by_day[day].append(v)
    hot_days=[]
    for day,rows in sorted(by_day.items()):
        btc_moves=[r['btc_ret_24h_before'] for r in rows if r['btc_ret_24h_before'] is not None]
        hot_days.append({
            'day':day,'n_setups':len(rows),
            'avg_btc_24h_before':avg(btc_moves),
            'symbols':[r['symbol'] for r in rows],
            'dirs':dict(Counter(r['direction'] for r in rows)),
            'note': 'elevated activity' if len(rows)>=3 else 'normal'
        })

    knowledge = {
      'document_type': 'cosmas_trading_strategy_knowledge',
      'version': '2.0.0',
      'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
      'purpose': 'Paste this JSON into another AI/agent so it can recommend Bitunix USDT-M futures setups in the style of Cosmas (Telegram channel Cosmas trading/forex signals).',
      'source': {
        'telegram_chat_id': -1002004401199,
        'title': 'Cosmas trading/forex signals',
        'venue_emphasis': 'Bitunix',
        'study_corpus_messages': len(sigs),
        'valid_geometry_setups': len(valid),
        'method': [
          'Fetch recent Cosmas messages via Telethon (study session copy)',
          'Parse symbol/direction/entry/SL/TP/leverage',
          'Keep only valid geometry: LONG stop<entry<tp, SHORT stop>entry>tp',
          'Score outcomes on Bitunix candles (1h first-touch of SL/TP and +1R vs -1R)',
          'Correlate each valid setup with BTCUSDT/ETHUSDT 24h returns before/after post time',
          'Tag symbols into ecosystems (BTC/ETH/Solana/meme/AI/L2/new-listing/etc.)',
          'Mine risk-management coaching language from non-setup messages'
        ],
        'limitations': [
          'Not every Cosmas post is a clean executable setup; many are teasers, promos, or SL/TP swapped',
          'Full TP hit-rate looks poor because TPs are often extreme runners; use +1R-before-SL as structural edge metric',
          'News linkage is proxied via BTC/ETH regime and ecosystem tags; discrete headline IDs are not scraped yet',
          'Position size in USDT is rarely stated; risk is expressed via leverage + wide SL + profit-taking coaching'
        ]
      },
      'trader_persona': {
        'name': 'Cosmas',
        'style_summary': 'High-conviction Bitunix USDT perpetual calls, mostly market/CMP entries, moderate leverage (often 7x), wide invalidation stops, asymmetric moonshot take-profits, heavy alt/meme/new-listing focus, with frequent community coaching to take profits and respect manipulation/volatility.',
        'communication_template': {
          'typical_setup_format': [
            '<Symbol> /usdt',
            '<Long|Short> position',
            'Entry CMP',
            'Tp <price>',
            'Sl <price>',
            'Leverage <N>x',
            'optional: Scalping | Night research | Becareful of volatility'
          ],
          'example_setup': 'Qnt /usdt\nShort position\nEntry CMP\nTp 270\nSl 311\nLeverage 10x\nScalping\nBecareful of volatility',
          'top_message_templates': templates.most_common(15),
          'message_type_mix': dict(msg_types)
        }
      },
      'how_he_takes_trades': {
        'market': 'USDT-margined crypto futures (Bitunix-first)',
        'entry': {
          'primary': 'CMP / market order (majority of valid setups)',
          'cmp_share_valid': round(sum(1 for v in valid if v['cmp'])/len(valid), 3) if valid else None,
          'rare': 'Explicit numeric entry when not CMP'
        },
        'direction_selection': {
          'mix_valid': dict(Counter(v['direction'] for v in valid)),
          'vs_btc_24h_before': dict(aligned_with_btc),
          'interpretation': 'Not a pure BTC-beta slave: mix of with-trend and counter-BTC alt calls; many names are idiosyncratic new listings/memes.'
        },
        'symbol_universe': {
          'ecosystem_tag_counts': dict(ecos.most_common(30)),
          'ecosystem_direction_skew': {k:dict(v) for k,v in list(eco_dirs.items())[:20]},
          'frequent_valid_symbols': Counter(v['symbol'] for v in valid).most_common(20),
          'bias': 'Heavy on new listings, memes, Solana ecosystem, and occasional majors/macro (XAU) — not only BTC/ETH pairs'
        },
        'timing_tags': {
          'scalping_share_approx': round(rm_hits.get('scalping_tag',0)/max(1,len(sigs)), 3),
          'night_research_present': rm_hits.get('night_session',0)+rm_hits.get('research_tag',0),
          'volatility_warnings': rm_hits.get('volatility_warning',0)
        },
        'execution_checklist_for_agents': [
          'Parse Cosmas-like fields: symbol, direction, CMP entry, SL, TP, leverage, tags',
          'Reject invalid geometry (SL/TP on wrong side of entry)',
          'Confirm Bitunix lists the symbol as a tradeable perp',
          'Check live mark still between SL and TP with acceptable live R:R',
          'Prefer CROSS + stated leverage (or desk policy floor) and attach SL/TP immediately',
          'Plan profit-taking / BE before full moonshot TP'
        ]
      },
      'risk_management': {
        'explicit_rules_from_coaching': [
          'Always take calculated risk before execution',
          'Profit taking matters — secure profits when you see them (do not wait only for final TP)',
          'Community does not control the market; manipulation is real',
          'Be careful of volatility on scalps',
          'Fund/trade liquid venue (Bitunix messaging) — operational risk awareness'
        ],
        'phrase_frequencies': dict(rm_hits),
        'sample_coaching_messages': coaching[:12],
        'leverage_policy': {
          'observed_values': dict(Counter(levs)),
          'average': avg(levs),
          'typical': '7x most common; 10x next; occasional 20-25x on select names (e.g. metals/high conviction)',
          'agent_default': '7x to 10x for Cosmas-style suggestions; do not blindly copy 25x'
        },
        'stop_loss_policy': {
          'median_stop_distance_pct_of_entry': median(stop_pcts) if stop_pcts else None,
          'p25_stop_pct': sorted(stop_pcts)[max(0,len(stop_pcts)//4-1)] if stop_pcts else None,
          'p75_stop_pct': sorted(stop_pcts)[min(len(stop_pcts)-1, 3*len(stop_pcts)//4)] if stop_pcts else None,
          'average_stop_pct': avg(stop_pcts),
          'style': 'Structural/wide invalidation on alts — often far beyond tight scalp stops. SL is the thesis invalidation, not a 0.2% noise stop.'
        },
        'take_profit_policy': {
          'median_tp_distance_pct_of_entry': median(tp_pcts) if tp_pcts else None,
          'median_planned_rr': median(rrs) if rrs else None,
          'average_planned_rr': avg(rrs),
          'style': 'Asymmetric runners / moonshots. Final TP is aspirational; coaching emphasizes intermediate profit-taking.',
          'practical_management': [
            'Treat TP1 as a runner target (multi-R)',
            'Scale out or move stop to BE after ~+1R',
            'Do not grade the strategy only on whether final TP prints'
          ]
        },
        'position_sizing': {
          'stated_in_messages': 'Rarely specifies USDT margin; risk is implicit via leverage and stop distance',
          'agent_recommendation': 'Size so invalidation loss is a fixed small % of equity (desk often ~daredevil risk tier / ~$5 margin on small accounts), never full TP distance as assumed loss'
        },
        'structural_edge_metric': {
          'plus1R_before_minus1R': one_edge,
          'full_tp_win_rate_resolved': habits.get('full_win_rate'),
          'interpretation': 'Use +1R-before-SL hit rate as the learning/quality metric; full TP rate understates edge because TPs are extreme.'
        }
      },
      'btc_eth_major_context': {
        'method': 'For each valid Cosmas setup, measure BTCUSDT and ETHUSDT spot-perp mark proxy via 1h closes: return 24h before post and 24h after post.',
        'btc_regime_when_setups_posted_24h_before': dict(btc_buckets),
        'eth_regime_when_setups_posted_24h_before': dict(eth_buckets),
        'direction_alignment_vs_btc_24h_before': dict(aligned_with_btc),
        'avg_btc_24h_before_on_valid': avg([v['btc_ret_24h_before'] for v in valid]),
        'avg_eth_24h_before_on_valid': avg([v['eth_ret_24h_before'] for v in valid]),
        'avg_btc_24h_after_on_valid': avg([v['btc_ret_24h_after'] for v in valid]),
        'avg_eth_24h_after_on_valid': avg([v['eth_ret_24h_after'] for v in valid]),
        'ecosystem_relative_to_majors': {
          'bitcoin_related_tags': ['bitcoin','ordinals','stacks'],
          'ethereum_related_tags': ['ethereum','l2','defi','ethena','rwa','oracle'],
          'solana_related_tags': ['solana','meme','dex','launchpad'],
          'observation': 'Many calls are alt/meme/new-listing beta plays that still feel BTC risk-on/risk-off; always check BTC regime before suggesting aggressive longs in risk-off or aggressive shorts in strong BTC squeezes unless idiosyncratic catalyst.'
        },
        'agent_context_checks_before_recommend': [
          'BTC 4h/24h trend and distance to session high/low',
          'ETH/BTC relative strength if suggesting ETH-ecosystem alts',
          'Solana ecosystem strength if suggesting SOL memes/DEX tokens',
          'Avoid stacking many same-direction high-beta alts when BTC is already extended into your stop side',
          'If a major news window is known (FOMC, ETF flows, exploit, listing), require wider SL or smaller size — Cosmas volatility warnings map to this'
        ],
        'active_days': hot_days[-15:]
      },
      'recommendation_policy_for_other_agents': {
        'goal': 'Recommend futures trades that look and manage like Cosmas, without blindly copying invalid geometry or ignoring majors context.',
        'output_format': {
          'symbol': 'XYZUSDT',
          'direction': 'LONG|SHORT',
          'entry': 'CMP',
          'stop_loss': 'number',
          'take_profit': 'number',
          'leverage': '7x|10x typically',
          'tags': ['Scalping|Night research|volatility warning'],
          'rationale': ['setup structure','BTC/ETH regime','ecosystem note','risk plan'],
          'risk_plan': ['invalidates if SL trades','take partial at +1R','leave runner to TP']
        },
        'hard_filters': [
          'Valid geometry only',
          'Symbol must be tradeable on target venue',
          'Live mark between SL and TP',
          'Live planned R:R >= 1.0 for NEW suggestions (stricter than auto-follow)',
          'Leverage <= 15x unless user overrides',
          'Do not recommend if BTC crash/squeeze would immediately invalidate a crowded high-beta book'
        ],
        'soft_preferences': [
          'CMP entries',
          '7x-10x leverage',
          'Wide structural stops (roughly Cosmas median band, adapted to the coin ATR/swing)',
          'Asymmetric TP (multi-R runner) plus mandatory profit-taking plan',
          'Symbols in Cosmas-like universes: liquid alts, SOL memes, new listings, selective majors'
        ],
        'risk_management_required_in_every_call': [
          'State SL as invalidation',
          'State what +1R price is and advise secure/partial there',
          'State max account risk if SL hits',
          'Mention BTC/ETH context in one line',
          'Mention volatility/manipulation caveat in Cosmas voice when leverage>=10 or stop is tight vs ATR'
        ]
      },
      'learning_progress': {
        'phases': [
          {
            'phase': 1,
            'name': 'corpus_ingest',
            'status': 'complete',
            'detail': f'Ingested {len(sigs)} trade-like Cosmas messages into cosmas_study.json'
          },
          {
            'phase': 2,
            'name': 'geometry_and_templates',
            'status': 'complete',
            'detail': f'Identified {len(valid)} valid-geometry setups; mined message templates and coaching phrases'
          },
          {
            'phase': 3,
            'name': 'outcome_scoring',
            'status': 'complete',
            'detail': f"1h first-touch scoring; +1R-before-SL edge={one_edge}; full TP win rate={habits.get('full_win_rate')}"
          },
          {
            'phase': 4,
            'name': 'btc_eth_ecosystem_context',
            'status': 'complete',
            'detail': 'Tagged ecosystems and correlated setups with BTC/ETH 24h regimes'
          },
          {
            'phase': 5,
            'name': 'headline_news_graph',
            'status': 'in_progress',
            'detail': 'Not yet binding discrete news headlines to each signal; currently using BTC/ETH regime + volatility coaching as news/risk proxy. Next: attach dated macro/crypto headline feeds to hot_days.'
          },
          {
            'phase': 6,
            'name': 'live_scanner_recommendations',
            'status': 'pending',
            'detail': 'Scanner not enabled yet; this document is the instruction set for recommendation agents.'
          }
        ],
        'metrics_snapshot': {
          'valid_setups': len(valid),
          'cmp_share': round(sum(1 for v in valid if v['cmp'])/len(valid), 3) if valid else None,
          'median_rr': median(rrs) if rrs else None,
          'median_stop_pct': median(stop_pcts) if stop_pcts else None,
          'median_tp_pct': median(tp_pcts) if tp_pcts else None,
          'leverage_avg': avg(levs),
          'oneR_edge': one_edge,
          'btc_align': dict(aligned_with_btc)
        },
        'next_experiments': [
          'ATR-normalized stop distances vs Cosmas absolute % stops',
          'Win rate conditioned on with_btc_trend vs counter_btc',
          'Separate models for meme/new-listing vs majors',
          'Attach news headlines to hot_days and measure pre/post volatility'
        ]
      },
      'worked_examples_valid_geometry': valid[:20],
      'invalid_geometry_examples': invalid_geom[:10],
      'derived_from_playbook_habits': habits,
      'refresh': {
        'vps_files': [
          '/app/data/cosmas_study.json',
          '/app/data/cosmas_playbook.json',
          '/app/data/cosmas_strategy_knowledge.json'
        ],
        'repo_copy_path_hint': 'data/cosmas_strategy_knowledge.json',
        'scheduler': '6h Cosmas learn job should regenerate this document'
      }
    }

    OUT.write_text(json.dumps(knowledge, indent=2, default=str), encoding='utf-8')
    print(json.dumps({
      'wrote': str(OUT),
      'valid': len(valid),
      'msg_types': dict(msg_types),
      'rm_top': rm_hits.most_common(12),
      'templates_top': templates.most_common(8),
      'btc_align': dict(aligned_with_btc),
      'median_rr': median(rrs) if rrs else None,
      'median_stop': median(stop_pcts) if stop_pcts else None,
      'oneR_edge': one_edge,
      'phases_done': 4,
    }, indent=2))

if __name__ == '__main__':
    main()