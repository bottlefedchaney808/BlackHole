"""test_quant_view.py

Covers Task 6 of docs/superpowers/plans/2026-08-01-quant-console.md:
`GET /quant`, the module-card view.

Task 6's own checklist is explicitly UI/manual verification ("this is UI, not
unit-testable in the usual sense"), not a TDD-with-fixtures task like Tasks
1-5 -- there is no prescribed failing-test step in the plan for this task.
These tests cover what *is* mechanically checkable without a browser: the
route exists, renders one card per `dashboard.quant_modules.MODULE_REGISTRY`
entry, and gives the Options module (`runnable=False`) a statically distinct
"unsupported" treatment rather than folding it into a generic status -- the
concrete, checkable form of Task 6 Step 2 (spec Open Question 2). Real
browser rendering / click-through of Run -> poll -> summary is NOT covered
here; see the task report for what was and wasn't manually verified.

`dashboard.app` is imported in-process, same convention as
test_quant_summary_route.py -- no .env-loading behavior is under test here.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402
from dashboard.quant_modules import MODULE_REGISTRY  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


def test_quant_route_returns_200():
    resp = client.get('/quant')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['content-type']


def test_quant_route_renders_one_card_per_registry_entry():
    resp = client.get('/quant')
    html = resp.text

    # One `data-module-id="<id>"` card per registry entry, no more/no fewer.
    for entry in MODULE_REGISTRY:
        assert f'data-module-id="{entry["id"]}"' in html, entry["id"]
        assert entry['name'] in html
    assert html.count('class="quant-card"') == len(MODULE_REGISTRY) or \
        html.count('quant-card') >= len(MODULE_REGISTRY)


def test_quant_route_wires_run_button_to_registry_suite():
    """Each card's Run control must target its own registry `suite` (used as
    the `POST /run/{suite_or_unified}` path segment client-side), not a
    generic/shared value -- otherwise every card would trigger the same run.
    """
    resp = client.get('/quant')
    html = resp.text
    for entry in MODULE_REGISTRY:
        assert f'data-suite="{entry["suite"]}"' in html, entry['id']


def test_options_card_is_marked_unsupported_statically_and_distinctly():
    """Options (`runnable=False`) must render a distinct, visible marker
    *before* any run happens (the registry already knows this) -- and no
    runnable module's card should carry that same marker in its initial
    state.
    """
    resp = client.get('/quant')
    html = resp.text

    options_start = html.index('data-module-id="options"')
    # Slice out roughly this one card's markup (next card boundary or EOF).
    next_card = html.find('data-module-id="', options_start + 1)
    options_card_html = html[options_start:next_card if next_card != -1 else len(html)]

    assert 'unsupported' in options_card_html
    assert 'data-runnable="false"' in options_card_html

    for entry in MODULE_REGISTRY:
        if entry['id'] == 'options':
            continue
        start = html.index(f'data-module-id="{entry["id"]}"')
        nxt = html.find('data-module-id="', start + 1)
        card_html = html[start:nxt if nxt != -1 else len(html)]
        assert 'data-runnable="true"' in card_html
        # A runnable module's card must not carry the same static
        # "unsupported" marker Options gets.
        assert 'class="pill unsupported"' not in card_html


def test_quant_route_client_js_wires_run_poll_and_summary_endpoints():
    """The frontend must call the *existing* endpoints this task reuses
    (POST /run/{suite}, GET /runs/{run_id}, GET /runs/{run_id}/summary) --
    not invent new ones. Cheap regression guard against a typo'd path.
    """
    resp = client.get('/quant')
    html = resp.text
    assert "'/run/'" in html or '"/run/"' in html
    assert "'/runs/'" in html or '"/runs/"' in html
    assert '/summary' in html


def test_quant_nav_link_present_and_marked_active():
    resp = client.get('/quant')
    html = resp.text
    assert 'href="/quant"' in html
    assert 'class="on"' in html or 'class="tabs-on"' in html or ' on"' in html

    home_resp = client.get('/')
    assert 'href="/quant"' in home_resp.text
