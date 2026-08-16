from fastapi.testclient import TestClient
from dashboard.app import app

client = TestClient(app)

def test_vrp_term_structure_page_launches():
    r = client.get('/tools/vrp-term-structure')
    assert r.status_code == 200

def test_price_distribution_page_launches():
    r = client.get('/tools/price-distribution')
    assert r.status_code == 200
