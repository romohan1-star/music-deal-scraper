"""Recheck saved random product samples with current parsing rules, without HTTP."""
import argparse
from dataclasses import fields
import json
from pathlib import Path
import re
from music_deals.model import Offer
from music_deals.pipeline import validate_samples
from music_deals.store import Store


def audit(db_path,run):
    store=Store(db_path)
    offers={o['product_url']:o for o in store.current(run)}
    samples=store.db.execute("SELECT * FROM validations WHERE id IN (SELECT MAX(id) FROM validations WHERE run_id=? AND check_type='random_product' GROUP BY retailer,collection_url,source_url)",(run,)).fetchall()
    checked=0; skipped=0
    allowed={f.name for f in fields(Offer)}
    for sample in samples:
        match=re.search(r'evidence=(.+)$',sample['detail'])
        record=offers.get(sample['source_url'])
        if not match or not record or not Path(match[1]).is_file(): skipped+=1; continue
        path=match[1]
        class ArchivedClient:
            def get(self,url):
                assert url==sample['source_url']
                return {'text':Path(path).read_text(),'evidence_path':path}
        offer=Offer(**{k:v for k,v in record.items() if k in allowed})
        validate_samples(store,ArchivedClient(),run,{'id':sample['retailer']},sample['collection_url'],[offer],1)
        checked+=1
    store.close()
    return {'rechecked_saved_samples':checked,'samples_without_saved_detail':skipped,'new_network_requests':0,'note':'Saved detail observations are checked against the selected run; this is not a fresh live price check.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--db',default='data/deals.sqlite'); p.add_argument('--run-id',required=True)
    args=p.parse_args(); print(json.dumps(audit(args.db,args.run_id),indent=2))
