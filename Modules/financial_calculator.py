"""
Financial Calculator for Fintoit
This module handles all financial calculations for startups
"""

from datetime import datetime, timedelta
from typing import List, Dict
from decimal import Decimal

# Shared guards for metrics that are undefined in some states (LTV at zero
# churn, burn multiple for a profitable company). metrics.py imports nothing
# from this module, so there is no cycle.
from Modules import metrics as canonical_metrics


def to_float(value) -> float:
    """Convert Decimal or any number to float"""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


class FinancialCalculator:
    """
    This class contains all methods to calculate startup financial metrics
    """
    
    @staticmethod
    def calculate_burn_rate(transactions: List[Dict], months: int = 3) -> float:
        """Average monthly GROSS burn over a trailing window.

        Rewritten to delegate. The original summed expenses inside a rolling
        90-day cutoff and then divided by the REQUESTED window rather than the
        number of months it actually found, so an account with one month of
        history under-reported its burn by 3x. Leaving that arithmetic in place
        as a public helper is how it kept getting called by new code.
        """
        canon = canonical_metrics.compute_metrics(transactions, 0,
                                                  window_months=months)
        return canon['gross_burn_monthly']
    
    
    @staticmethod
    def calculate_runway(current_cash: float, monthly_burn: float) -> float:
        """Months until cash runs out, given a NET monthly burn.

        DANGEROUS TO CALL DIRECTLY, and kept only for backwards compatibility.
        Callers historically passed GROSS burn here, which silently ignores
        revenue and hands back a confident finite runway for a profitable
        company — the "2.6 months / risk of closure" reading on an account that
        was generating $575 a month. Use get_all_metrics() or
        Modules.metrics.compute_metrics() instead; both net revenue and return
        None when there is no finite runway.
        """
        if monthly_burn <= canonical_metrics.BREAKEVEN_EPSILON:
            return float('inf')
        if current_cash <= 0:
            return 0.0
        return round(current_cash / monthly_burn, 1)
    
    
    @staticmethod
    def calculate_current_cash(transactions: List[Dict], starting_cash: float = 100000) -> float:
        """Calculate how much cash you have right now"""
        current_cash = to_float(starting_cash)
        
        for transaction in transactions:
            amount = to_float(transaction['amount'])
            if transaction['type'] == 'income':
                current_cash += amount
            else:
                current_cash -= amount
        
        return round(current_cash, 2)
    
    
    @staticmethod
    def calculate_mrr(transactions: List[Dict]) -> float:
        """Revenue in the most recent COMPLETE month.

        Delegates so the product carries exactly one definition of MRR. The
        original counted only rows flagged `recurring`, so an account whose
        income arrived via CSV import or a bank feed — where nothing carries
        that flag — reported $0 MRR while the dashboard showed real revenue.
        That is the same two-surfaces-disagreeing failure in miniature.
        """
        return canonical_metrics.compute_metrics(transactions, 0)['mrr']
    
    
    @staticmethod
    def group_by_month(transactions: List[Dict], starting_cash: float = 100000) -> List[Dict]:
        """Group all transactions by month and calculate monthly metrics"""
        monthly_data = {}
        
        for transaction in transactions:
            trans_date_raw = transaction['date']
            
            # Handle both string and date objects
            if isinstance(trans_date_raw, str):
                trans_date = datetime.strptime(trans_date_raw, '%Y-%m-%d')
            else:
                trans_date = datetime.combine(trans_date_raw, datetime.min.time())
            
            month = trans_date.strftime('%Y-%m')
            
            if month not in monthly_data:
                monthly_data[month] = {
                    'month': month,
                    'revenue': 0.0,
                    'expenses': 0.0
                }
            
            amount = to_float(transaction['amount'])
            if transaction['type'] == 'income':
                monthly_data[month]['revenue'] += amount
            else:
                monthly_data[month]['expenses'] += amount
        
        sorted_months = sorted(monthly_data.keys())
        
        result = []
        cumulative_cash = to_float(starting_cash)
        
        for month in sorted_months:
            data = monthly_data[month]
            burn_rate = data['expenses'] - data['revenue']
            cumulative_cash -= burn_rate
            
            result.append({
                'month': month,
                'revenue': round(data['revenue'], 2),
                'expenses': round(data['expenses'], 2),
                'burn_rate': round(burn_rate, 2),
                'cash_balance': round(cumulative_cash, 2)
            })
        
        return result
    
    
    @staticmethod
    def calculate_growth_rate(values: List[float]) -> float:
        """Calculate average growth rate from a series of values"""
        if len(values) < 2:
            return 0
        
        growth_rates = []
        
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                growth = ((values[i] - values[i - 1]) / values[i - 1]) * 100
                growth_rates.append(growth)
        
        if growth_rates:
            avg_growth = sum(growth_rates) / len(growth_rates)
            return round(avg_growth, 2)
        
        return 0
    
    
    @staticmethod
    def get_all_metrics(transactions: List[Dict], starting_cash: float = 100000) -> Dict:
        """Calculate ALL metrics at once — delegating to the canonical module.

        This used to compute burn, runway, MRR and growth itself, and it carried
        every defect the canonical module was written to fix:

          * calculate_burn_rate() divided by a HARDCODED 3 regardless of how many
            months of data existed. An account with one month of history and $612
            of expenses reported $211.82/mo.
          * calculate_runway() divided cash by GROSS burn and never netted
            revenue, so any company with expenses got a finite runway — including
            profitable ones. That produced "2.6 months" and an AI-generated
            "risk of closure" warning for an account generating $575/mo.
          * revenue growth included the CURRENT PARTIAL MONTH, so on the 5th of
            any month a company with no revenue booked yet showed -100% growth.

        Migrating the dashboard and the AI chat away from this function fixed
        those two surfaces but left /ai-insights, /export-pdf, /custom-report,
        /projections and /goals still reading the broken figures. Rather than
        patch five call sites and risk missing a sixth, the function itself now
        delegates: every caller, present and future, gets the canonical numbers.

        Contract note: runway_months is None whenever the company is profitable
        or at breakeven. That was already this function's contract (it returned
        None for infinite runway), so callers that guard with `or 'N/A'` keep
        working — but callers must never format it unconditionally.
        """
        starting_cash = to_float(starting_cash)
        canon = canonical_metrics.compute_metrics(transactions, starting_cash)

        # Charts and tables still want every month INCLUDING the current partial
        # one, so this stays as-is. Only the averaged figures exclude it.
        monthly_metrics = FinancialCalculator.group_by_month(transactions, starting_cash)

        # Growth over COMPLETE months only. Comparing a part-way-through month
        # against a finished one is what produced the -100% reading.
        complete = [m for m in canon['monthly'] if not m['is_partial']]
        revenue_growth = 0
        if len(complete) >= 2:
            revenue_growth = FinancialCalculator.calculate_growth_rate(
                [m['revenue'] for m in complete])

        return {
            'current_cash': canon['cash_balance'],
            'monthly_burn': canon['gross_burn_monthly'],
            'runway_months': canon['runway_months'],     # None when profitable
            'mrr': canon['mrr'],
            'revenue_growth': revenue_growth,
            'monthly_breakdown': monthly_metrics,
            # Additional canonical fields so consumers can render honestly
            # instead of inferring from a bare number.
            'net_burn': canon['net_burn_monthly'],
            'revenue_monthly': canon['revenue_monthly'],
            'runway_display': canon['runway_display'],
            'is_profitable': canon['is_profitable'],
            'window_months': canon['window_months'],
            'months_of_data': canon['months_of_data'],
            'has_data': canon['has_data'],
        }

    @staticmethod
    def forecast_cash_flow(monthly_data: List[Dict], current_cash: float, months_ahead: int = 6) -> List[Dict]:
        """Forecast future cash flow based on historical trends"""
        if len(monthly_data) < 2:
            return []

        # Use last 3 months to calculate average revenue/expense trends
        recent = monthly_data[-3:] if len(monthly_data) >= 3 else monthly_data

        avg_revenue = sum(m['revenue'] for m in recent) / len(recent)
        avg_expenses = sum(m['expenses'] for m in recent) / len(recent)

        # Calculate month-over-month growth trends
        if len(monthly_data) >= 3:
            rev_values = [m['revenue'] for m in monthly_data[-3:]]
            exp_values = [m['expenses'] for m in monthly_data[-3:]]
            rev_growth = (rev_values[-1] - rev_values[0]) / rev_values[0] / 2 if rev_values[0] > 0 else 0
            exp_growth = (exp_values[-1] - exp_values[0]) / exp_values[0] / 2 if exp_values[0] > 0 else 0
        else:
            rev_growth = 0
            exp_growth = 0

        # Cap growth rates to realistic bounds
        rev_growth = max(min(rev_growth, 0.20), -0.20)
        exp_growth = max(min(exp_growth, 0.10), -0.10)

        # Generate future months
        from datetime import date
        last_month = monthly_data[-1]['month']
        last_date = datetime.strptime(last_month + '-01', '%Y-%m-%d')

        forecast = []
        cash = current_cash

        for i in range(1, months_ahead + 1):
            # Apply growth trend
            projected_revenue = avg_revenue * (1 + rev_growth) ** i
            projected_expenses = avg_expenses * (1 + exp_growth) ** i
            projected_burn = projected_expenses - projected_revenue
            cash -= projected_burn

            # Get future month label
            month_num = last_date.month + i
            year = last_date.year + (month_num - 1) // 12
            month = ((month_num - 1) % 12) + 1
            label = f"{year}-{month:02d}"

            forecast.append({
                'month': label,
                'revenue': round(projected_revenue, 2),
                'expenses': round(projected_expenses, 2),
                'burn_rate': round(projected_burn, 2),
                'cash_balance': round(cash, 2),
                'is_forecast': True
            })

        return forecast
    
    @staticmethod
    def calculate_kpis(transactions: List[Dict], starting_cash: float = 0,
                       canon: Dict = None) -> Dict:
        """Calculate all startup KPIs.

        canon -- the canonical metrics object from Modules.metrics. When given,
                 burn multiple is derived from the same net burn the dashboard
                 tiles show, instead of a second independently computed figure.

        LTV and burn multiple return None where they are mathematically
        undefined, and the UI renders that as N/A. Printing a confident number
        built on a silent default -- a $1,566,540 LTV at 0% churn -- is the
        first thing a sophisticated user notices, and it discredits every other
        figure on the page.
        """
        monthly = FinancialCalculator.group_by_month(transactions, starting_cash)

        if not monthly:
            return {
                'cac': 0, 'ltv': None, 'ltv_cac_ratio': None,
                'churn_rate': 0, 'gross_margin': 0,
                'mom_growth': 0, 'burn_multiple': None,
                'arr': 0, 'mrr': 0, 'nrr': 0,
                'mom_trend': [],
                'ltv_note': 'Needs churn data',
                'burn_multiple_note': '—',
            }

        # Last 3 months
        recent = monthly[-3:] if len(monthly) >= 3 else monthly
        last = monthly[-1]
        prev = monthly[-2] if len(monthly) >= 2 else None

        # MRR & ARR
        mrr = last['revenue']
        arr = mrr * 12

        # Gross Margin — (revenue - COGS) / revenue
        # We use non-salary, non-marketing expenses as COGS proxy
        cogs_categories = ['Software', 'Infrastructure', 'Hosting', 'COGS', 'Cost of Goods']
        total_cogs = sum(
            to_float(t['amount']) for t in transactions
            if t['type'] == 'expense' and t.get('category', '') in cogs_categories
        )
        total_revenue = sum(to_float(t['amount']) for t in transactions if t['type'] == 'income')
        gross_margin = round(((total_revenue - total_cogs) / total_revenue * 100) if total_revenue > 0 else 0, 1)

        # MoM Revenue Growth
        mom_growth = 0
        if prev and prev['revenue'] > 0:
            mom_growth = round(((last['revenue'] - prev['revenue']) / prev['revenue']) * 100, 1)

        # CAC — Marketing spend / new customers (estimated as 1 per $5000 marketing)
        marketing_spend = sum(
            to_float(t['amount']) for t in transactions
            if t['type'] == 'expense' and t.get('category', '') in ['Marketing', 'Sales', 'Advertising']
        )
        # Estimate customers from income transactions
        income_txs = [t for t in transactions if t['type'] == 'income']
        estimated_customers = max(len(set(t.get('description', '') for t in income_txs)), 1)
        cac = round(marketing_spend / estimated_customers, 2) if estimated_customers > 0 else 0

        # Churn Rate — estimated from revenue decline months
        churn_months = sum(1 for i in range(1, len(monthly)) if monthly[i]['revenue'] < monthly[i-1]['revenue'])
        churn_rate = round((churn_months / max(len(monthly) - 1, 1)) * 100, 1)

        # LTV — ARPU / churn rate.
        # This previously multiplied average revenue per customer by an
        # UNSTATED 12-month lifetime, which silently substitutes an ~8.3%/month
        # churn floor. On a company with no observed churn that produced a
        # confident finite number where the honest answer is "undefined".
        # metrics.ltv() returns None whenever churn <= 0.
        arpu = (mrr / estimated_customers) if estimated_customers > 0 else 0
        ltv = canonical_metrics.ltv(arpu, churn_rate)
        ltv_cac_ratio = round(ltv / cac, 1) if (ltv is not None and cac > 0) else None
        ltv_note = ('Needs churn data' if ltv is None
                    else 'ARPU ÷ %.1f%% monthly churn' % churn_rate)

        # Burn Multiple — net burn / net new ARR. Undefined unless the company
        # is both burning and growing; it previously divided GROSS burn by new
        # ARR and reported 0 for a profitable company rather than N/A.
        #
        # This MUST come from the canonical object when one is supplied. This
        # function measured net new ARR first-month-to-last while metrics.py
        # measures it month-over-month, so the KPI tile said 8.97x while the
        # assistant said 42.39x for the same company -- the same class of
        # two-surfaces-disagreeing bug the canonical module exists to prevent.
        if canon is not None:
            net_burn = canon['net_burn_monthly']
            burn_multiple = canon['burn_multiple']
        else:
            net_burn = sum(m['expenses'] - m['revenue'] for m in recent) / len(recent)
            avg_new_arr = (last['revenue'] - monthly[0]['revenue']) * 12 if len(monthly) > 1 else 0
            burn_multiple = (round(net_burn / (avg_new_arr / 12), 2)
                             if avg_new_arr > 0 and net_burn > 0 else None)

        if burn_multiple is not None:
            burn_multiple_note = '%.2fx' % burn_multiple
        else:
            burn_multiple_note = ('N/A (profitable)' if net_burn <= 0
                                  else 'N/A (no revenue growth)')

        # NRR — net revenue retention (revenue this period / revenue last period)
        if prev and prev['revenue'] > 0:
            nrr = round((last['revenue'] / prev['revenue']) * 100, 1)
        else:
            nrr = 100.0

        # MoM trend for sparkline
        mom_trend = [round(m['revenue'], 2) for m in monthly[-6:]]

        return {
            'cac': cac,
            'ltv': ltv,
            'ltv_cac_ratio': ltv_cac_ratio,
            'ltv_note': ltv_note,
            'churn_rate': churn_rate,
            'gross_margin': gross_margin,
            'mom_growth': mom_growth,
            'burn_multiple': burn_multiple,
            'burn_multiple_note': burn_multiple_note,
            'arr': round(arr, 2),
            'mrr': round(mrr, 2),
            'nrr': nrr,
            'mom_trend': mom_trend
        }
    
    @staticmethod
    def calculate_projections(transactions: List[Dict], starting_cash: float,
                            revenue_growth_rate: float = 0.05,
                            expense_growth_rate: float = 0.03,
                            months: int = 12) -> List[Dict]:
        """Project financials for the next N months"""
        monthly = FinancialCalculator.group_by_month(transactions, starting_cash)

        # Use last 3 months average as baseline
        recent = monthly[-3:] if len(monthly) >= 3 else monthly
        if not recent:
            return []

        base_revenue = sum(m['revenue'] for m in recent) / len(recent)
        base_expenses = sum(m['expenses'] for m in recent) / len(recent)
        current_cash = starting_cash
        if monthly:
            current_cash = monthly[-1]['cash_balance']

        from datetime import datetime, timedelta
        from dateutil.relativedelta import relativedelta
        today = datetime.now()
        projections = []

        for i in range(1, months + 1):
            proj_revenue = base_revenue * ((1 + revenue_growth_rate) ** i)
            proj_expenses = base_expenses * ((1 + expense_growth_rate) ** i)
            proj_net = proj_revenue - proj_expenses
            current_cash += proj_net

            month_date = today.replace(day=1) + relativedelta(months=i)
            month_label = month_date.strftime('%Y-%m')

            projections.append({
                'month': month_label,
                'revenue': round(proj_revenue, 2),
                'expenses': round(proj_expenses, 2),
                'net': round(proj_net, 2),
                'cash_balance': round(current_cash, 2),
                'is_projection': True
            })

        return projections


# Demo dataset — a realistic default-dead pre-seed company.
#
# Deliberately NOT flat: expenses vary month to month (Delaware C-corp
# conversion, an annual insurance premium, a design sprint spanning two months),
# because flat data hides averaging bugs — a wrong denominator still produces a
# plausible number when every month is identical. That is exactly how the
# $10,200 burn defect survived.
#
# It doubles as the burn/runway regression fixture. With DEMO_STARTING_CASH the
# canonical metrics must come out as:
#     cash_balance        173,000.00
#     gross_burn_monthly   32,116.67
#     net_burn_monthly     23,316.67
#     runway_months              7.42
#     mrr                   9,400.00
# If any surface disagrees with those, it is computing independently.
#
# A planted anomaly sits in the most recent month: the same $1,800 Datadog
# charge twice, three days apart.
#
# (month_index, day, description, category, amount, type)
# month_index 0 = six months ago ... 5 = last complete month. Months roll
# forward with the calendar so the demo never shows stale dates, while the
# metrics above stay identical whenever it runs.
DEMO_ROWS = [
    (0,  1, 'Stripe payout - subscription revenue', 'Revenue',              6800, 'income'),
    (0, 15, 'Salaries - monthly',                   'Salaries',            21000, 'expense'),
    (0,  3, 'Software & Tools - monthly',           'Software & Tools',     2450, 'expense'),
    (0,  8, 'Marketing - monthly',                  'Marketing',            3200, 'expense'),
    (0,  5, 'Office & Admin - monthly',             'Office & Admin',       1150, 'expense'),

    (1,  1, 'Stripe payout - subscription revenue', 'Revenue',              7350, 'income'),
    (1, 15, 'Salaries - monthly',                   'Salaries',            21000, 'expense'),
    (1,  3, 'Software & Tools - monthly',           'Software & Tools',     2450, 'expense'),
    (1,  8, 'Marketing - monthly',                  'Marketing',            3200, 'expense'),
    (1,  5, 'Office & Admin - monthly',             'Office & Admin',       1150, 'expense'),
    (1, 19, 'Delaware C-corp conversion + SAFE docs', 'Legal & Professional', 4500, 'expense'),

    (2,  1, 'Stripe payout - subscription revenue', 'Revenue',              7900, 'income'),
    (2, 15, 'Salaries - monthly',                   'Salaries',            21000, 'expense'),
    (2,  3, 'Software & Tools - monthly',           'Software & Tools',     2450, 'expense'),
    (2,  8, 'Marketing - monthly',                  'Marketing',            3200, 'expense'),
    (2,  5, 'Office & Admin - monthly',             'Office & Admin',       1150, 'expense'),
    (2, 19, 'Annual D&O + general liability (paid yearly)', 'Insurance',    3600, 'expense'),

    (3,  1, 'Stripe payout - subscription revenue', 'Revenue',              8150, 'income'),
    (3, 15, 'Salaries - monthly',                   'Salaries',            21000, 'expense'),
    (3,  3, 'Software & Tools - monthly',           'Software & Tools',     2450, 'expense'),
    (3,  8, 'Marketing - monthly',                  'Marketing',            3200, 'expense'),
    (3,  5, 'Office & Admin - monthly',             'Office & Admin',       1150, 'expense'),
    (3, 19, 'Brand + design sprint',                'Contractors',          5800, 'expense'),

    (4,  1, 'Stripe payout - subscription revenue', 'Revenue',              8850, 'income'),
    (4, 15, 'Salaries - monthly',                   'Salaries',            21000, 'expense'),
    (4,  3, 'Software & Tools - monthly',           'Software & Tools',     2450, 'expense'),
    (4,  8, 'Marketing - monthly',                  'Marketing',            3200, 'expense'),
    (4,  5, 'Office & Admin - monthly',             'Office & Admin',       1150, 'expense'),
    (4, 19, 'Design sprint, final invoice',         'Contractors',          2400, 'expense'),
    (4, 19, 'Trademark filing',                     'Legal & Professional', 1150, 'expense'),

    (5,  1, 'Stripe payout - subscription revenue', 'Revenue',              9400, 'income'),
    (5, 15, 'Salaries - monthly',                   'Salaries',            21000, 'expense'),
    (5,  3, 'Software & Tools - monthly',           'Software & Tools',     2450, 'expense'),
    (5,  8, 'Marketing - monthly',                  'Marketing',            3200, 'expense'),
    (5,  5, 'Office & Admin - monthly',             'Office & Admin',       1150, 'expense'),
    (5, 19, 'Datadog annual - CHARGED TWICE (anomaly)', 'Software & Tools', 1800, 'expense'),
    (5, 22, 'Datadog annual - CHARGED TWICE (anomaly)', 'Software & Tools', 1800, 'expense'),
]

# Opening cash chosen so the demo ends at exactly $173,000 of cash
# (total revenue 48,450 - total expenses 187,850 = -139,400).
DEMO_STARTING_CASH = 312400.00

# Categories that recur every month, used to set the recurring flag.
_DEMO_RECURRING = {'Revenue', 'Salaries', 'Software & Tools', 'Marketing', 'Office & Admin'}


def create_sample_transactions(as_of=None):
    """Build the demo dataset, anchored to the six most recent COMPLETE months.

    Amounts are stored as positive magnitudes with an explicit type, matching the
    rest of the app (the source CSV writes expenses as negatives).
    """
    from calendar import monthrange
    from dateutil.relativedelta import relativedelta
    today = as_of or datetime.now().date()
    first_of_this_month = today.replace(day=1)

    transactions = []
    for month_index, day, description, category, amount, ttype in DEMO_ROWS:
        # month_index 5 -> last complete month, 0 -> six months ago
        month_start = first_of_this_month - relativedelta(months=(6 - month_index))
        last_day = monthrange(month_start.year, month_start.month)[1]
        d = month_start.replace(day=min(day, last_day))
        transactions.append({
            'date': d.strftime('%Y-%m-%d'),
            'amount': abs(float(amount)),
            'type': ttype,
            'category': category,
            'description': description,
            'recurring': category in _DEMO_RECURRING,
        })
    return transactions


if __name__ == '__main__':
    print("Testing Financial Calculator...")
    transactions = create_sample_transactions()
    metrics = FinancialCalculator.get_all_metrics(transactions, 150000)
    
    print(f"\nCurrent Cash: ${metrics['current_cash']:,.2f}")
    print(f"Monthly Burn: ${metrics['monthly_burn']:,.2f}")
    print(f"Runway: {metrics['runway_months']} months" if metrics['runway_months'] else "Runway: Infinite")
    print(f"MRR: ${metrics['mrr']:,.2f}")
    print("\nTest complete!")


class AnomalyDetector:
    """Detects unusual patterns in financial data"""

    @staticmethod
    def detect(transactions: list, monthly_data: list, as_of=None) -> list:
        """Flag unusual months.

        Every check here compares the LATEST month against the average of the
        months before it, so the current, part-way-through month must be
        excluded. Including it meant that on the 5th of any month a healthy,
        growing company was told:

            "Revenue drop detected — revenue in 2026-08 was $0, down 100% from
             your average of $1,200/month."
            "Zero revenue month — no revenue recorded in 2026-08 despite $20 in
             expenses."

        Both fired purely because the month had barely started. That is a
        guaranteed false alarm for every customer at the start of every month,
        and it trains people to ignore the alerts that matter.
        """
        anomalies = []

        as_of = as_of or datetime.now().date()
        current_month = '%04d-%02d' % (as_of.year, as_of.month)

        monthly_data = [m for m in monthly_data if m['month'] < current_month]
        if len(monthly_data) < 3:
            return anomalies

        # --- Expense spike by category ---
        # Group expenses by category per month
        cat_monthly = {}
        for t in transactions:
            if t['type'] != 'expense':
                continue
            date_raw = t['date']
            if isinstance(date_raw, str):
                month = date_raw[:7]
            else:
                month = date_raw.strftime('%Y-%m')
            if month >= current_month:
                continue          # partial month — not comparable yet
            cat = t['category']
            amt = float(t['amount'])
            if cat not in cat_monthly:
                cat_monthly[cat] = {}
            cat_monthly[cat][month] = cat_monthly[cat].get(month, 0) + amt

        for cat, monthly in cat_monthly.items():
            if len(monthly) < 3:
                continue
            sorted_months = sorted(monthly.keys())
            values = [monthly[m] for m in sorted_months]
            avg = sum(values[:-1]) / len(values[:-1])
            latest = values[-1]
            latest_month = sorted_months[-1]
            if avg > 0 and latest > avg * 1.5:
                pct = round((latest - avg) / avg * 100)
                anomalies.append({
                    'type': 'expense_spike',
                    'severity': 'high' if latest > avg * 2 else 'medium',
                    'icon': '🔴' if latest > avg * 2 else '🟡',
                    'title': f'{cat} spending spike',
                    'message': f'{cat} spending in {latest_month} was ${latest:,.0f}, {pct}% above your average of ${avg:,.0f}/month.',
                    'month': latest_month
                })

        # --- Revenue drop ---
        if len(monthly_data) >= 3:
            revenues = [m['revenue'] for m in monthly_data]
            avg_rev = sum(revenues[:-1]) / len(revenues[:-1])
            latest_rev = revenues[-1]
            latest_month = monthly_data[-1]['month']
            if avg_rev > 0 and latest_rev < avg_rev * 0.7:
                pct = round((avg_rev - latest_rev) / avg_rev * 100)
                anomalies.append({
                    'type': 'revenue_drop',
                    'severity': 'high',
                    'icon': '🔴',
                    'title': 'Revenue drop detected',
                    'message': f'Revenue in {latest_month} was ${latest_rev:,.0f}, down {pct}% from your average of ${avg_rev:,.0f}/month.',
                    'month': latest_month
                })

        # --- Burn rate surge ---
        if len(monthly_data) >= 3:
            burns = [m['burn_rate'] for m in monthly_data]
            avg_burn = sum(burns[:-1]) / len(burns[:-1])
            latest_burn = burns[-1]
            latest_month = monthly_data[-1]['month']
            if avg_burn > 0 and latest_burn > avg_burn * 1.4:
                pct = round((latest_burn - avg_burn) / avg_burn * 100)
                anomalies.append({
                    'type': 'burn_surge',
                    'severity': 'high' if latest_burn > avg_burn * 2 else 'medium',
                    'icon': '🔴' if latest_burn > avg_burn * 2 else '🟡',
                    'title': 'Burn rate surging',
                    'message': f'Your burn rate hit ${latest_burn:,.0f} in {latest_month}, {pct}% higher than your average of ${avg_burn:,.0f}/month.',
                    'month': latest_month
                })

        # --- No revenue months ---
        for m in monthly_data[-3:]:
            if m['revenue'] == 0 and m['expenses'] > 0:
                anomalies.append({
                    'type': 'zero_revenue',
                    'severity': 'high',
                    'icon': '🔴',
                    'title': 'Zero revenue month',
                    'message': f'No revenue was recorded in {m["month"]} despite ${m["expenses"]:,.0f} in expenses.',
                    'month': m['month']
                })

        # Sort: high severity first
        anomalies.sort(key=lambda x: 0 if x['severity'] == 'high' else 1)
        return anomalies