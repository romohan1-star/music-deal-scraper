import argparse
import json
from pathlib import Path
from .pipeline import collect
from .report import export
from .store import Store


def main():
    p=argparse.ArgumentParser(description='Zero-AI music promotion inventory')
    p.add_argument('command',choices=['collect','export'])
    p.add_argument('--config',default='config/retailers.json')
    p.add_argument('--db',default='data/deals.sqlite')
    p.add_argument('--out',default='reports')
    p.add_argument('--run-id',help='Resume the exact run ID; omit for fresh prices')
    p.add_argument('--retailers',help='Comma-separated configured retailer IDs')
    p.add_argument('--max-pages',type=int,help='Explicit cap per collection; capped output is PARTIAL')
    args=p.parse_args()
    if args.max_pages is not None and args.max_pages<1: p.error('--max-pages must be positive')
    if args.command=='collect':
        config=json.loads(Path(args.config).read_text())
        if config.get('ai_enrichment'): p.error('AI enrichment is not implemented; keep disabled')
        ids=args.retailers.split(',') if args.retailers else None
        if ids and set(ids)-{c['id'] for c in config['retailers']}: p.error('Unknown retailer ID')
        collect(config,args.db,args.out,args.run_id,ids,args.max_pages)
    else:
        store=Store(args.db)
        run=args.run_id or store.db.execute('SELECT id FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()[0]
        export(store,run,args.out); store.close()


if __name__=='__main__': main()
