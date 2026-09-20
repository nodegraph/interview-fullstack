import httpx
import pytest

from app import medication_fallback as fallback


@pytest.fixture(autouse=True)
def clear_cache():
    fallback._CACHE.clear()
    yield
    fallback._CACHE.clear()


def client_for(responses):
    requests = []

    def handle(request):
        requests.append(request)
        value = responses.get(request.url.path)
        if callable(value):
            value = value(request)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            return httpx.Response(value)
        return httpx.Response(200, json=value)

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def ingredient(rxcui='6851', name='methotrexate'):
    return {'properties': {'rxcui': rxcui, 'name': name, 'tty': 'IN'}}


def test_shorthand_lookup_is_verified_and_cached_without_catalog_writes():
    client, requests = client_for({
        '/REST/rxcui.json': {'idGroup': {'rxnormId': ['6851']}},
        '/REST/rxcui/6851/properties.json': ingredient(),
    })
    with client, fallback.RxNavFallback(client) as resolver:
        result = resolver.resolve('MTX')
        assert result['medication'].rxcui == '6851'
        assert result['medication'].source == 'rxnav'
        assert result['match_type'] == 'shorthand'
        assert result['correction'] is None
        assert requests[0].url.params['name'] == 'methotrexate'
        assert requests[0].url.params['search'] == '0'
        result['medication'].name = 'changed'
        assert resolver.resolve('methotrexate')['medication'].name == 'methotrexate'
        assert len(requests) == 2


def test_single_ingredient_brand_resolves_through_verified_relationship():
    client, requests = client_for({
        '/REST/rxcui.json': {'idGroup': {'rxnormId': ['brand-id']}},
    })
    with client, fallback.RxNavFallback(client) as resolver:
        assert resolver.resolve('Singulair') is None  # IDs must be actual numeric RxCUIs.
    fallback._CACHE.clear()
    client, requests = client_for({
        '/REST/rxcui.json': {'idGroup': {'rxnormId': ['153165']}},
        '/REST/rxcui/153165/properties.json': {'properties': {'rxcui': '153165', 'name': 'Singulair', 'tty': 'BN'}},
        '/REST/rxcui/153165/related.json': {'relatedGroup': {'conceptGroup': [{'tty': 'IN', 'conceptProperties': [{'rxcui': '88249', 'tty': 'IN'}]}]}},
        '/REST/rxcui/88249/properties.json': ingredient('88249', 'montelukast'),
    })
    with client, fallback.RxNavFallback(client) as resolver:
        result = resolver.resolve('Singulair')
    assert result['medication'].rxcui == '88249'
    assert result['medication'].brand_names == ['Singulair']
    assert result['match_type'] == 'brand'
    assert len(requests) == 4


@pytest.mark.parametrize('ids', [[], ['1', '2']])
def test_missing_or_ambiguous_names_stay_unresolved(ids):
    client, requests = client_for({'/REST/rxcui.json': {'idGroup': {'rxnormId': ids}}})
    with client, fallback.RxNavFallback(client) as resolver:
        assert resolver.resolve('unknown') is None
        assert resolver.resolve('unknown') is None
    assert len(requests) == 1


def test_combination_brand_is_not_reduced_to_one_ingredient():
    client, _ = client_for({
        '/REST/rxcui.json': {'idGroup': {'rxnormId': ['123']}},
        '/REST/rxcui/123/properties.json': {'properties': {'rxcui': '123', 'name': 'Combination', 'tty': 'BN'}},
        '/REST/rxcui/123/related.json': {'relatedGroup': {'conceptGroup': [{'tty': 'IN', 'conceptProperties': [{'rxcui': '1'}, {'rxcui': '2'}]}]}},
    })
    with client, fallback.RxNavFallback(client) as resolver:
        assert resolver.resolve('Combination') is None


@pytest.mark.parametrize('payload', [
    {'properties': {'rxcui': '999', 'name': 'wrong ID', 'tty': 'IN'}},
    {'properties': {'rxcui': '6851', 'name': 'product', 'tty': 'SCD'}},
    {},
])
def test_properties_must_identify_the_requested_ingredient(payload):
    client, _ = client_for({
        '/REST/rxcui.json': {'idGroup': {'rxnormId': ['6851']}},
        '/REST/rxcui/6851/properties.json': payload,
    })
    with client, fallback.RxNavFallback(client) as resolver:
        assert resolver.resolve('methotrexate') is None


@pytest.mark.parametrize('failure', [429, 500, httpx.ReadTimeout('timeout'), ['invalid']])
def test_outage_or_invalid_response_does_not_raise_or_cache_a_false_absence(failure):
    client, requests = client_for({'/REST/rxcui.json': failure})
    with client, fallback.RxNavFallback(client) as resolver:
        assert resolver.resolve('methotrexate') is None
        assert resolver.resolve('methotrexate') is None
    assert len(requests) == 2


def test_budget_limits_requests_and_short_unknown_abbreviations_skip_network():
    client, requests = client_for({'/REST/rxcui.json': {'idGroup': {}}})
    with client, fallback.RxNavFallback(client) as resolver:
        assert resolver.resolve('XYZ') is None
        resolver.remaining = 1
        assert resolver.resolve('unknown-one') is None
        assert resolver.resolve('unknown-two') is None
        resolver.remaining = 10
        resolver.deadline = 0
        assert resolver.resolve('unknown-three') is None
    assert len(requests) == 1
