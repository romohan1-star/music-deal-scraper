"""Independently reconcile the exported snapshot against SQLite (no network)."""
import argparse
import csv
import json
import sqlite3
from pathlib import Path
from openpyxl import load_workbook


def validate(db_path,reports):
    reports=Path(reports)
    manifest=json.loads((reports/'run_manifest.json').read_text())
    run=manifest['run_id']
    db=sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro',uri=True)
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    expected={r[0]:json.loads(r[1]) for r in db.execute('SELECT offer_id,data_json FROM observations WHERE run_id=?',(run,))}
    with (reports/'inventory.csv').open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    assert len(rows)==len(expected)==manifest['unique_offers']
    assert {r['offer_id'] for r in rows}==set(expected)
    for row in rows:
        actual=expected[row['offer_id']]
        for key in ('promotional_price','normal_advertised_price','msrp','verified_effective_price'):
            assert (float(row[key]) if row[key] else None)==actual.get(key),(row['offer_id'],key)
        assert row['product_url']==actual['product_url']
    wb=load_workbook(reports/'deals.xlsx',read_only=True,data_only=False)
    required=['All Offers','Best Deals','Cross-Retailer Comparisons','Coverage Report','Errors and Limitations','Price History']
    assert all(s in wb.sheetnames for s in required)
    ws=wb['All Offers']; excel=list(ws.iter_rows(values_only=True)); header=list(excel[0])
    if expected:
        assert len(excel)-1==len(expected)
        for values in excel[1:]:
            row=dict(zip(header,values)); o=expected[row['offer_id']]
            assert row['promotional_price']==o['promotional_price']
            assert row['verified_effective_price']==o['verified_effective_price']
    for sheet in wb:
        for row in sheet:
            assert all(c.data_type!='f' for c in row),'Untrusted formula in deterministic export'
    with (reports/'coverage.csv').open(encoding='utf-8-sig') as f: coverage=list(csv.DictReader(f))
    for c in coverage:
        if c['status']=='VERIFIED COMPLETE':
            assert c['count_reliable']=='1' and c['terminal']=='1'
            assert int(c['unique_listings'])==int(c['advertised_count'])
            assert int(c['pages_failed'])==0
    result={'run_id':run,'sqlite_integrity':'ok','csv_rows':len(rows),'excel_offer_rows':len(expected),'workbook_sheets':wb.sheetnames,'verified_complete_collections':sum(c['status']=='VERIFIED COMPLETE' for c in coverage),'ai_calls':0,'status':'PASS'}
    wb.close(); db.close()
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--db',default='data/deals.sqlite'); p.add_argument('--reports',default='reports')
    args=p.parse_args(); print(json.dumps(validate(args.db,args.reports),indent=2))
