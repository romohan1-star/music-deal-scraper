import csv
import json
import re
import math
from collections import defaultdict,Counter
from pathlib import Path
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.utils import get_column_letter
from .model import match_key

PROFILE_PATH=Path(__file__).resolve().parent.parent/'config'/'relevance.json'
PROFILE=json.loads(PROFILE_PATH.read_text()) if PROFILE_PATH.exists() else {}


def relevance(o):
    text=(o['product_title']+' '+(o.get('category') or '')).lower()
    reasons=[]; score=0
    for pattern,points,why in [
        (r'fretless',100,'Fretless bass capability'),
        (r'(?:5[ -]string|five[ -]string|\bsr\d+5\b|\bray35\b).*bass|bass.*(?:5[ -]string|five[ -]string)',95,'Five-string bass'),
        (r'granular|glitch|freeze|sustain pedal|slicer|pitch.shift|whammy|harmonizer',85,'Unusual processing'),
        (r'groovebox|drum machine|sampler|sequencer',65,'Rhythm and sampling; compare against Maschine+'),
        (r'midi controller|expression pedal|foot controller|control surface',65,'Expressive or MIDI control'),
        (r'audio interface|multitrack|digital mixer|field recorder',60,'Recording/routing capability'),
        (r'ear training|practice tool|metronome',55,'Practice support'),
        (r'synthesizer|synth pedal',45,'Synthesis; overlap with JUNO/Anagram/Maschine+'),
        (r'electric guitar',25,'Guitar candidate; upgrade value requires review')]:
        if re.search(pattern,text): score=max(score,points); reasons.append(why)
    if re.search(r'cable|gig bag|cover|case for|replacement|power supply|\bstrings\b|\bpickups?\b|\bstand\b',text): score=min(score,10)
    if score==25 and o.get('promotional_price') is not None and o['promotional_price']<700:
        score=10; reasons.append('No clear upgrade over owned Sire S7 established')
    if re.search(r'distortion|overdrive|compressor|chorus|reverb|delay pedal|eq pedal|tuner pedal',text): score=min(score,5); reasons.append('Conventional effect largely covered by Anagram')
    if re.search(r'4[ -]string.*bass|mustang.*bass|ray34|marcus miller v7|waza.air|maschine\+|juno|rokit',text): score=min(score,10); reasons.append('Substantial overlap with owned gear')
    if any(re.search(pattern,text,re.I) for pattern in PROFILE.get('owned_patterns',[])):
        score=min(score,5); reasons.append('Owned capability; replacement value not established')
    if not PROFILE.get('allow_left_handed',False) and re.search(r'left[ -]hand(?:ed)?|\blefty\b',text):
        score=0; reasons.append('Left-handed instrument excluded from personal shortlist')
    if o.get('availability') in ('out_of_stock','https://schema.org/OutOfStock'): score=0; reasons.append('Out of stock')
    # Discounts only break ties; unverified compare-at cannot inflate ranking.
    return score,'; '.join(reasons)


def safe_cell(v):
    if isinstance(v,(list,dict)): v=json.dumps(v,ensure_ascii=False)
    if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')): return "'"+v
    return v


def comparisons(offers):
    groups=defaultdict(list)
    for o in offers:
        key=match_key(o)
        if key: groups[key].append(o)
    rows=[]
    for key,group in groups.items():
        if len({o['retailer'] for o in group})<2: continue
        prices=[o['verified_effective_price'] for o in group if o['verified_effective_price'] is not None and o.get('availability') not in ('out_of_stock','https://schema.org/OutOfStock')]
        for o in group:
            rows.append({'match_basis':key[0],'match_status':'candidate_review_required' if key[0]=='exact_title_candidate' else 'identifier_match_with_variant_guards','title':o['product_title'],'retailer':o['retailer'],'condition':o['condition'],'currency':o['currency'],'price':o['verified_effective_price'],'lowest_observed_in_stock_price':min(prices) if prices else None,'product_url':o['product_url']})
    return rows


def export(store,run,out):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    offers=store.current(run)
    for o in offers: o['relevance_score'],o['relevance_reason']=relevance(o)
    offers.sort(key=lambda o:(o['retailer'],o['product_title'],o['offer_id']))
    shortlist=sorted([o for o in offers if o['relevance_score']>=25 and o.get('verified_effective_price') is not None],key=lambda o:(-o['relevance_score'],-(o.get('discount_percentage') or 0),o['verified_effective_price']))[:30]
    coverage=store.coverage(run); comps=comparisons(offers)
    errors=[dict(x) for x in store.db.execute('SELECT retailer,url,stage,message,created_at FROM errors WHERE run_id=?',(run,))]
    checks=[dict(x) for x in store.db.execute('SELECT retailer,collection_url,source_url,check_type,status,detail FROM validations WHERE id IN (SELECT MAX(id) FROM validations WHERE run_id=? GROUP BY retailer,collection_url,source_url,check_type)',(run,))]
    for v in checks:
        if v['status']!='PASS': errors.append({'retailer':v['retailer'],'url':v['source_url'],'stage':v['check_type'],'message':v['status']+': '+v['detail']})
    history=[]
    for row in store.db.execute('SELECT run_id,data_json FROM observations ORDER BY collected_at,offer_id'):
        o=json.loads(row['data_json']); history.append({k:o.get(k) for k in ('offer_id','retailer','product_title','condition','currency','collected_at','promotional_price','verified_effective_price','product_url')}|{'run_id':row['run_id']})
    shortlist_view=[{'rank':i,**{k:o.get(k) for k in ('product_title','retailer','condition','verified_effective_price','currency','reference_price','reference_discount_percentage','availability','relevance_reason','product_url','data_quality_flags')}} for i,o in enumerate(shortlist,1)]
    datasets={'All Offers':offers,'Best Deals':shortlist_view,'Cross-Retailer Comparisons':comps,'Coverage Report':coverage,'Errors and Limitations':errors,'Price History':history,'Validation Evidence':checks}
    for name,rows in [('inventory',offers),('coverage',coverage),('validations',checks),('errors',errors)]:
        with (out/f'{name}.csv').open('w',newline='',encoding='utf-8-sig') as f:
            keys=list(dict.fromkeys(k for row in rows for k in row)) or ['note']
            writer=csv.DictWriter(f,fieldnames=keys); writer.writeheader()
            writer.writerows({k:safe_cell(v) for k,v in row.items()} for row in rows)
    # Public Python exporter is deliberately self-contained for unattended Actions.
    wb=Workbook(); wb.remove(wb.active)
    for name,rows in datasets.items():
        ws=wb.create_sheet(name)
        keys=list(dict.fromkeys(k for row in rows for k in row)) or ['note']
        ws.append(keys)
        for row in rows: ws.append([safe_cell(row.get(k)) for k in keys])
        if not rows: ws.append(['No qualifying records in this run; see Coverage Report.'])
        ws.freeze_panes='D2' if name=='All Offers' else 'C2'; ws.auto_filter.ref=ws.dimensions
        ws.sheet_view.showGridLines=False
        for c in ws[1]: c.fill=PatternFill('solid',fgColor='173D4B'); c.font=Font(color='FFFFFF',bold=True); c.alignment=Alignment(wrap_text=True,vertical='center')
        ws.row_dimensions[1].height=32
        for i,k in enumerate(keys,1):
            width=55 if k in ('product_title','title','reason','message','detail','relevance_reason') else 22
            if 'url' in k or k=='source_page': width=45
            if k=='data_quality_flags': width=60
            ws.column_dimensions[get_column_letter(i)].width=width
            for cells in ws.iter_rows(min_row=2,min_col=i,max_col=i):
                c=cells[0]
                if c.row%2==0: c.fill=PatternFill('solid',fgColor='EEF4F6')
                wrap=k in ('product_title','title','reason','message','detail','relevance_reason','data_quality_flags')
                c.alignment=Alignment(vertical='top',wrap_text=wrap)
                if wrap and c.value is not None:
                    height=max(18,15*math.ceil(len(str(c.value))/max(width-4,1))+3)
                    ws.row_dimensions[c.row].height=min(409,max(ws.row_dimensions[c.row].height or 18,height))
                if 'price' in k or k=='msrp':
                    if isinstance(c.value,(int,float)): c.number_format='#,##0.00'
                elif k.endswith('percentage'): c.number_format='0.00"%"'
                if k.endswith('url') and isinstance(c.value,str) and c.value.startswith('https://'): c.hyperlink=c.value; c.font=Font(color='0563C1',underline='single')
    tmp=out/'deals.tmp.xlsx'; wb.save(tmp); tmp.replace(out/'deals.xlsx')
    # Read back exported file to verify sheet names, dimensions, and inventory row count.
    check=load_workbook(out/'deals.xlsx',read_only=True,data_only=False)
    assert check.sheetnames==list(datasets)
    assert check['All Offers'].max_row==max(2,len(offers)+1)
    check.close()
    totals=Counter(o['retailer'] for o in offers)
    run_row=store.db.execute('SELECT finished_at FROM runs WHERE id=?',(run,)).fetchone()
    execution_state='FINISHED' if run_row and run_row['finished_at'] else 'CHECKPOINT'
    summary=[f'# Music retailer promotion inventory — {run}', '',f'**{len(offers):,} unique retailer offers** collected across **{len(totals)} retailers**. Product variants are separate offers. Raw inventory is preserved independently of the shortlist.','',
        'Prices are listing observations, excluding sales tax and shipping. Effective price means the directly displayed price without an unverified coupon. No purchase recommendation or historical-low claim is implied.','',
        '## Coverage','', '| Retailer | Offers | Collection statuses |','|---|---:|---|']
    summary.insert(4,f'Execution state: **{execution_state}**. Unfinished collections are retained for resumption; this does not imply retailer-wide completeness.')
    for retailer in sorted({c['retailer'] for c in coverage}):
        counts=Counter(c['status'] for c in coverage if c['retailer']==retailer)
        summary.append(f'| {retailer} | {totals[retailer]} | '+', '.join(f'{n} {s}' for s,n in counts.items())+' |')
    summary+=['','Collection completeness never establishes retailer-wide completeness. See coverage.csv for counts, request/page totals, duplicate memberships, scope and reasons.','', '## Top personal candidates (up to 30)','',
        'Ranked by capability relevance, then verified retailer-reference discount when available, then price. Unverified compare-at discounts do not improve the ranking. These are candidates to inspect, not instructions to buy.','', '| Product | Retailer | Price | Why review |','|---|---|---:|---|']
    for o in shortlist:
        title=o['product_title'].replace('|','/')
        summary.append(f"| [{title}]({o['product_url']}) | {o['retailer']} | {o.get('currency') or 'currency unverified'} {o['verified_effective_price']:,.2f} | {o['relevance_reason']} |")
    if len(shortlist)<30: summary.append(f'\nOnly {len(shortlist)} qualifying candidates were found; the list is not padded with irrelevant products.')
    summary+=['','## Comparisons and quality','',f'{len(comps)} cross-retailer comparison rows. '+('Conservative identity guards found no defensible comparisons.' if not comps else 'Exact-title matches are review candidates; offers remain separate.'),f'{len(history)} historical observations across {store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]} runs. A single run cannot establish historical selling prices.',f'Validation checks: {dict(Counter(v["status"] for v in checks))}. See validations.csv for individual evidence.','',
        'Missing condition, manufacturer identifiers, currency, stock and coupon eligibility remain missing. Shopify vendor may identify a distributor, and compare_at_price is not verified MSRP or a verified past selling price. Coupon-only and cart-only offers cannot be priced without eligibility evidence.','', '## Unresolved limitations','',
        '- Tier 1 adapters have only conservative structured-data fallbacks; live extraction/pagination cannot be certified while access is blocked.',
        '- Policy-blocked retailers are skipped without bypass. Permission or an authorized feed is needed.',
        '- Discovery covers configured seeds plus linked promotion collections; unlinked promotions and additional retailers are not exhaustively discovered.',
        '- Browser-only collections remain unsupported where HTTP extraction provides no listing data. No CAPTCHA solving, proxy rotation or stealth browser is used.',
        '- Price checks occur over a crawl window, not a single atomic snapshot. Unknown availability should be checked before purchase.',
        '- Recurring scheduling is disabled. GitHub Actions artifacts expire after 30 days unless repository settings shorten that period. Download/archive snapshots you want to retain.','']
    summary+=['','## Missing source fields','', '| Field | Offers missing the field |','|---|---:|']
    for field in ('brand','manufacturer_model_number','retailer_sku','upc','condition','currency','availability','normal_advertised_price'):
        missing=sum(o.get(field) is None for o in offers)
        summary.append(f'| {field} | {missing:,} / {len(offers):,} |')
    summary+=['','## Collection-specific gaps','']
    for c in coverage:
        if c['status']!='VERIFIED COMPLETE': summary.append(f"- {c['retailer']} — {c['url']}: **{c['status']}**. {c['reason']}")
    (out/'executive_summary.md').write_text('\n'.join(summary),encoding='utf-8')
    (out/'run_manifest.json').write_text(json.dumps({'run_id':run,'execution_state':execution_state,'unique_offers':len(offers),'retailer_offer_counts':dict(totals),'coverage_status_counts':dict(Counter(c['status'] for c in coverage)),'shortlist_count':len(shortlist),'validation_counts':dict(Counter(v['status'] for v in checks)),'ai_calls':0},indent=2))
