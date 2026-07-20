import json, pandas as pd

def load_market(perf_path, events_path, market):
    perf = json.load(open(perf_path))
    eps = perf['episodes']
    ev = pd.read_parquet(events_path)
    ev['date'] = pd.to_datetime(ev['date']).dt.strftime('%Y-%m-%d')
    methods_by_key = ev.groupby(['symbol', 'date'])['method'].apply(list).to_dict()
    out = []
    for e in eps:
        if e['status'] not in ('won', 'lost'):
            continue
        key = (e['symbol'], e['date'])
        methods = methods_by_key.get(key, [])
        out.append({**e, 'market': market, 'methods_matched': methods})
    return out

in_ep = load_market('data/performance.json', 'scratch/events_in.parquet', 'IN')
us_ep = load_market('data/us/performance.json', 'scratch/events_us.parquet', 'US')
all_ep = in_ep + us_ep
print('IN resolved matched:', len(in_ep), 'US resolved matched:', len(us_ep))
matched = [e for e in all_ep if e['methods_matched']]
print('total with method match:', len(matched))
json.dump(all_ep, open('scratch/live_examples_with_methods.json', 'w'), indent=2, default=str)
for e in matched[:8]:
    print(e['market'], e['symbol'], e['date'], e['status'], e['signals'], '->', e['methods_matched'])
