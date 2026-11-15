import json
from pathlib import Path
import pytest
import httpx
from music_deals.model import Offer,money,condition,match_key,canonical,valid_gtin
from music_deals.http import Robots,Client,AccessBlocked
from music_deals.store import Store
from music_deals.collectors import ShopifyCollector,MagentoCollector
from music_deals.pipeline import crawl_collection
from music_deals.report import export,relevance,safe_cell,select_shortlist,comparisons


def test_money_missing_not_zero():
    assert money(None) is None
    assert money('') is None
    assert money('Call for price') is None
    assert money('$1,234.50')==1234.5
    assert money('from $50') is None
    assert money('NaN') is None
    assert money('-5') is None
    assert money(0)==0


def test_no_msrp_history_or_coupon_assumption():
    o=Offer('r','Bass','https://r/p','https://r/s','1',msrp=1000,reference_price=1000,promotional_price=400,coupon_code='SAVE').finish()
    assert o.normal_advertised_price is None
    assert o.discount_percentage is None
    assert o.verified_effective_price is None
    assert o.reference_discount_percentage==60
    assert 'large_msrp_gap_requires_price_history' in o.data_quality_flags


def test_robots_wildcards_specific_agents_and_allow():
    robots=Robots('User-agent: *\nDisallow: /collections/*sort_by*\nDisallow: /private\nAllow: /private/public$\nCrawl-delay: 3\nUser-agent: OtherBot\nDisallow: /')
    assert not robots.allowed('https://r/collections/sale?sort_by=price')
    assert robots.allowed('https://r/collections/sale?page=2')
    assert robots.allowed('https://r/private/public')
    assert not robots.allowed('https://r/private/public/foo')
    assert robots.delay==3
    assert not Robots('User-agent: *\nAllow: /\nUser-agent: MusicDealInventory\nDisallow: /').allowed('https://r/a')
    assert not Robots('User-agent: Music\nAllow: /specific\nUser-agent: MusicDealInventory\nDisallow: /').allowed('https://r/specific')


def test_access_block_stops_domain(tmp_path):
    calls=[]
    def response(req):
        calls.append(str(req.url))
        return httpx.Response(200,text='User-agent: *\nAllow: /') if req.url.path=='/robots.txt' else httpx.Response(403,text='denied')
    c=Client(tmp_path,delay=0); c.client=httpx.Client(transport=httpx.MockTransport(response))
    with pytest.raises(AccessBlocked): c.get('https://r/first')
    with pytest.raises(AccessBlocked): c.get('https://r/second')
    assert len(calls)==2


def test_robots_unavailable_fails_closed(tmp_path):
    c=Client(tmp_path,delay=0); c.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(403)))
    with pytest.raises(AccessBlocked,match='robots'): c.get('https://r/p')


def test_conservative_matching():
    o={'brand':'Fender','product_title':'Bass V2 Red','condition':'new','currency':'USD','variant':None,'bundle_contents':None}
    assert match_key(o)!=match_key(o|{'product_title':'Bass V1 Red'})
    assert match_key(o)!=match_key(o|{'product_title':'Bass V2 Blue'})
    assert match_key(o)!=match_key(o|{'condition':'used'})
    assert match_key(o)!=match_key(o|{'bundle_contents':'Case'})
    assert match_key(o|{'condition':None}) is None
    assert valid_gtin('036000291452')
    assert not valid_gtin('036000291453')


def test_canonical_preserves_condition_variant():
    assert canonical('https://r/p?variant=12&utm_source=ad')=='https://r/p?variant=12'
    assert canonical('https://r/p?condition=used')!='https://r/p?condition=new'


def test_shopify_variants_short_page_and_missing_condition():
    adapter=ShopifyCollector({'id':'r','base':'https://r'})
    adapter.metadata('<script>Shopify.currency = {"active":"USD"};</script>')
    product={'id':1,'title':'Bass','handle':'bass','vendor':'Brand','tags':[],'variants':[{'id':10,'title':'Red','price':'100.00','compare_at_price':'200','available':True},{'id':11,'title':'Blue','price':'110','available':False}]}
    page=adapter.parse(json.dumps({'products':[product]}),'https://r/collections/sale/products.json?limit=250&page=1','https://r/collections/sale')
    assert len(page.offers)==2
    assert page.offers[0].offer_id!=page.offers[1].offer_id
    assert page.offers[0].condition is None
    assert page.offers[0].currency=='USD'
    assert page.offers[0].normal_advertised_price is None
    assert not page.terminal and page.next_urls[0].endswith('page=2')
    assert adapter.parse('{"products":[]}',page.next_urls[0],'https://r/collections/sale').terminal


def test_magento_count_price_and_stock():
    a=MagentoCollector({'id':'r','base':'https://r'})
    html='''<p class="toolbar-amount">Items 1-24 of 93 items</p><li class="product-item"><a class="product-item-link" href="/bass">Bass (Demo)</a><div data-product-id="42"></div><span data-price-type="finalPrice" data-price-amount="125.50"></span><span data-price-type="oldPrice" data-price-amount="200"></span><div class="stock unavailable">Out of stock</div></li><div class="pages"><a class="next" href="?p=2">Next</a></div>'''
    p=a.parse(html,'https://r/sale','https://r/sale')
    assert p.advertised_count==93 and p.count_reliable
    assert p.offers[0].promotional_price==125.5
    assert p.offers[0].condition=='demo'
    assert p.offers[0].availability=='out_of_stock'
    assert p.offers[0].normal_advertised_price is None
    assert len(p.next_urls)==1


def test_store_preserves_history_and_memberships(tmp_path):
    s=Store(tmp_path/'db.sqlite'); o=Offer('r','Bass','https://r/p','https://r/s','1',promotional_price=100).finish()
    s.collection('a','r','https://r/s')
    for run,collection in [('a','https://r/s'),('a','https://r/s2'),('b','https://r/s')]:
        s.save_page(run,'r',collection,collection,1,{},[o],[])
    assert s.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==2
    assert s.db.execute('SELECT COUNT(*) FROM membership').fetchone()[0]==3
    assert s.coverage('a')[0]['duplicate_offers_across_collections']==1
    assert len(s.current('a'))==1
    s.close()


class FixtureClient:
    def __init__(self,pages,tmp): self.pages=pages; self.calls=[]; self.last_result={}; self.tmp=tmp
    def get(self,url):
        self.calls.append(url)
        text=self.pages[url]; path=self.tmp/str(len(self.calls)); path.write_text(text)
        self.last_result={'text':text,'evidence_path':str(path),'status':200,'fetched_at':'2026-11-15T00:00:00+00:00'}
        return self.last_result
    def close(self): pass


def test_repeated_page_is_partial_and_data_retained(tmp_path):
    s=Store(tmp_path/'db'); base='https://r/collections/sale'; cfg={'id':'r','base':'https://r'}
    p=json.dumps({'products':[{'id':1,'title':'Bass','handle':'bass','variants':[{'id':10,'price':'100'}]}]})
    c=FixtureClient({base:'<html/>',base+'/products.json?limit=250&page=1':p,base+'/products.json?limit=250&page=2':p},tmp_path)
    s.collection('run','r',base)
    crawl_collection(s,c,'run',cfg,ShopifyCollector(cfg),base,20,0)
    assert s.coverage('run')[0]['status']=='PARTIAL'
    assert len(s.current('run'))==1
    s.close()


def test_resume_uses_completed_pages_and_visits_unfinished(tmp_path):
    s=Store(tmp_path/'db'); base='https://r/collections/sale'; cfg={'id':'r','base':'https://r'}
    p=json.dumps({'products':[{'id':1,'title':'Bass','handle':'bass','variants':[{'id':10,'price':'100'}]}]})
    pages={base:'<html/>',base+'/products.json?limit=250&page=1':p,base+'/products.json?limit=250&page=2':'{"products":[]}'}
    c=FixtureClient(pages,tmp_path); s.collection('run','r',base)
    crawl_collection(s,c,'run',cfg,ShopifyCollector(cfg),base,2,0)
    assert s.coverage('run')[0]['status']=='PARTIAL'
    c.calls=[]
    crawl_collection(s,c,'run',cfg,ShopifyCollector(cfg),base,10,0)
    # Completed listing pages can be re-requested only for explicit boundary validation.
    assert base not in c.calls
    assert pages.keys()-set(c.calls)=={base}
    assert len(s.current('run'))==1
    assert s.coverage('run')[0]['status']=='UNVERIFIABLE'
    s.close()


def test_report_valid_empty_workbook_and_csv_injection(tmp_path):
    s=Store(tmp_path/'db'); s.collection('r','retailer','https://r/s')
    export(s,'r',tmp_path/'reports')
    assert (tmp_path/'reports/deals.xlsx').exists()
    assert json.loads((tmp_path/'reports/run_manifest.json').read_text())['unique_offers']==0
    assert safe_cell('=HYPERLINK("bad")').startswith("'")
    s.close()


def test_relevance_does_not_reward_redundant_or_unavailable():
    assert relevance({'product_title':'Bass 5-string','category':'bass','availability':'in_stock'})[0]==95
    assert relevance({'product_title':'Overdrive pedal','category':None})[0]<=5
    assert relevance({'product_title':'Fretless bass','category':None,'availability':'out_of_stock'})[0]==0
    assert relevance({'product_title':'Left Handed 5-String Bass','category':None,'availability':'in_stock'})[0]==0
    assert relevance({'product_title':'Electro-Harmonix Pico Deep Freeze Pedal','category':None})[0]<=5


def test_magento_page_size_does_not_skip_items():
    a=MagentoCollector({'id':'r','base':'https://r'})
    html='''<li class="product-item"><a class="product-item-link" href="/p">Bass</a><div data-product-id="1"></div><span data-price-type="finalPrice" data-price-amount="100"></span></li><select class="limiter-options"><option value="32">32</option><option value="80">80</option><option value="all">All</option></select><div class="pages"><a class="next" href="?p=2">Next</a></div>'''
    p=a.parse(html,'https://r/sale','https://r/sale')
    assert p.next_urls==['https://r/sale?product_list_limit=80']
    assert 'p=2' not in p.next_urls[0]


def test_explicit_analytics_currency_and_stock():
    a=MagentoCollector({'id':'r','base':'https://r'})
    html='''<li class="product-item"><a class="product-item-link" href="/p">Bass</a><div data-product-id="1"></div><a onclick='window.dataLayer.push({"ecommerce":{"items":[{"item_id":"1","currency":"USD","item_stock_status":"In stock","item_category":"Bass"}]}});'></a><span data-price-type="finalPrice" data-price-amount="100"></span></li>'''
    o=a.parse(html,'https://r/sale','https://r/sale').offers[0]
    assert o.currency=='USD' and o.availability=='in_stock' and o.category=='Bass'


def test_coverage_cannot_exceed_one_hundred(tmp_path):
    s=Store(tmp_path/'db'); s.collection('r','s','https://s/c')
    with s.db: s.db.execute('UPDATE collections SET advertised_count=1,count_reliable=1')
    os=[Offer('s','P'+str(i),'https://s/'+str(i),'https://s/c',str(i)).finish() for i in range(2)]
    s.save_page('r','s','https://s/c','https://s/c',1,{},os,[])
    assert s.coverage('r')[0]['coverage_percentage'] is None
    s.close()


def test_end_to_end_isolates_failure_and_exports(tmp_path,monkeypatch):
    from music_deals import pipeline
    html='''<p class="toolbar-amount">1 items</p><li class="product-item"><a class="product-item-link" href="/p">Fretless Bass (Demo)</a><div data-product-id="1"></div><span data-price-type="finalPrice" data-price-amount="799"></span></li>'''
    client=FixtureClient({'https://good/':'<html/>','https://good/sale':html},tmp_path)
    monkeypatch.setattr(pipeline,'Client',lambda *a,**k:client)
    cfg={'sample_products':0,'retailers':[
        {'id':'good','tier':1,'base':'https://good','adapter':'magento','policy':'reviewed','seeds':['/sale']},
        {'id':'bad','tier':2,'base':'https://bad','adapter':'magento','policy':'reviewed','seeds':['/sale']} ]}
    pipeline.collect(cfg,str(tmp_path/'db'),str(tmp_path/'reports'),'test-run')
    s=Store(tmp_path/'db')
    assert len(s.current('test-run'))==1
    statuses={r['retailer']:r['status'] for r in s.coverage('test-run')}
    assert statuses=={'good':'VERIFIED COMPLETE','bad':'PARTIAL'}
    manifest=json.loads((tmp_path/'reports/run_manifest.json').read_text())
    assert manifest['unique_offers']==1 and manifest['ai_calls']==0
    s.close()
    from scripts.validate_outputs import validate
    assert validate(tmp_path/'db',tmp_path/'reports')['status']=='PASS'


def test_detail_validation_cannot_borrow_related_product_price(tmp_path):
    from music_deals.pipeline import validate_samples
    s=Store(tmp_path/'db')
    o=Offer('r','Target','https://r/target','https://r/s','1',promotional_price=100)
    html='''<script type="application/ld+json">[{"@type":"Product","name":"Target","offers":{"@type":"Offer","price":"200"}},{"@type":"Product","name":"Related","offers":{"@type":"Offer","price":"100"}}]</script>'''
    c=FixtureClient({'https://r/target':html},tmp_path)
    validate_samples(s,c,'run',{'id':'r'},'https://r/s',[o],1)
    assert s.db.execute('SELECT status FROM validations').fetchone()[0]=='FAIL'
    s.close()


def test_unexpected_empty_pagination_page_is_failed(tmp_path):
    s=Store(tmp_path/'db'); base='https://r/sale'; cfg={'id':'r','base':'https://r'}
    html='''<p class="toolbar-amount">2 items</p><li class="product-item"><a class="product-item-link" href="/p">Bass</a><div data-product-id="1"></div><span data-price-type="finalPrice" data-price-amount="100"></span></li><div class="pages"><a class="next" href="?p=2">Next</a></div>'''
    client=FixtureClient({base:html,base+'?p=2':'<html>Unexpected empty page</html>'},tmp_path)
    s.collection('run','r',base)
    crawl_collection(s,client,'run',cfg,MagentoCollector(cfg),base,10,0)
    coverage=s.coverage('run')[0]
    assert coverage['status']=='PARTIAL' and coverage['pages_failed']==1
    assert coverage['unique_listings']==1 and coverage['advertised_count']==2
    s.close()


def test_shortlist_covers_interests_without_padding_accessories():
    offers=[]
    for i in range(50):
        o={'offer_id':str(i),'product_title':('Premium 5-String Bass' if i<40 else 'Granular pedal'),'category':None,'verified_effective_price':100+i,'promotional_price':100+i,'discount_percentage':None}
        o['relevance_score'],o['relevance_reason']=relevance(o); offers.append(o)
    chosen=select_shortlist(offers)
    assert len(chosen)==30
    assert sum('Granular' in o['product_title'] for o in chosen)>=8
    assert relevance({'product_title':'Fender Bass Strings (5-Strings)','category':None})[0]<25


def test_comparison_does_not_call_unknown_stock_in_stock():
    common={'brand':'Brand','product_title':'Same Model Red','condition':'new','currency':'USD','product_url':'https://r/p','variant':None,'bundle_contents':None}
    rows=comparisons([common|{'retailer':'a','verified_effective_price':50,'availability':None},common|{'retailer':'b','verified_effective_price':100,'availability':'in_stock'}])
    assert len(rows)==2 and all(r['lowest_observed_in_stock_price']==100 for r in rows)


def test_real_shortlist_false_positives_are_demoted():
    for title,price in [('Electro-Harmonix 22500 Looper Foot Controller',35.99),('Fender Link I O Audio Interface',89.95),('Electro-Harmonix Freeze Sound Retainer Pedal',129.99),('Soundiron Glitch Hero',49.5),('Blackstar Beam Control Wireless Foot Controller for Beam Amps',74.99),('Yamaha AC1R Acoustic-Electric Guitar',701.95),('Focusrite Scarlett Solo Studio Audio Interface',252.95)]:
        assert relevance({'product_title':title,'category':None,'promotional_price':price})[0]<25
    assert relevance({'product_title':'Sire Marcus Miller V7 4-String Fretless Bass','category':None,'promotional_price':700})[0]>=95
