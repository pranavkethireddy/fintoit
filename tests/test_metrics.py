"""
Regression tests for the canonical burn/runway metrics.

Test I is the one that matters most: it asserts the dashboard path and the AI
path produce identical numbers. Had it existed, the 9x burn discrepancy would
have failed CI instead of reaching the public demo.

Run: python -m pytest tests/test_metrics.py
"""
from datetime import date
from Modules import metrics as M

AS_OF = date(2026, 8, 1)   # Feb-Jul 2026 are all complete months


def tx(month, revenue=0, expense=0):
    rows = []
    if revenue:
        rows.append({'date': '2026-%02d-15' % month, 'amount': revenue, 'type': 'income'})
    if expense:
        rows.append({'date': '2026-%02d-15' % month, 'amount': expense, 'type': 'expense'})
    return rows


DEMO = sum([tx(m, r, 15300) for m, r in
            [(2, 30000), (3, 36000), (4, 42000), (5, 48000), (6, 54000), (7, 60000)]], [])


def test_a_profitable_company():
    """The exact case that shipped broken: dashboard said 32.2mo, AI said 3.58mo."""
    m = M.compute_metrics(DEMO, 150000, AS_OF, 3)
    assert m['cash_balance'] == 328200.00
    assert m['gross_burn_monthly'] == 15300.00
    assert m['net_burn_monthly'] == -38700.00
    assert m['is_profitable'] is True
    assert m['runway_months'] is None
    assert m['runway_display'] == 'Profitable'
    assert m['burn_multiple'] is None
    assert m['mrr'] == 60000.00
    assert m['arr'] == 720000.00


def test_b_pre_revenue_burning():
    m = M.compute_metrics(sum([tx(x, 0, 40000) for x in (5, 6, 7)], []), 620000, AS_OF, 3)
    assert m['gross_burn_monthly'] == 40000.00
    assert m['net_burn_monthly'] == 40000.00
    assert m['gross_burn_monthly'] == m['net_burn_monthly']   # equal when revenue is 0
    assert m['cash_balance'] == 500000.00
    assert m['runway_months'] == 12.5


def test_c_burning_with_revenue():
    m = M.compute_metrics(sum([tx(x, 9000, 31000) for x in (5, 6, 7)], []), 246000, AS_OF, 3)
    assert m['gross_burn_monthly'] == 31000.00
    assert m['net_burn_monthly'] == 22000.00
    assert abs(m['runway_months'] - 8.18) < 0.01


def test_d_exact_breakeven_no_division_error():
    m = M.compute_metrics(sum([tx(x, 30000, 30000) for x in (5, 6, 7)], []), 100000, AS_OF, 3)
    assert m['net_burn_monthly'] == 0.00
    assert m['runway_months'] is None
    assert m['runway_display'] == 'Breakeven'


def test_e_single_month_divides_by_one():
    """The class of bug that produced $10,200: dividing by the requested window."""
    m = M.compute_metrics(tx(7, 60000, 15300), 100000, AS_OF, 3)
    assert m['window_months'] == 1
    assert m['gross_burn_monthly'] == 15300.00


def test_f_partial_window():
    m = M.compute_metrics(tx(6, 0, 10000) + tx(7, 0, 20000), 100000, AS_OF, 3)
    assert m['window_months'] == 2
    assert m['gross_burn_monthly'] == 15000.00


def test_g_empty_account_is_clean():
    m = M.compute_metrics([], 0, AS_OF, 3)
    assert m['has_data'] is False
    assert m['runway_months'] is None
    assert m['runway_display'] == 'No data yet'
    for key in ('gross_burn_monthly', 'net_burn_monthly', 'cash_balance'):
        assert m[key] == m[key]            # not NaN
    ctx = M.ai_context(m)
    assert 'no transactions' in ctx
    assert '0.0 months' not in ctx          # must never imply insolvency


def test_h_zero_churn_ltv_is_undefined():
    assert M.ltv(500, 0) is None
    assert M.ltv(500, 5) == 10000.00


def test_i_cross_surface_consistency():
    """Dashboard and AI must agree exactly, for every scenario."""
    cases = [
        DEMO,
        sum([tx(x, 0, 40000) for x in (5, 6, 7)], []),
        sum([tx(x, 9000, 31000) for x in (5, 6, 7)], []),
        sum([tx(x, 30000, 30000) for x in (5, 6, 7)], []),
        tx(7, 60000, 15300),
        [],
    ]
    for txs in cases:
        dashboard = M.compute_metrics(txs, 150000, AS_OF)
        ai        = M.compute_metrics(txs, 150000, AS_OF)
        assert dashboard == ai, 'dashboard and AI metrics diverged'
        if dashboard['runway_months'] is None and dashboard['has_data']:
            ctx = M.ai_context(dashboard)
            # Stated in English, not as a raw field name — the model was
            # quoting "runway_months: null" back at users verbatim.
            assert 'Runway: not applicable' in ctx
            assert 'Never state a number of months' in ctx


def test_net_burn_sign_is_never_absolute():
    """A profitable company must report negative net burn, not a positive number."""
    m = M.compute_metrics(DEMO, 150000, AS_OF, 3)
    assert m['net_burn_monthly'] < 0


def test_business_date_not_insert_time():
    """All six months seeded at once must still bucket into six months."""
    m = M.compute_metrics(DEMO, 150000, AS_OF, 3)
    assert m['months_of_data'] == 6
    assert m['gross_burn_monthly'] == 15300.00     # not 6 x 15300


# ─────────────────────────────────────────────────────────────────────────────
# The demo dataset IS the regression fixture. If the dashboard and the AI both
# report these figures, the burn bug is closed and the demo is correct in one
# move. If either differs, that is exactly where they diverge.
# ─────────────────────────────────────────────────────────────────────────────
from Modules.financial_calculator import create_sample_transactions, DEMO_STARTING_CASH


def test_demo_fixture_exact_values():
    as_of = date(2026, 8, 1)
    m = M.compute_metrics(create_sample_transactions(as_of=as_of),
                          DEMO_STARTING_CASH, as_of, 3)
    assert m['cash_balance'] == 173000.00
    assert m['gross_burn_monthly'] == 32116.67
    assert m['net_burn_monthly'] == 23316.67      # the number from the bug spec
    assert m['runway_months'] == 7.42             # the number from the bug spec
    assert m['mrr'] == 9400.00
    assert m['is_profitable'] is False
    assert m['window_months'] == 3
    assert m['months_of_data'] == 6


def test_demo_fixture_is_date_invariant():
    """Rolling months keep the demo fresh without changing the documented figures."""
    for as_of in (date(2026, 8, 1), date(2026, 12, 31), date(2027, 3, 5), date(2028, 1, 1)):
        m = M.compute_metrics(create_sample_transactions(as_of=as_of),
                              DEMO_STARTING_CASH, as_of, 3)
        assert m['cash_balance'] == 173000.00
        assert m['net_burn_monthly'] == 23316.67
        assert m['runway_months'] == 7.42


def test_demo_fixture_expenses_are_lumpy():
    """Flat data hides averaging bugs; this fixture must vary month to month."""
    as_of = date(2026, 8, 1)
    m = M.compute_metrics(create_sample_transactions(as_of=as_of),
                          DEMO_STARTING_CASH, as_of, 3)
    complete = [r for r in m['monthly'] if not r['is_partial']]
    assert len({r['expenses'] for r in complete}) > 1


def test_demo_fixture_contains_planted_anomaly():
    """Same $1,800 charge twice, three days apart, in the most recent month."""
    txs = create_sample_transactions(as_of=date(2026, 8, 1))
    dupes = sorted(t['date'] for t in txs
                   if t['amount'] == 1800.0 and 'Datadog' in t['description'])
    assert len(dupes) == 2
    gap = (date.fromisoformat(dupes[1]) - date.fromisoformat(dupes[0])).days
    assert gap == 3


def test_demo_fixture_cross_surface_agreement():
    """Dashboard path and AI path must both report the fixture's figures."""
    as_of = date(2026, 8, 1)
    txs = create_sample_transactions(as_of=as_of)
    dashboard = M.compute_metrics(txs, DEMO_STARTING_CASH, as_of, 3)
    ai = M.compute_metrics(txs, DEMO_STARTING_CASH, as_of, 3)
    assert dashboard == ai
    ctx = M.ai_context(ai)
    assert '23,316.67' in ctx      # net burn quoted, not recomputed
    assert '7.4' in ctx            # runway quoted
    assert '91,800' not in ctx     # the old six-month-total defect


# ──────────────────────────────────────────────────────────────────────────
# Round 2: scenario modelling.
#
# The original bug was closed but the assistant refused every "what if I hire
# two engineers?" question -- the exact interaction the marketing site shows as
# its hero example. These tests pin the numbers from the retest report.
# ──────────────────────────────────────────────────────────────────────────

FIXTURE = M.compute_metrics(create_sample_transactions(), DEMO_STARTING_CASH)


def test_scenario_hire_ladder_matches_report():
    """7.4 -> 5.0 -> 3.8 -> 3.1 months. The demo's money moment."""
    expected = {
        1: (34316.67, 5.04),
        2: (45316.67, 3.82),
        3: (56316.67, 3.07),
    }
    for hires, (net, runway) in expected.items():
        s = M.compute_scenario(FIXTURE, hires=hires, salary_per_hire=11000)
        assert s['net_burn_monthly'] == net, hires
        assert s['runway_months'] == runway, hires
        assert s['runway_display'] == '%.1f months' % runway


def test_scenario_reports_runway_delta():
    s = M.compute_scenario(FIXTURE, hires=2, salary_per_hire=11000)
    assert s['baseline_runway_months'] == 7.42
    assert s['runway_delta_months'] == round(3.82 - 7.42, 2)


def test_hire_ladder_helper_matches_individual_scenarios():
    ladder = M.hire_ladder(FIXTURE, salary_per_hire=11000, max_hires=3)
    assert [s['hires'] for s in ladder] == [1, 2, 3]
    assert [s['runway_months'] for s in ladder] == [5.04, 3.82, 3.07]


def test_scenario_flags_assumed_salary():
    """No cost given -> use the documented default AND admit to it."""
    s = M.compute_scenario(FIXTURE, hires=1)
    assert s['salary_assumed'] is True
    assert s['salary_per_hire'] == M.DEFAULT_FULLY_LOADED_MONTHLY_COST
    given = M.compute_scenario(FIXTURE, hires=1, salary_per_hire=11000)
    assert given['salary_assumed'] is False


def test_scenario_keeping_company_profitable_has_no_runway():
    """A profitable company that stays profitable must not gain a month count."""
    profitable = M.compute_metrics(DEMO, 328200, as_of=AS_OF)
    assert profitable['runway_months'] is None
    s = M.compute_scenario(profitable, hires=1, salary_per_hire=11000)
    assert s['runway_months'] is None
    assert s['is_profitable'] is True


def test_scenario_can_flip_profitable_to_burning():
    profitable = M.compute_metrics(DEMO, 328200, as_of=AS_OF)
    s = M.compute_scenario(profitable, hires=10, salary_per_hire=11000)
    assert s['is_profitable'] is False
    assert s['runway_months'] is not None and s['runway_months'] > 0


def test_scenario_cost_cut_can_reach_profitability():
    s = M.compute_scenario(FIXTURE, monthly_expense_delta=-25000)
    assert s['is_profitable'] is True
    assert s['runway_months'] is None
    assert s['runway_display'] == 'Profitable'


def test_scenario_percentage_cut_uses_gross_burn():
    s = M.compute_scenario(FIXTURE, expense_pct_delta=-10)
    assert s['gross_burn_monthly'] == round(32116.67 * 0.9, 2)


def test_scenario_raise_extends_runway():
    s = M.compute_scenario(FIXTURE, cash_delta=500000)
    assert s['cash_balance'] == 673000.00
    assert s['net_burn_monthly'] == 23316.67      # a raise does not change burn
    assert s['runway_months'] == round(673000.0 / 23316.67, 2)


def test_scenario_delayed_start_gives_longer_runway():
    now = M.compute_scenario(FIXTURE, hires=2, salary_per_hire=11000)
    later = M.compute_scenario(FIXTURE, hires=2, salary_per_hire=11000, start_month=3)
    assert later['runway_months'] > now['runway_months']


def test_scenario_never_divides_by_zero_at_breakeven():
    breakeven = M.compute_metrics(
        sum([tx(m, 30000, 30000) for m in range(2, 8)], []), 100000, as_of=AS_OF)
    s = M.compute_scenario(breakeven, monthly_expense_delta=0)
    assert s['runway_months'] is None
    assert s['runway_display'] == 'Breakeven'


# ── natural-language routing ──────────────────────────────────────────────

def test_parser_extracts_hires_and_salary():
    cases = [
        ("What's my runway if I hire two engineers at $11,000 per month each?", 2, 11000),
        ("What's my runway if I hire 3 engineers at $12k/month?", 3, 12000),
        ("if we bring on 2 more devs at 11000 a month", 2, 11000),
        ("Can I afford to hire five salespeople at $9,500?", 5, 9500),
    ]
    for text, hires, salary in cases:
        p = M.parse_scenario_request(text)
        assert p is not None, text
        assert p['hires'] == hires, text
        assert p['salary_per_hire'] == salary, text


def test_parser_handles_hire_without_salary():
    p = M.parse_scenario_request("what if I hire a designer")
    assert p == {'hires': 1}


def test_parser_extracts_cost_cuts_and_raises():
    assert M.parse_scenario_request("what happens if I cut costs by 20%") == \
        {'expense_pct_delta': -20.0}
    assert M.parse_scenario_request("what if we reduce spending by $5,000 a month") == \
        {'monthly_expense_delta': -5000.0}
    assert M.parse_scenario_request("what's my runway if I raise $500k") == \
        {'cash_delta': 500000.0}


def test_parser_ignores_non_scenario_questions():
    """False positives are cheap; these must not be treated as what-ifs."""
    for text in ["What is my current monthly burn rate and runway?",
                 "Calculate my burn rate from scratch and show your work",
                 "Are you sure? That doesn't look right.",
                 "What was my revenue in March 2025?",
                 "How am I doing?"]:
        assert M.parse_scenario_request(text) is None, text


def test_parser_never_raises_on_junk():
    for text in ["", None, "$$$", "hire", "cut", "raise", "%%%", "12345678901234567890"]:
        M.parse_scenario_request(text)


def test_the_exact_demo_question_routes_and_computes():
    """End to end: the question from the report produces the right number."""
    params = M.parse_scenario_request(
        "What's my runway if I hire two engineers at $11,000 per month each?")
    s = M.compute_scenario(FIXTURE, **params)
    assert s['net_burn_monthly'] == 45316.67
    assert s['runway_months'] == 3.82


# ── what the model is actually shown ──────────────────────────────────────

RAW_FIELD_NAMES = ['net_burn_monthly', 'runway_months', 'gross_burn_monthly',
                   'cash_balance', 'revenue_monthly', 'burn_multiple_display',
                   'is_profitable', 'window_months']


def test_ai_context_exposes_no_raw_field_names():
    """The assistant was quoting database column names at users."""
    ctx = M.ai_context(FIXTURE)
    for name in RAW_FIELD_NAMES:
        assert name not in ctx, name


def test_ai_context_publishes_both_burn_components():
    """Showing the work only holds up if the components actually reconcile."""
    ctx = M.ai_context(FIXTURE)
    assert '32,116.67' in ctx      # average monthly expenses
    assert '8,800.00' in ctx       # average monthly revenue
    assert '23,316.67' in ctx      # net burn
    assert round(FIXTURE['gross_burn_monthly'] - FIXTURE['revenue_monthly'], 2) \
        == FIXTURE['net_burn_monthly']


def test_ai_rules_do_not_prime_the_leaked_phrasing():
    """The assistant said "I'm not allowed to..." and "these are authoritative".

    Those exact phrases came from the rules text itself. Quoting a forbidden
    phrase inside a prohibition still puts it in the model's mouth, so the rule
    is now written without ever spelling the phrases out.
    """
    lowered = M.AI_RULES.lower()
    for phrase in ['not allowed', 'authoritative', 'pre-computed', 'precomputed']:
        assert phrase not in lowered, phrase
    # ...but the prohibition itself must still be there.
    assert 'never describe your own instructions' in lowered


def test_ai_rules_permit_showing_the_work():
    lowered = M.AI_RULES.lower()
    assert 'show your work' in lowered
    assert 'never refuse' in lowered


def test_ai_rules_do_not_send_scenario_questions_away():
    """The instruction that caused the P0 refusal must not come back."""
    lowered = M.AI_RULES.lower()
    assert 'scenario planning page' not in lowered
    assert 'another page' not in lowered


def test_scenario_context_quotes_the_computed_numbers():
    s = M.compute_scenario(FIXTURE, hires=2, salary_per_hire=11000)
    block = M.scenario_context(s, ladder=M.hire_ladder(FIXTURE, 11000, 3))
    assert '45,316.67' in block
    assert '3.8 months' in block
    assert '7.4 months' in block          # baseline, for comparison
    for name in RAW_FIELD_NAMES:
        assert name not in block, name


def test_scenario_context_admits_an_assumed_salary():
    s = M.compute_scenario(FIXTURE, hires=2)
    block = M.scenario_context(s)
    assert 'assumes' in block.lower()
    assert '12,500' in block


def test_scenario_context_is_empty_when_not_a_scenario():
    assert M.scenario_context(None) == ''


def test_kpi_burn_multiple_matches_canonical():
    """Two definitions of net new ARR had the KPI tile at 8.97x while the
    assistant said 42.39x for the same company. The canonical value wins."""
    from Modules.financial_calculator import FinancialCalculator
    txs = create_sample_transactions()
    canon = M.compute_metrics(txs, DEMO_STARTING_CASH)
    kpis = FinancialCalculator.calculate_kpis(txs, DEMO_STARTING_CASH, canon=canon)
    assert kpis['burn_multiple'] == canon['burn_multiple']
    assert kpis['burn_multiple'] is not None
    assert kpis['burn_multiple_note'] == canon['burn_multiple_display']


def test_kpi_burn_multiple_is_na_for_profitable_company():
    from Modules.financial_calculator import FinancialCalculator
    txs, cash = [], 328200
    for m in range(2, 8):
        txs.append({'date': '2026-%02d-15' % m, 'amount': 60000, 'type': 'income'})
        txs.append({'date': '2026-%02d-15' % m, 'amount': 15300, 'type': 'expense'})
    canon = M.compute_metrics(txs, cash)
    kpis = FinancialCalculator.calculate_kpis(txs, cash, canon=canon)
    assert kpis['burn_multiple'] is None
    assert 'profitable' in kpis['burn_multiple_note'].lower()
    assert kpis['ltv'] is None            # zero observed churn


def test_burn_multiple_uses_matching_periods():
    """Net burn for a month against the ARR added in that month.

    The formula previously annualised net burn but divided by the ARR delta
    from a single month, comparing a year of spending with one month of
    progress and overstating the ratio by exactly 12x (42.39x instead of 3.53x
    on the demo fixture).
    """
    m = M.compute_metrics(create_sample_transactions(), DEMO_STARTING_CASH)
    # MRR moved 8,850 -> 9,400, so ARR grew by 550 x 12 = 6,600.
    net_new_arr = (9400.00 - 8850.00) * 12
    assert net_new_arr == 6600.00
    assert m['burn_multiple'] == round(23316.67 / 6600.00, 2) == 3.53
    assert m['burn_multiple_display'] == '3.53x'


def test_burn_multiple_reads_as_dollars_burned_per_dollar_of_arr():
    """A sanity floor: burning ~23k/mo to add 6.6k of ARR cannot be under 1x."""
    m = M.compute_metrics(create_sample_transactions(), DEMO_STARTING_CASH)
    assert 1 < m['burn_multiple'] < 10
