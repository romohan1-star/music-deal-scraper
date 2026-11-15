from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = str(value).replace(',', '').replace('$', '').strip()
        if not re.fullmatch(r'\d+(?:\.\d{1,4})?', value):
            return None
        n = Decimal(value)
        return float(n.quantize(Decimal('0.01'))) if n.is_finite() and n >= 0 else None
    except InvalidOperation:
        return None


def canonical(url):
    s = urlsplit(url)
    # Retain variant/condition and other semantic query parameters.
    query = [(k,v) for k,v in parse_qsl(s.query) if not k.startswith('utm_') and k not in ('pfm','form','gclid','fbclid')]
    return urlunsplit((s.scheme, s.netloc.lower(), s.path.rstrip('/') or '/', urlencode(sorted(query)), ''))


def condition(text, explicit=None):
    t = (explicit or text or '').lower()
    for label, pattern in [('refurbished',r'refurb'), ('open box',r'open[ -]?box'), ('demo',r'\bdemo\b'), ('used',r'\bused\b|pre[ -]owned'), ('b-stock',r'b[ -]stock')]:
        if re.search(pattern,t):
            return label
    return 'new' if explicit and re.search(r'\bnew\b', explicit.lower()) else None


@dataclass
class Offer:
    retailer: str
    product_title: str
    product_url: str
    promotion_url: str
    listing_id: str
    brand: str | None = None
    manufacturer_model_number: str | None = None
    retailer_sku: str | None = None
    upc: str | None = None
    category: str | None = None
    subcategory: str | None = None
    condition: str | None = None
    bundle_contents: str | None = None
    variant: str | None = None
    variant_id: str | None = None
    msrp: float | None = None
    normal_advertised_price: float | None = None
    reference_price: float | None = None
    reference_price_type: str | None = None
    promotional_price: float | None = None
    coupon_code: str | None = None
    coupon_terms: str | None = None
    verified_effective_price: float | None = None
    discount_percentage: float | None = None
    reference_discount_percentage: float | None = None
    availability: str | None = None
    currency: str | None = None
    collected_at: str = field(default_factory=now)
    source_page: str | None = None
    data_quality_flags: list = field(default_factory=list)

    @property
    def offer_id(self):
        return hashlib.sha256(f'{self.retailer}|{self.listing_id}|{self.condition}|{self.variant_id or self.variant}'.encode()).hexdigest()[:24]

    def finish(self):
        self.product_url = canonical(self.product_url)
        if self.condition is None:
            self.data_quality_flags.append('condition_unverified')
        if self.promotional_price is None:
            self.data_quality_flags.append('price_missing')
        if self.currency is None:
            self.data_quality_flags.append('currency_unverified')
        if self.availability is None:
            self.data_quality_flags.append('availability_unverified')
        if self.coupon_code:
            self.data_quality_flags.append('coupon_eligibility_unverified')
        else:
            # Verified here means the listing price, never checkout, tax or shipping.
            self.verified_effective_price = self.promotional_price
        price = self.promotional_price
        for ref, attr in [(self.normal_advertised_price, 'discount_percentage'), (self.reference_price, 'reference_discount_percentage')]:
            if ref and price is not None and ref > price:
                setattr(self, attr, round((1-price/ref)*100, 2))
        if self.reference_price and not self.normal_advertised_price:
            self.data_quality_flags.append('reference_price_not_verified_history')
        if self.msrp and price and self.msrp / price >= 2:
            self.data_quality_flags.append('large_msrp_gap_requires_price_history')
        self.data_quality_flags = sorted(set(self.data_quality_flags))
        return self

    def record(self):
        return {'offer_id':self.offer_id, **asdict(self)}


def title_key(text):
    # No token removal: generations, finishes, quantities and specifications survive.
    return re.sub(r'\s+', ' ', (text or '').casefold()).strip()


def match_key(o):
    # Identity is intentionally strict; retail SKUs are NOT global identifiers.
    if not o.get('condition') or not o.get('currency') or not o.get('brand'):
        return None
    guard = (o['condition'], o['currency'], title_key(o.get('variant')), title_key(o.get('bundle_contents')))
    if o.get('upc') and valid_gtin(o['upc']):
        return ('gtin', str(o['upc']), *guard)
    if o.get('manufacturer_model_number'):
        return ('mpn', title_key(o['brand']), title_key(o['manufacturer_model_number']), title_key(o['product_title']), *guard)
    return ('exact_title_candidate',title_key(o['brand']),title_key(o['product_title']),*guard)


def valid_gtin(value):
    value=str(value)
    if not value.isdigit() or len(value) not in (8,12,13,14):
        return False
    digits=[int(v) for v in value]
    return (10-sum(n*(3 if i%2==0 else 1) for i,n in enumerate(reversed(digits[:-1])))%10)%10 == digits[-1]
