"""
Tax estimation for Fintoit.

Replaces the previous flat "15.3% SE tax + 22% income tax" calculation, which
was wrong for most users:
  * Self-employment tax does not apply to C-corps or to S-corp distributions,
    yet it was applied to everyone.
  * The 12.4% Social Security portion is capped at the annual wage base; it was
    applied uncapped.
  * SE tax applies to 92.35% of net earnings, not 100%.
  * Half of SE tax is deductible against income tax; that was not accounted for.
  * 22% is a marginal bracket, not an effective rate, and it ignores filing
    status, other household income, the QBI deduction, and the standard
    deduction (which alone zeroes out small net incomes).

This module instead produces an explicit LOW-HIGH RANGE, driven by the company's
entity type and state, and returns the assumptions it made so the UI can show
them. It deliberately does not pretend to know the owner's personal return.

None of this is tax advice. Rates are top-line figures as of RATES_AS_OF and
must be verified with an accountant.
"""

RATES_AS_OF = 2025

# ── Federal ─────────────────────────────────────────────────────────────
FEDERAL_CORPORATE_RATE = 0.21          # flat, C-corps
SS_WAGE_BASE = 176_100                 # 2025 Social Security wage base
SS_RATE = 0.124                        # Social Security portion of SE tax
MEDICARE_RATE = 0.029                  # Medicare portion (uncapped)
SE_TAXABLE_PORTION = 0.9235            # SE tax applies to 92.35% of net earnings

# Plausible federal marginal brackets for a pass-through owner. Used to build a
# range, not to claim knowledge of the owner's actual bracket.
PASSTHROUGH_FED_LOW = 0.10
PASSTHROUGH_FED_HIGH = 0.32

# Rough standard deduction floor — below this, federal income tax on
# pass-through business income is commonly zero for an owner with no other
# income. Used only to keep the LOW end of the range honest.
STANDARD_DEDUCTION_FLOOR = 15_000

ENTITY_TYPES = {
    'c_corp': 'C-Corporation',
    's_corp': 'S-Corporation',
    'llc': 'LLC / Partnership',
    'sole_prop': 'Sole Proprietor',
}

NO_INCOME_TAX_NOTE = {
    'TX': 'Texas has no personal income tax, but levies a franchise (margin) tax on businesses.',
    'WA': 'Washington has no income tax, but levies a B&O gross receipts tax.',
    'NV': 'Nevada has no income tax, but levies a commerce tax on large gross receipts.',
    'OH': 'Ohio has no corporate income tax, but levies a Commercial Activity Tax (CAT).',
    'SD': 'South Dakota has no state income tax.',
    'WY': 'Wyoming has no state income tax.',
    'AK': 'Alaska has no personal income tax (corporations are taxed).',
    'FL': 'Florida has no personal income tax (corporations are taxed).',
    'TN': 'Tennessee has no personal income tax (corporations are taxed).',
    'NH': 'New Hampshire does not tax wage income (business taxes apply).',
    'DE': 'Delaware also charges an annual franchise tax, which this estimate excludes.',
}

# Top marginal state rates as of RATES_AS_OF. Individual rates apply to
# pass-through owners; corporate rates apply to C-corps. Approximate top-line
# figures — brackets, surtaxes and local taxes are not modelled.
STATE_RATES = {
    'AL': (0.050, 0.065), 'AK': (0.000, 0.094), 'AZ': (0.025, 0.049),
    'AR': (0.039, 0.043), 'CA': (0.133, 0.0884), 'CO': (0.044, 0.044),
    'CT': (0.0699, 0.075), 'DE': (0.066, 0.087), 'DC': (0.1075, 0.0825),
    'FL': (0.000, 0.055), 'GA': (0.0539, 0.0539), 'HI': (0.110, 0.064),
    'ID': (0.05695, 0.05695), 'IL': (0.0495, 0.095), 'IN': (0.0305, 0.049),
    'IA': (0.038, 0.071), 'KS': (0.057, 0.065), 'KY': (0.040, 0.050),
    'LA': (0.030, 0.075), 'ME': (0.0715, 0.0893), 'MD': (0.0575, 0.0825),
    'MA': (0.090, 0.080), 'MI': (0.0425, 0.060), 'MN': (0.0985, 0.098),
    'MS': (0.047, 0.050), 'MO': (0.048, 0.040), 'MT': (0.059, 0.0675),
    'NE': (0.0584, 0.0584), 'NV': (0.000, 0.000), 'NH': (0.000, 0.075),
    'NJ': (0.1075, 0.090), 'NM': (0.059, 0.059), 'NY': (0.109, 0.0725),
    'NC': (0.045, 0.025), 'ND': (0.025, 0.0431), 'OH': (0.035, 0.000),
    'OK': (0.0475, 0.040), 'OR': (0.099, 0.076), 'PA': (0.0307, 0.0799),
    'RI': (0.0599, 0.070), 'SC': (0.062, 0.050), 'SD': (0.000, 0.000),
    'TN': (0.000, 0.065), 'TX': (0.000, 0.000), 'UT': (0.0455, 0.0455),
    'VT': (0.0875, 0.085), 'VA': (0.0575, 0.060), 'WA': (0.000, 0.000),
    'WV': (0.0512, 0.065), 'WI': (0.0765, 0.079), 'WY': (0.000, 0.000),
}

STATE_NAMES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
    'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
    'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana',
    'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana',
    'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan',
    'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana',
    'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey',
    'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina',
    'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma', 'OR': 'Oregon',
    'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
    'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
    'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}


def _self_employment_tax(net_income):
    """Correct SE tax: 92.35% base, SS capped at the wage base, Medicare uncapped."""
    if net_income <= 0:
        return 0.0, 0.0
    se_base = net_income * SE_TAXABLE_PORTION
    social_security = min(se_base, SS_WAGE_BASE) * SS_RATE
    medicare = se_base * MEDICARE_RATE
    total = social_security + medicare
    # Half of SE tax is deductible against income tax.
    return round(total, 2), round(total / 2.0, 2)


def estimate(net_income, entity_type=None, state=None):
    """Return an honest low-high tax estimate plus the assumptions behind it.

    net_income  -- business net income for the year
    entity_type -- one of ENTITY_TYPES keys; None means "not set"
    state       -- two-letter state code; None means "not set"
    """
    net_income = float(net_income or 0)
    entity_type = (entity_type or '').strip().lower() or None
    state = (state or '').strip().upper() or None

    result = {
        'configured': bool(entity_type),
        'entity_type': entity_type,
        'entity_label': ENTITY_TYPES.get(entity_type),
        'state': state,
        'state_label': STATE_NAMES.get(state),
        'rates_as_of': RATES_AS_OF,
        'net_income': round(net_income, 2),
        'low': 0.0,
        'high': 0.0,
        'components': [],
        'assumptions': [],
        'notes': [],
    }

    if not entity_type:
        result['assumptions'].append(
            'Set your entity type and state to see an estimate.')
        return result

    if net_income <= 0:
        result['notes'].append(
            'No net income for this period, so no income tax is estimated. '
            'Minimum state franchise or filing fees may still apply.')
        return result

    ind_rate, corp_rate = STATE_RATES.get(state, (0.0, 0.0))
    low = high = 0.0

    if entity_type == 'c_corp':
        federal = net_income * FEDERAL_CORPORATE_RATE
        result['components'].append(
            {'label': 'Federal corporate income tax (21%)',
             'low': round(federal, 2), 'high': round(federal, 2)})
        low += federal
        high += federal
        if state:
            st = net_income * corp_rate
            result['components'].append(
                {'label': '%s corporate income tax (%.2f%%)' % (result['state_label'], corp_rate * 100),
                 'low': round(st, 2), 'high': round(st, 2)})
            low += st
            high += st
        result['assumptions'] += [
            'Taxed at the entity level; owners are not taxed here on distributions.',
            'No net operating loss carryforwards, credits, or deductions beyond '
            'the expenses already in your books.',
        ]
        result['notes'].append(
            'Dividends paid to shareholders are taxed again on their personal '
            'returns (double taxation) and are not included here.')

    else:
        # Pass-through: the entity generally owes no federal income tax; the
        # owner does, and the amount depends on their personal return.
        se_tax = se_deduction = 0.0
        if entity_type in ('llc', 'sole_prop'):
            se_tax, se_deduction = _self_employment_tax(net_income)
            result['components'].append(
                {'label': 'Self-employment tax (Social Security capped at $%s + Medicare)' % f"{SS_WAGE_BASE:,}",
                 'low': se_tax, 'high': se_tax})
            low += se_tax
            high += se_tax
            result['assumptions'].append(
                'Self-employment tax applies to 92.35% of net earnings; the '
                'Social Security portion stops at the annual wage base.')
        else:  # s_corp
            result['assumptions'].append(
                'S-corp distributions are not subject to self-employment tax, '
                'but the IRS requires owner-employees to take reasonable W-2 '
                'salary, which carries payroll tax not estimated here.')

        taxable = max(0.0, net_income - se_deduction)
        fed_low = 0.0 if taxable <= STANDARD_DEDUCTION_FLOOR else taxable * PASSTHROUGH_FED_LOW
        fed_high = taxable * PASSTHROUGH_FED_HIGH
        result['components'].append(
            {'label': 'Federal income tax on pass-through income (owner\'s return)',
             'low': round(fed_low, 2), 'high': round(fed_high, 2)})
        low += fed_low
        high += fed_high

        if state:
            st_low = 0.0 if ind_rate == 0 else taxable * ind_rate * 0.5
            st_high = taxable * ind_rate
            result['components'].append(
                {'label': '%s income tax (top rate %.2f%%)' % (result['state_label'], ind_rate * 100),
                 'low': round(st_low, 2), 'high': round(st_high, 2)})
            low += st_low
            high += st_high

        result['assumptions'] += [
            'Business income flows to the owner\'s personal return; the real '
            'figure depends on filing status, other household income, and credits.',
            'Half of self-employment tax is deducted before estimating income tax.',
            'The low end assumes the standard deduction absorbs most income; the '
            'high end assumes a %d%% federal marginal bracket.' % int(PASSTHROUGH_FED_HIGH * 100),
        ]
        result['notes'].append(
            'The Qualified Business Income (QBI) deduction can reduce federal '
            'tax by up to 20% for eligible pass-throughs and is not applied here.')

    if state and state in NO_INCOME_TAX_NOTE:
        result['notes'].append(NO_INCOME_TAX_NOTE[state])
    if not state:
        result['assumptions'].append(
            'No state selected, so the estimate covers federal tax only.')

    result['low'] = round(low, 2)
    result['high'] = round(high, 2)
    result['effective_low'] = round((low / net_income * 100), 1) if net_income else 0.0
    result['effective_high'] = round((high / net_income * 100), 1) if net_income else 0.0
    return result
