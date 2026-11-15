"""Retailer adapters share extraction primitives, not assumed completeness."""
from dataclasses import dataclass, field
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode, urlunsplit, unquote
from bs4 import BeautifulSoup
from .model import Offer, money, condition, canonical

PROMO = re.compile(r'(?:^|[/_-])(sale|deals?|clearance|black-friday|holiday|open-box|openbox|demo|price-drops|b-stock|coupon|special-offers)(?:[/_.?\-]|$)',re.I)


@dataclass
class Page:
    offers: list = field(default_factory=list)
    next_urls: list = field(default_factory=list)
    collections: list = field(default_factory=list)
    advertised_count: int | None = None
    count_reliable: bool = False
    terminal: bool = False
    listing_ids: list = field(default_factory=list)
    reason: str = ''


def promo_links(soup,base):
    found=set()
    for a in soup.select('a[href]'):
        href=urljoin(base,a['href'])
        p=urlsplit(href)
        if re.search(r'[\s<>"\'\\]',unquote(p.path)): continue
        if p.netloc!=urlsplit(base).netloc or not PROMO.search(p.path): continue
        if '/products/' in p.path or '/store/detail/' in p.path: continue
        # Never explode every sort/filter facet; only collection-level links.
        if p.query: continue
        if not (p.path.startswith(('/collections/','/pages/','/shop/','/promotions/','/deals','/on-sale','/black-friday','/featured','/clearance'))): continue
        found.add(canonical(href))
    return sorted(found)


def json_objects(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try: yield from walk(json.loads(script.string or script.get_text()))
        except (ValueError,TypeError): continue


def walk(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values(): yield from walk(v)
    elif isinstance(obj,list):
        for v in obj: yield from walk(v)


class BaseCollector:
    def __init__(self,cfg): self.cfg=cfg; self.currency=None

    def metadata(self,text):
        soup=BeautifulSoup(text,'html.parser')
        m=re.search(r'Shopify\.currency\s*=\s*\{[^}]*"active"\s*:\s*"([A-Z]{3})"',text)
        if m: self.currency=m[1]
        magento_currency=re.search(r'"currencyConfiguration"\s*:\s*\{\s*"currency"\s*:\s*"([A-Z]{3})"',text)
        if magento_currency: self.currency=magento_currency[1]
        meta=soup.select_one('meta[property="product:price:currency"],meta[itemprop="priceCurrency"]')
        if meta: self.currency=meta.get('content')
        return soup

    def parse(self,text,url,promotion):
        soup=self.metadata(text); result=Page(collections=promo_links(soup,url))
        for p in json_objects(soup):
            if p.get('@type')!='Product' or not p.get('name') or not p.get('url'): continue
            offers=p.get('offers',[]); offers=offers if isinstance(offers,list) else [offers]
            for i,price in enumerate(offers):
                if not isinstance(price,dict) or price.get('@type')=='AggregateOffer': continue
                brand=p.get('brand'); brand=brand.get('name') if isinstance(brand,dict) else brand
                product_url=urljoin(url,p['url']); sku=str(p.get('sku') or product_url)
                o=Offer(self.cfg['id'],p['name'],product_url,promotion,sku,brand=brand,manufacturer_model_number=p.get('mpn'),retailer_sku=p.get('sku'),upc=p.get('gtin12') or p.get('gtin13'),category=p.get('category'),condition=condition(p['name'],str(price.get('itemCondition','')).rsplit('/',1)[-1].replace('Condition','')),promotional_price=money(price.get('price')),currency=price.get('priceCurrency'),availability=price.get('availability'),source_page=url)
                result.offers.append(o.finish()); result.listing_ids.append(sku)
        nexts=soup.select('a[rel="next"],.pagination a.next,.pages a.next')
        result.next_urls=sorted(set(urljoin(url,a['href']) for a in nexts if a.get('href')))
        result.terminal=bool(result.offers) and not result.next_urls
        result.reason='Generic structured-data fallback: pagination/count not independently validated'
        return result


class ShopifyCollector(BaseCollector):
    def parse(self,text,url,promotion):
        if '/products.json' not in url:
            soup=self.metadata(text)
            r=Page(collections=promo_links(soup,url))
            if re.fullmatch(r'/collections/[^/]+/?',urlsplit(url).path):
                r.next_urls=[promotion.rstrip('/')+'/products.json?limit=250&page=1']
            else:
                r.reason='Promotion landing page; child collections enumerated'
            # Only a clearly labelled collection count is a reliable denominator.
            for el in soup.select('#ProductCountDesktop,#ProductCount,.collection-products-count,.collection__products-count'):
                m=re.fullmatch(r'\s*([\d,]+)\s+(?:products|items)\s*',el.get_text(' ',strip=True),re.I)
                if m: r.advertised_count=int(m[1].replace(',','')); r.count_reliable=True; break
            return r
        payload=json.loads(text)
        if 'products' not in payload or not isinstance(payload['products'],list): raise ValueError('Shopify products array absent')
        r=Page(); products=payload['products']
        for p in products:
            if not p.get('title') or not p.get('id'): raise ValueError('Product missing title/id')
            r.listing_ids.append(str(p['id']))
            tags=p.get('tags',[]); tags=tags if isinstance(tags,list) else tags.split(',')
            explicit=next((str(t).split(':')[-1].strip() for t in tags if re.fullmatch(r'(?:condition[:_ -]*)?(new|used|demo|open[ -]box|refurbished|b-stock)',str(t),re.I)),None)
            for v in p.get('variants',[]):
                variant=v.get('title'); variant=None if variant=='Default Title' else variant
                title=p['title']
                o=Offer(self.cfg['id'],title,self.cfg['base']+'/products/'+p['handle']+'?variant='+str(v['id']),promotion,str(p['id']),brand=p.get('vendor'),retailer_sku=v.get('sku') or None,upc=v.get('barcode') or None,category=p.get('product_type') or None,condition=condition(title+' '+(variant or ''),explicit),variant=variant,variant_id=str(v['id']),promotional_price=money(v.get('price')),reference_price=money(v.get('compare_at_price')),reference_price_type='Shopify compare_at_price (basis unverified)',availability=('in_stock' if v['available'] else 'out_of_stock') if isinstance(v.get('available'),bool) else None,currency=self.currency,source_page=url)
                # IDs identify variants within a retailer; never imply shared variant identity.
                o.data_quality_flags.extend(['vendor_may_be_distributor','variant_identity_retailer_specific'])
                if re.search(r'\bbundle\b|\bpack\b|\bwith\b',title,re.I):
                    o.bundle_contents=title; o.data_quality_flags.append('bundle_contents_title_only')
                r.offers.append(o.finish())
        if products:
            q=parse_qs(urlsplit(url).query); page=int(q.get('page',['1'])[0])+1
            # Fetch until an explicit empty page, even after a short page.
            r.next_urls=[promotion.rstrip('/')+f'/products.json?limit=250&page={page}']
        else: r.terminal=True
        return r


class MagentoCollector(BaseCollector):
    def parse(self,text,url,promotion):
        soup=self.metadata(text); r=Page(collections=promo_links(soup,url))
        cards=soup.select('li.product-item,li.item.product,div.product-item-info')
        # Prefer outer listing cards; avoid nested duplicate extraction.
        cards=[c for c in cards if not any(p in cards for p in c.parents)]
        for c in cards:
            a=c.select_one('.product-item-link,.product-name a,.product-item-name a')
            if not a:
                name=c.select_one('.product-item-name,.product-name')
                a=name.find_parent('a') if name else None
            if not a or not a.get('href'): continue
            title=a.get_text(' ',strip=True)
            final=c.select_one('[data-price-type="finalPrice"]')
            old=c.select_one('[data-price-type="oldPrice"]')
            box=c.select_one('[data-product-id]')
            product_url=urljoin(url,a['href']); pid=box.get('data-product-id') if box else canonical(product_url)
            price=money(final.get('data-price-amount')) if final else None
            if price is None:
                price_el=c.select_one('.special-price .price,.regular-price .price,.price')
                price=money(price_el.get_text(strip=True)) if price_el else None
            stock=c.select_one('.stock')
            stock_text=stock.get_text(' ',strip=True) if stock else None
            availability='out_of_stock' if stock_text and re.search('out of stock|unavailable',stock_text,re.I) else ('in_stock' if stock_text and re.search('in stock',stock_text,re.I) else None)
            analytics={}
            for tag in c.select('[onclick]'):
                # Parse literal JSON passed to dataLayer.push; never execute JavaScript.
                for match in re.finditer(r'dataLayer\.push\((\{)',tag['onclick']):
                    try:
                        payload,_=json.JSONDecoder().raw_decode(tag['onclick'][match.start(1):])
                        for item in walk(payload):
                            if str(item.get('item_id',''))==str(pid): analytics=item
                    except ValueError: continue
            if analytics.get('item_stock_status'):
                value=analytics['item_stock_status'].lower()
                availability='in_stock' if value=='in stock' else ('out_of_stock' if value=='out of stock' else availability)
            o=Offer(self.cfg['id'],title,product_url,promotion,str(pid),brand=analytics.get('item_brand'),category=analytics.get('item_category'),condition=condition(title),promotional_price=price,reference_price=money(old.get('data-price-amount')) if old else None,reference_price_type='Retailer struck-through price (history unverified)',availability=availability,currency=analytics.get('currency') or self.currency,source_page=url)
            # An add-to-cart button alone is not a stock statement.
            if re.search(r'\bbundle\b|\bpack\b',title,re.I): o.bundle_contents=title; o.data_quality_flags.append('bundle_contents_title_only')
            r.offers.append(o.finish()); r.listing_ids.append(str(pid))
        amounts=soup.select('.toolbar-amount')
        if amounts:
            count_text=amounts[0].get_text(' ',strip=True)
            m=re.search(r'(?:of\s+)?([\d,]+)\s+(?:items|products)',count_text,re.I)
            if m: r.advertised_count=int(m[1].replace(',','')); r.count_reliable=True
        nexts=soup.select('.pages a.next,a[rel="next"]')
        r.next_urls=sorted(set(urljoin(url,a['href']) for a in nexts if a.get('href')))
        # Discover, rather than assume, an offered page size. Start again at page
        # one so raising the size never skips products between old page 1 and 2.
        q=parse_qs(urlsplit(url).query)
        sizes=[int(o['value']) for o in soup.select('select.limiter-options option[value]') if o['value'].isdigit() and int(o['value'])<=100]
        if sizes and 'product_list_limit' not in q and 'p' not in q and r.next_urls:
            parts=urlsplit(url); q['product_list_limit']=[str(max(sizes))]
            r.next_urls=[urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(q,doseq=True),''))]
        r.terminal=bool(cards) and not r.next_urls
        if not cards: r.reason='Landing page or unsupported product markup; child promotion links discovered'
        return r


# Separate adapter identities preserve retailer-specific extension/test boundaries.
# These have no verified live parsing coverage while access is blocked.
class GuitarCenterCollector(BaseCollector): pass
class SweetwaterCollector(BaseCollector): pass
class MusiciansFriendCollector(BaseCollector): pass
class ZZoundsCollector(BaseCollector): pass

ADAPTERS={'shopify':ShopifyCollector,'magento':MagentoCollector,'guitar_center':GuitarCenterCollector,'sweetwater':SweetwaterCollector,'musicians_friend':MusiciansFriendCollector,'zzounds':ZZoundsCollector,'generic':BaseCollector}
