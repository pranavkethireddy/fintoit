"""
Regression tests for the public /runway-calculator projection math.

The calculator is client-side JavaScript embedded in the template, because the
page is ungated and has to load fast for a cold visitor. To keep it honest,
these tests EXTRACT THE SHIPPED SCRIPT BLOCK from the template itself and run it
under Node. There is no second copy of the math to drift out of sync — if the
template changes, these tests exercise the change.

(The previous version of the template carried a comment claiming its edge cases
were "pinned" in a module at fintoit/public/runway.js. No such file exists in
this repository. This is that claim made real.)

Run: python -m pytest tests/test_runway_calculator.py
"""
import json
import os
import re
import shutil
import subprocess

import pytest

TEMPLATE = os.path.join(os.path.dirname(__file__), '..', 'templates',
                        'runway_calculator.html')

pytestmark = pytest.mark.skipif(shutil.which('node') is None,
                                reason='Node is required to run the calculator JS')


def _extract_script():
    """Pull the projection block out of the template exactly as it ships."""
    html = open(TEMPLATE, encoding='utf-8').read()
    blocks = re.findall(r'<script>(.*?)</script>', html, re.S)
    matching = [b for b in blocks if 'function projectRunway' in b]
    assert len(matching) == 1, 'expected exactly one projection script block'
    body = matching[0]
    # The DOM half is guarded by `typeof document !== 'undefined'`, so it is
    # skipped automatically under Node. Guard against that guard being removed.
    assert "typeof document !== 'undefined'" in body, \
        'the browser-only section must stay guarded so it can be tested headlessly'
    return body


SCRIPT = _extract_script() if shutil.which('node') else ''


def run_js(expression):
    """Evaluate an expression against the shipped calculator code."""
    program = SCRIPT + '\n;console.log(JSON.stringify(' + expression + '));'
    out = subprocess.run(['node', '-e', program], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def project(cash, expenses, revenue, growth, hires=0, hire_cost=0):
    return run_js('projectRunway(%s)' % json.dumps({
        'cash': cash, 'expenses': expenses, 'revenue': revenue,
        'growth': growth, 'hires': hires, 'hireCost': hire_cost}))


def months_of(result):
    """JSON has no Infinity; JSON.stringify emits null for it."""
    return result['months']


# ── the pre-filled example ────────────────────────────────────────────────
# These are the numbers a cold visitor sees, so they are worth pinning exactly.
# If someone edits RC_DEFAULTS, this test tells them what it did to the story.

DEFAULTS = dict(cash=210000, expenses=55000, revenue=23000, growth=7)


def test_defaults_land_on_a_default_dead_company_at_ten_months():
    r = project(**DEFAULTS)
    assert round(months_of(r), 2) == 10.00


def test_defaults_are_actually_the_template_defaults():
    """Guard against the copy and the code drifting apart."""
    d = run_js('RC_DEFAULTS')
    assert d == {'cash': 210000, 'expenses': 55000, 'revenue': 23000,
                 'growth': 7, 'hires': 0, 'hireCost': 11000}


def test_defaults_reach_breakeven_after_the_cash_runs_out():
    """The hook: default dead, but only just — 3 months short."""
    r = project(**DEFAULTS)
    assert r['breakevenMonth'] == 13
    assert r['breakevenMonth'] > months_of(r)


def test_growth_materially_changes_the_answer():
    """If a single division gave the same number, the growth input is pointless."""
    r = project(**DEFAULTS)
    assert round(r['naiveMonths'], 2) == 6.56          # cash / (55k - 23k)
    assert round(months_of(r), 2) == 10.00
    assert months_of(r) - r['naiveMonths'] > 3.0


# ── hiring ────────────────────────────────────────────────────────────────

def test_each_hire_shortens_runway():
    ladder = [round(months_of(project(hires=n, hire_cost=11000, **DEFAULTS)), 2)
              for n in range(4)]
    assert ladder == [10.00, 5.68, 4.25, 3.43]
    assert ladder == sorted(ladder, reverse=True)


def test_hiring_also_pushes_breakeven_out():
    """Revenue has further to climb, so the finish line moves too."""
    base = project(**DEFAULTS)['breakevenMonth']
    with_hires = project(hires=2, hire_cost=11000, **DEFAULTS)['breakevenMonth']
    assert with_hires > base


def test_hire_cost_is_ignored_when_there_are_no_hires():
    a = project(hires=0, hire_cost=11000, **DEFAULTS)
    b = project(hires=0, hire_cost=0, **DEFAULTS)
    assert months_of(a) == months_of(b)


# ── growth ────────────────────────────────────────────────────────────────

def test_zero_growth_matches_the_simple_formula_exactly():
    """Backwards compatibility: the old page did one division, and at 0% growth
    the projection must agree with it to the last decimal."""
    r = project(cash=210000, expenses=55000, revenue=23000, growth=0)
    assert abs(months_of(r) - 210000 / (55000 - 23000)) < 1e-9
    assert abs(months_of(r) - r['naiveMonths']) < 1e-9


def test_enough_growth_flips_the_company_to_default_alive():
    """Sliding 7% -> 10% on the defaults is the moment worth screenshotting."""
    assert months_of(project(cash=210000, expenses=55000, revenue=23000, growth=7)) is not None
    r = project(cash=210000, expenses=55000, revenue=23000, growth=10)
    assert months_of(r) is None            # Infinity -> null through JSON
    assert r['breakevenMonth'] == 10


def test_more_growth_never_shortens_runway():
    """Monotonic in growth, with default alive (None) treated as the ceiling."""
    prev = 0.0
    for g in (0, 2, 4, 6, 8):
        m = months_of(project(cash=210000, expenses=55000, revenue=23000, growth=g))
        if m is None:
            prev = float('inf')            # default alive — nothing beats it
            continue
        assert prev != float('inf'), 'runway went finite again after default alive'
        assert m >= prev
        prev = m


def test_declining_revenue_shortens_runway():
    flat = months_of(project(cash=210000, expenses=55000, revenue=23000, growth=0))
    decl = months_of(project(cash=210000, expenses=55000, revenue=23000, growth=-5))
    assert decl < flat


def test_flat_revenue_never_breaks_even():
    r = project(cash=210000, expenses=55000, revenue=23000, growth=0)
    assert r['breakevenMonth'] is None


def test_already_profitable_is_default_alive_immediately():
    r = project(cash=100000, expenses=20000, revenue=30000, growth=0)
    assert months_of(r) is None
    assert r['breakevenMonth'] == 0
    assert r['netBurnNow'] < 0             # sign preserved, as on the server


# ── edge cases that must not produce a nonsense number ────────────────────

def test_no_cash_is_zero_months_not_a_crash():
    r = project(cash=0, expenses=55000, revenue=23000, growth=7)
    assert months_of(r) == 0


def test_no_expenses_is_default_alive():
    r = project(cash=210000, expenses=0, revenue=23000, growth=7)
    assert months_of(r) is None


def test_no_revenue_burns_at_full_expenses():
    r = project(cash=210000, expenses=55000, revenue=0, growth=7)
    assert abs(months_of(r) - 210000 / 55000) < 1e-9
    assert r['breakevenMonth'] is None     # 0 revenue never grows into anything


def test_empty_input_does_not_produce_nan():
    r = project(cash=0, expenses=0, revenue=0, growth=0)
    for key in ('months', 'netBurnNow', 'naiveMonths', 'totalExpenses'):
        value = r[key]
        assert value is None or value == value, key   # NaN != NaN


def test_absurd_growth_is_clamped_and_terminates():
    r = project(cash=210000, expenses=55000, revenue=23000, growth=100000)
    assert months_of(r) is None


def test_absurd_hire_count_is_clamped_and_terminates():
    r = project(cash=210000, expenses=55000, revenue=23000, growth=7,
                hires=999999, hire_cost=11000)
    assert months_of(r) is not None and months_of(r) >= 0


def test_currency_formatting_does_not_affect_math():
    """Amounts arrive from text inputs, so grouped strings must parse."""
    grouped = run_js('projectRunway({cash:"210,000",expenses:"$55,000",'
                     'revenue:"23,000",growth:"7",hires:"0",hireCost:"11,000"})')
    plain = project(**DEFAULTS)
    assert round(grouped['months'], 6) == round(plain['months'], 6)


# ── the chart series ──────────────────────────────────────────────────────

def test_series_ends_at_zero_when_the_company_dies():
    r = project(**DEFAULTS)
    assert r['series'][0]['cash'] == 210000
    assert r['series'][-1]['cash'] == 0
    assert abs(r['series'][-1]['month'] - months_of(r)) < 1e-9


def test_series_is_monotonically_declining_while_burning():
    r = project(**DEFAULTS)
    cashes = [p['cash'] for p in r['series']]
    assert cashes == sorted(cashes, reverse=True)


def test_series_turns_upward_once_default_alive():
    r = project(cash=210000, expenses=55000, revenue=23000, growth=10)
    cashes = [p['cash'] for p in r['series']]
    assert min(cashes) < cashes[-1], 'cash should recover after breakeven'
    assert min(cashes) > 0, 'a default-alive company never touches zero'


def test_series_is_bounded_for_the_chart():
    r = project(cash=5000000, expenses=55000, revenue=0, growth=0)
    assert len(r['series']) <= 40      # RC_CHART_MONTHS + a little slack


# ── bridging to breakeven ─────────────────────────────────────────────────
# "How much more do I need?" is the single most actionable number on the page,
# so it has to be the real one. The naive answer -- months_short x today's net
# burn -- overstates it badly, because net burn shrinks every month as revenue
# grows. On the defaults that naive figure is $91,065; the truth is $9,765.

def test_bridge_figure_is_the_cash_trough_not_months_times_burn():
    r = project(**DEFAULTS)
    naive = (r['breakevenMonth'] - r['months']) * r['netBurnNow']
    assert round(r['additionalCashNeeded']) == 9765
    assert naive > 9 * r['additionalCashNeeded'], 'naive estimate should be wildly higher'


@pytest.mark.parametrize('hires,expected', [(0, 9765), (1, 161575), (2, 340022)])
def test_bridge_figure_for_each_hiring_level(hires, expected):
    r = project(hires=hires, hire_cost=11000, **DEFAULTS)
    assert round(r['additionalCashNeeded']) == expected


@pytest.mark.parametrize('hires', [0, 1, 2])
def test_bridge_figure_is_exactly_enough(hires):
    """The boundary: that much cash survives to breakeven, slightly less does not."""
    need = project(hires=hires, hire_cost=11000, **DEFAULTS)['additionalCashNeeded']

    survives = project(cash=DEFAULTS['cash'] + need + 1, expenses=DEFAULTS['expenses'],
                       revenue=DEFAULTS['revenue'], growth=DEFAULTS['growth'],
                       hires=hires, hire_cost=11000)
    assert months_of(survives) is None, 'bridge amount should reach default alive'

    dies = project(cash=DEFAULTS['cash'] + need - 500, expenses=DEFAULTS['expenses'],
                   revenue=DEFAULTS['revenue'], growth=DEFAULTS['growth'],
                   hires=hires, hire_cost=11000)
    assert months_of(dies) is not None, 'just under the bridge amount should still die'


def test_no_bridge_needed_when_already_default_alive():
    r = project(cash=210000, expenses=55000, revenue=23000, growth=10)
    assert months_of(r) is None
    assert r['additionalCashNeeded'] == 0


def test_no_bridge_figure_when_breakeven_is_unreachable():
    r = project(cash=210000, expenses=55000, revenue=23000, growth=0)
    assert r['breakevenMonth'] is None
    assert r['additionalCashNeeded'] == 0
