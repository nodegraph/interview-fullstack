"""Bounded, exact RxNav fallback for names missing from the local catalog.

Only extracted names are sent, never the surrounding note. Ambiguous results
and multi-ingredient brands remain unresolved. No catalog writes happen here.
"""

from collections import OrderedDict
from threading import Lock
from time import monotonic

import httpx

from app.config import get_settings
from app.medication_matching import SHORTHAND, normalize_name
from app.schemas import MedicationOut

MAX_REQUESTS = 10
MAX_SECONDS = 8.0
CACHE_SIZE = 512
_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = Lock()


class _BudgetExhausted(Exception):
    pass


class RxNavFallback:
    def __init__(self, client: httpx.Client | None = None):
        self.base = get_settings().rxnav_base_url.rstrip('/')
        self.client = client
        self.owns_client = client is None
        self.remaining = MAX_REQUESTS
        self.deadline = monotonic() + MAX_SECONDS

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.owns_client and self.client is not None:
            self.client.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        seconds = self.deadline - monotonic()
        if self.remaining <= 0 or seconds <= 0:
            raise _BudgetExhausted()
        self.remaining -= 1
        if self.client is None:
            self.client = httpx.Client()
        response = self.client.get(
            self.base + path, params=params, timeout=min(3.0, seconds),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError('Invalid RxNav response')
        return payload

    def _properties(self, rxcui: str) -> dict:
        props = self._get(f'/rxcui/{rxcui}/properties.json').get('properties') or {}
        if props.get('rxcui') != rxcui or not props.get('name'):
            raise ValueError('Invalid RxNorm properties')
        return props

    def _resolve(self, query: str) -> tuple[MedicationOut, str] | None:
        # Exact search avoids treating the closest remote string as a verified
        # correction. Known shorthand is expanded before reaching this method.
        ids = self._get('/rxcui.json', {'name': query, 'search': 0, 'allsrc': 0}).get('idGroup', {}).get('rxnormId') or []
        if not isinstance(ids, list) or len(set(ids)) != 1:
            return None
        rxcui = str(ids[0])
        if not rxcui.isdigit():
            return None
        props = self._properties(rxcui)
        original = props
        if props.get('tty') in {'BN', 'PIN'}:
            groups = self._get(f'/rxcui/{rxcui}/related.json', {'tty': 'IN'}).get('relatedGroup', {}).get('conceptGroup') or []
            ingredients = {
                c['rxcui'] for group in groups if group.get('tty') == 'IN'
                for c in (group.get('conceptProperties') or [])
                if c.get('rxcui') and c.get('tty', 'IN') == 'IN'
            }
            if len(ingredients) != 1:
                return None
            rxcui = ingredients.pop()
            if not rxcui.isdigit():
                return None
            props = self._properties(rxcui)
        if props.get('tty') != 'IN':
            return None
        kind = ('brand' if original.get('tty') == 'BN' else
                'exact' if normalize_name(props['name']) == query else 'synonym')
        medication = MedicationOut(
            rxcui=rxcui, name=props['name'], tty='IN',
            synonym=props.get('synonym') or None,
            brand_names=[original['name']] if kind == 'brand' else [],
            source='rxnav',
        )
        return medication, kind

    def resolve(self, written: str) -> dict | None:
        raw = normalize_name(written)
        query = SHORTHAND.get(raw, raw)
        if len(query) < 5 or len(query) > 150:
            return None
        key = (self.base, query)
        now = monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] > now:
                _CACHE.move_to_end(key)
                result = cached[1]
            else:
                _CACHE.pop(key, None)
                cached = None
        if cached is None:
            try:
                result = self._resolve(query)
            except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, _BudgetExhausted):
                # Outages and exhausted budgets are not cached as "not found".
                return None
            with _CACHE_LOCK:
                _CACHE[key] = (monotonic() + (3600 if result else 60), result)
                _CACHE.move_to_end(key)
                while len(_CACHE) > CACHE_SIZE:
                    _CACHE.popitem(last=False)
        if result is None:
            return None
        medication, kind = result
        return {
            'matched': True,
            'match_type': 'shorthand' if raw in SHORTHAND else kind,
            'correction': None,
            'medication': medication.model_copy(deep=True),
        }
