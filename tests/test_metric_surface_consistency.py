"""
Every surface must report the same burn, runway, MRR and growth.

This file exists because the same defect shipped three times.

  Round 1: the dashboard and the AI chat disagreed 9x on burn. Fixed by adding
           Modules/metrics.py and migrating those two surfaces.
  Round 2: scenario planning, the runway planner and the board report still had
           `else 999` sentinels. Fixed by migrating them too.
  Round 3: /ai-insights, /export-pdf, /custom-report, /projections and /goals
           were still calling FinancialCalculator.get_all_metrics(), which had
           kept ALL of the original bugs. On a real account the dashboard said
           "Profitable, infinite runway" while /ai-insights said "2.6 months"
           and raised an urgent warning about closure.

Each round was fixed by migrating the surfaces that were known about, which is
why a fourth one kept appearing. The fix is now at the root — get_all_metrics
delegates to the canonical module — and the STRUCTURAL tests at the bottom of
this file fail if anyone reintroduces an independent calculation.

Run: python -m pytest tests/test_metric_surface_consistency.py
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from Modules import metrics as M
from Modules.financial_calculator import FinancialCalculator

APP_PY = os.path.join(os.path.dirname(__file__), '..', 'app.py')


# ── the account from the bug report ───────────────────────────────────────
# One complete month that is comfortably profitable, plus a few days of the
# current month with a little spend and no revenue booked yet. Every defect
# fired at once on this shape.

OWNER_ACCOUNT = [
    {'date': '2026-07-03', 'type': 'expense', 'category': 'Software', 'amount': 250.00},
    {'date': '2026-07-09', 'type': 'expense', 'category': 'Government Fees', 'amount': 170.00},
    {'date': '2026-07-15', 'type': 'expense', 'category': 'Software', 'amount': 192.00},
    {'date': '2026-07-28', 'type': 'income', 'category': 'Sales', 'amount': 1188.00},
    {'date': '2026-08-02', 'type': 'expense', 'category': 'Software', 'amount': 23.27},
]
AS_OF = '2026-08-05'


def owner_metrics():
    return FinancialCalculator.get_all_metrics(OWNER_ACCOUNT, 0)


def test_profitable_account_gets_no_runway_figure():
    """The headline bug: /ai-insights told a profitable company it had 2.6
    months of cash and might close."""
    m = owner_metrics()
    assert m['runway_months'] is None
    assert m['is_profitable'] is True
    assert m['runway_display'] == 'Profitable'


def test_burn_is_not_divided_by_a_hardcoded_three():
    """$612 of expenses in the only complete month must read as $612, not
    $612/3. The old code divided by a hardcoded 3 and reported $211.82."""
    m = owner_metrics()
    assert m['window_months'] == 1
    assert m['monthly_burn'] == 612.00
    assert m['monthly_burn'] != pytest.approx(211.82, abs=1.0)


def test_net_burn_reflects_revenue_and_keeps_its_sign():
    m = owner_metrics()
    assert m['net_burn'] == 612.00 - 1188.00 == -576.00
    assert m['revenue_monthly'] == 1188.00


def test_growth_excludes_the_current_partial_month():
    """August is 5 days old with no revenue booked. Comparing it against a
    finished July produced a -100% 'significant decline' every month."""
    m = owner_metrics()
    assert m['revenue_growth'] != -100.0


def test_partial_month_never_drags_growth_to_minus_one_hundred():
    """Same shape, two complete months, then a bare partial one."""
    txs = [
        {'date': '2026-06-10', 'type': 'income', 'amount': 1000.00},
        {'date': '2026-06-10', 'type': 'expense', 'amount': 400.00},
        {'date': '2026-07-10', 'type': 'income', 'amount': 1200.00},
        {'date': '2026-07-10', 'type': 'expense', 'amount': 400.00},
        {'date': '2026-08-01', 'type': 'expense', 'amount': 20.00},
    ]
    m = FinancialCalculator.get_all_metrics(txs, 0)
    assert m['revenue_growth'] == pytest.approx(20.0, abs=0.01)   # 1000 -> 1200


# ── the legacy entry points must not lie any more ─────────────────────────

def test_calculate_burn_rate_delegates():
    """It was the original bug's home. It must now agree with canonical."""
    canon = M.compute_metrics(OWNER_ACCOUNT, 0)
    assert FinancialCalculator.calculate_burn_rate(OWNER_ACCOUNT) == canon['gross_burn_monthly']


def test_calculate_mrr_delegates_and_ignores_the_recurring_flag():
    """The old version only counted rows flagged `recurring`, so CSV and bank
    imports reported $0 MRR while the dashboard showed real revenue."""
    canon = M.compute_metrics(OWNER_ACCOUNT, 0)
    assert FinancialCalculator.calculate_mrr(OWNER_ACCOUNT) == canon['mrr'] == 1188.00
    assert not any(t.get('recurring') for t in OWNER_ACCOUNT)


def test_calculate_runway_is_infinite_at_or_below_breakeven():
    assert FinancialCalculator.calculate_runway(1000, 0) == float('inf')
    assert FinancialCalculator.calculate_runway(1000, -50) == float('inf')
    assert FinancialCalculator.calculate_runway(0, 100) == 0.0
    assert FinancialCalculator.calculate_runway(1000, 100) == 10.0


# ── get_all_metrics must agree with the canonical module, always ──────────

SCENARIOS = {
    'owner account (profitable, 1 month)': (OWNER_ACCOUNT, 0),
    'pre-revenue burning': ([{'date': '2026-%02d-10' % m, 'type': 'expense', 'amount': 40000}
                             for m in (5, 6, 7)], 500000),
    'burning with revenue': (sum([[{'date': '2026-%02d-10' % m, 'type': 'income', 'amount': 9000},
                                   {'date': '2026-%02d-11' % m, 'type': 'expense', 'amount': 31000}]
                                  for m in (5, 6, 7)], []), 180000),
    'exact breakeven': (sum([[{'date': '2026-%02d-10' % m, 'type': 'income', 'amount': 30000},
                              {'date': '2026-%02d-11' % m, 'type': 'expense', 'amount': 30000}]
                             for m in (5, 6, 7)], []), 100000),
    'empty account': ([], 0),
}


@pytest.mark.parametrize('name', list(SCENARIOS))
def test_get_all_metrics_agrees_with_canonical(name):
    txs, cash = SCENARIOS[name]
    legacy = FinancialCalculator.get_all_metrics(txs, cash)
    canon = M.compute_metrics(txs, cash)
    assert legacy['current_cash'] == canon['cash_balance'], name
    assert legacy['monthly_burn'] == canon['gross_burn_monthly'], name
    assert legacy['net_burn'] == canon['net_burn_monthly'], name
    assert legacy['runway_months'] == canon['runway_months'], name
    assert legacy['mrr'] == canon['mrr'], name
    assert legacy['is_profitable'] == canon['is_profitable'], name
    assert legacy['window_months'] == canon['window_months'], name


@pytest.mark.parametrize('name', list(SCENARIOS))
def test_no_surface_ever_reports_runway_for_a_profitable_company(name):
    txs, cash = SCENARIOS[name]
    legacy = FinancialCalculator.get_all_metrics(txs, cash)
    if legacy['is_profitable'] or not legacy['has_data']:
        assert legacy['runway_months'] is None, name


# ── the AI insights summary ───────────────────────────────────────────────

def test_insights_summary_does_not_crash_on_a_profitable_company():
    """It formatted runway as `{runway_months:.1f}`, which raises on None."""
    from Modules.ai_insights import AIFinancialAdvisor
    m = owner_metrics()
    text = AIFinancialAdvisor._prepare_summary(m, m['monthly_breakdown'], OWNER_ACCOUNT)
    assert 'not applicable' in text


def test_insights_summary_tells_the_model_not_to_warn_about_cash():
    from Modules.ai_insights import AIFinancialAdvisor
    m = owner_metrics()
    text = AIFinancialAdvisor._prepare_summary(m, m['monthly_breakdown'], OWNER_ACCOUNT)
    assert 'Do NOT warn about running out of cash' in text
    assert 'MAKES this much each month' in text
    assert '2.6' not in text


def test_insights_summary_states_that_one_month_is_thin_evidence():
    from Modules.ai_insights import AIFinancialAdvisor
    m = owner_metrics()
    text = AIFinancialAdvisor._prepare_summary(m, m['monthly_breakdown'], OWNER_ACCOUNT)
    assert 'only 1 complete month' in text


def test_insights_summary_labels_burn_with_its_period():
    """'Monthly Burn Rate: $211.82' with no period and no revenue is what let
    the model reason about a company that only spends."""
    from Modules.ai_insights import AIFinancialAdvisor
    m = owner_metrics()
    text = AIFinancialAdvisor._prepare_summary(m, m['monthly_breakdown'], OWNER_ACCOUNT)
    assert 'complete month' in text
    assert 'Average monthly revenue' in text


# ── STRUCTURAL: stop this happening a fourth time ─────────────────────────

LEGACY_PRIMITIVES = [
    'calculate_burn_rate',
    'calculate_runway',
    'calculate_mrr',
    'calculate_growth_rate',
]


def test_no_route_calls_the_legacy_primitives_directly():
    """Routes must go through get_all_metrics() or compute_metrics().

    Calling these helpers directly is how each round of this bug started: a new
    surface reached for the nearest-looking function and got an independent,
    subtly different answer.
    """
    src = open(APP_PY, encoding='utf-8').read()
    offenders = []
    for name in LEGACY_PRIMITIVES:
        for match in re.finditer(r'\.%s\s*\(' % name, src):
            line = src[:match.start()].count('\n') + 1
            offenders.append('%s at app.py:%d' % (name, line))
    assert not offenders, (
        'These call an independent metric calculation instead of the canonical '
        'one: %s' % offenders)


def test_no_route_divides_cash_by_burn_by_hand():
    """`cash / burn` in a route is always wrong — it skips the profitability
    guard and produces a confident number for a company that is fine."""
    src = open(APP_PY, encoding='utf-8').read()
    patterns = [
        r'current_cash\s*/\s*\w*burn',
        r'cash\s*/\s*monthly_burn',
        r'cash_balance\s*/\s*\w*burn',
    ]
    offenders = []
    for p in patterns:
        for match in re.finditer(p, src):
            line = src[:match.start()].count('\n') + 1
            offenders.append('app.py:%d -> %s' % (line, match.group(0)))
    assert not offenders, 'Hand-rolled runway division found: %s' % offenders


def test_no_999_runway_sentinel_survives():
    """999 renders as a real month count to a user."""
    src = open(APP_PY, encoding='utf-8').read()
    offenders = []
    for match in re.finditer(r'else\s+999\b', src):
        line = src[:match.start()].count('\n') + 1
        offenders.append('app.py:%d' % line)
    assert not offenders, 'runway 999 sentinel reintroduced at %s' % offenders


def test_every_get_all_metrics_caller_is_safe_by_construction():
    """get_all_metrics is now the safe entry point, so simply counting its
    callers documents the blast radius of any future change to it."""
    src = open(APP_PY, encoding='utf-8').read()
    callers = len(re.findall(r'get_all_metrics\s*\(', src))
    assert callers >= 8, (
        'Expected the known surfaces to still route through get_all_metrics; '
        'found %d. If a surface stopped using it, confirm it uses '
        'compute_metrics directly.' % callers)


# ── anomaly detection ─────────────────────────────────────────────────────
# Same partial-month defect, different surface. Every check compares the
# latest month against the average of the ones before it, so including a month
# that is five days old guaranteed a false alarm at the start of every month.

def _months(spec, category='Software'):
    """spec: list of (month, revenue, expense)."""
    rows = []
    for month, rev, exp in spec:
        if rev:
            rows.append({'date': '2026-%02d-10' % month, 'type': 'income', 'amount': rev})
        if exp:
            rows.append({'date': '2026-%02d-11' % month, 'type': 'expense',
                         'category': category, 'amount': exp})
    return rows


def _detect(rows, as_of):
    from Modules.financial_calculator import AnomalyDetector
    monthly = FinancialCalculator.group_by_month(rows, 0)
    return AnomalyDetector.detect(rows, monthly, as_of=as_of)


def test_healthy_company_gets_no_alarms_early_in_the_month():
    """The false alarm: on the 5th, a growing company was told its revenue had
    dropped 100% and that it had a zero-revenue month."""
    from datetime import date
    rows = _months([(3, 1000, 400), (4, 1100, 400), (5, 1200, 400),
                    (6, 1300, 400), (7, 1400, 400)])
    rows.append({'date': '2026-08-02', 'type': 'expense',
                 'category': 'Software', 'amount': 20})
    assert _detect(rows, date(2026, 8, 5)) == []


def test_partial_month_never_triggers_a_zero_revenue_alarm():
    from datetime import date
    rows = _months([(4, 1000, 400), (5, 1000, 400), (6, 1000, 400), (7, 1000, 400)])
    rows.append({'date': '2026-08-01', 'type': 'expense',
                 'category': 'Software', 'amount': 50})
    kinds = {a['type'] for a in _detect(rows, date(2026, 8, 3))}
    assert 'zero_revenue' not in kinds
    assert 'revenue_drop' not in kinds


def test_a_real_revenue_drop_in_a_complete_month_still_fires():
    """Suppressing the partial month must not suppress genuine signal."""
    from datetime import date
    rows = _months([(3, 1000, 400), (4, 1100, 400), (5, 1200, 400),
                    (6, 1300, 400), (7, 100, 400)])
    kinds = {a['type'] for a in _detect(rows, date(2026, 8, 5))}
    assert 'revenue_drop' in kinds


def test_a_real_expense_spike_in_a_complete_month_still_fires():
    from datetime import date
    rows = _months([(3, 1000, 400), (4, 1000, 400), (5, 1000, 400),
                    (6, 1000, 400), (7, 1000, 3000)])
    kinds = {a['type'] for a in _detect(rows, date(2026, 8, 5))}
    assert 'expense_spike' in kinds


def test_demo_fixture_planted_duplicate_is_still_detected():
    """The $1,800 Datadog charge billed twice must keep demoing."""
    from Modules.financial_calculator import (AnomalyDetector,
                                              create_sample_transactions,
                                              DEMO_STARTING_CASH)
    txs = create_sample_transactions()
    monthly = FinancialCalculator.group_by_month(txs, DEMO_STARTING_CASH)
    found = AnomalyDetector.detect(txs, monthly)
    assert any(a['type'] == 'expense_spike' for a in found), \
        'the planted duplicate charge must still be caught'


# ── PDF exports ───────────────────────────────────────────────────────────
# These go to investors and boards, so a None leaking into an f-string is not
# a cosmetic issue. Making burn_multiple and runway None-able (correctly) broke
# formatting that had always received numbers.

def _profitable_inputs():
    rows = []
    for m in (5, 6, 7):
        rows.append({'date': '2026-%02d-10' % m, 'type': 'income', 'amount': 60000})
        rows.append({'date': '2026-%02d-11' % m, 'type': 'expense',
                     'category': 'Ops', 'amount': 15300})
    metrics = FinancialCalculator.get_all_metrics(rows, 328200)
    canon = M.compute_metrics(rows, 328200)
    kpis = FinancialCalculator.calculate_kpis(rows, 328200, canon=canon)
    return rows, metrics, kpis


def test_board_pdf_never_prints_nonex_for_burn_multiple():
    """`f"{kpis['burn_multiple']}x"` rendered the literal string 'Nonex'."""
    rows, metrics, kpis = _profitable_inputs()
    assert kpis['burn_multiple'] is None
    bm = kpis.get('burn_multiple')
    cell = 'N/A' if bm is None else '%sx' % bm
    assert cell == 'N/A' and 'None' not in cell


@pytest.mark.parametrize('generator', ['board', 'investor', 'simple'])
def test_pdf_generators_render_a_profitable_company(generator):
    """Build the real PDF and assert no None leaked into the visible text.

    reportlab compresses page content streams by default, so searching the raw
    bytes for 'Nonex' silently finds nothing even when it IS printed on the
    page — an earlier version of this test passed against a deliberately
    reintroduced bug. Compression is disabled here so the drawn strings are
    actually inspectable, and the 'Acme' assertion below fails loudly if that
    ever stops being true.
    """
    pytest.importorskip('reportlab')
    from reportlab import rl_config
    from Modules import export_utils

    rows, metrics, kpis = _profitable_inputs()
    monthly = metrics['monthly_breakdown']

    previous = rl_config.pageCompression
    rl_config.pageCompression = 0
    try:
        if generator == 'board':
            out = export_utils.generate_board_report(
                'Acme', metrics, kpis, monthly, 'Narrative.', {'period': 'Q3 2026'})
        elif generator == 'investor':
            out = export_utils.generate_investor_report(
                'Acme', metrics, monthly, rows, 'Narrative.',
                {'tagline': 'x', 'stage': 'seed', 'use_of_funds': 'y'})
        else:
            out = export_utils.generate_pdf_report('Acme', metrics, monthly, rows)
    finally:
        rl_config.pageCompression = previous

    assert out and len(out) > 1000
    # Self-check: if the text is not searchable this test proves nothing, so
    # fail loudly rather than passing vacuously.
    assert b'Acme' in out, 'PDF text is not inspectable — assertions below are void'
    for forbidden in (b'Nonex', b'None mo', b'None%', b'$None'):
        assert forbidden not in out, '%s leaked into the %s PDF' % (forbidden, generator)
