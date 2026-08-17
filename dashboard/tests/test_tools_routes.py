from fastapi.testclient import TestClient
from dashboard.app import app

client = TestClient(app)

def test_vrp_term_structure_page_launches():
    r = client.get('/tools/vrp-term-structure')
    assert r.status_code == 200

def test_simulations_page_launches():
    r = client.get('/tools/simulations')
    assert r.status_code == 200

def test_simulations_form_reveals_inputs():
    r = client.get('/tools/simulations')
    assert r.status_code == 200
    assert 'horizon_days' in r.text and 'n_sims' in r.text
    assert 'confidence' in r.text and 'seed' in r.text
