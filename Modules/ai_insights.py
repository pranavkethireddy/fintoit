"""
AI-Powered Financial Insights for Fintoit
Using FREE Groq API (Updated Model)
"""

import os
from groq import Groq
from datetime import datetime
from typing import Dict, List

# Initialize Groq client
client = Groq(api_key=os.getenv('GROQ_API_KEY'))


class AIFinancialAdvisor:
    """AI-powered financial analysis using Groq"""
    
    @staticmethod
    def generate_insights(metrics: Dict, monthly_data: List[Dict], transactions: List[Dict]) -> Dict:
        """Generate AI-powered financial insights"""
        
        financial_summary = AIFinancialAdvisor._prepare_summary(metrics, monthly_data, transactions)
        insights = AIFinancialAdvisor._call_groq(financial_summary)
        
        return insights
    
    @staticmethod
    def _prepare_summary(metrics: Dict, monthly_data: List[Dict], transactions: List[Dict]) -> str:
        """Prepare financial data summary.

        Two things went wrong in the previous version and both reached users:

        1. It formatted runway as `{runway_months:.1f} months` unconditionally.
           runway_months is None for a profitable company, so this raises
           TypeError the moment the metrics are correct.
        2. It labelled GROSS burn as "Monthly Burn Rate" and never mentioned
           revenue, so the model reasoned about a company that only spends. On a
           profitable account that produced an URGENT WARNING about running out
           of cash "within 2.6 months" — the opposite of the truth.

        The figures are now described in the same plain-English, period-labelled
        way the chat assistant receives them.
        """
        cash = metrics['current_cash']
        gross = metrics['monthly_burn']
        net = metrics.get('net_burn')
        revenue = metrics.get('revenue_monthly')
        window = metrics.get('window_months') or 0
        plural = '' if window == 1 else 's'

        summary = "\nFINANCIAL OVERVIEW:\n"
        summary += "- Cash in the bank right now: $%s\n" % f"{cash:,.2f}"
        summary += "- Average monthly expenses over the last %d complete month%s: $%s\n" % (
            window, plural, f"{gross:,.2f}")
        if revenue is not None:
            summary += "- Average monthly revenue over the same period: $%s\n" % f"{revenue:,.2f}"

        if net is None:
            summary += "- Net burn: $%s per month\n" % f"{gross:,.2f}"
        elif net > 0:
            summary += ("- Net burn: $%s per month (expenses minus revenue: "
                        "the company loses this much each month)\n" % f"{net:,.2f}")
        else:
            summary += ("- Net cash generated: $%s per month (revenue exceeds "
                        "expenses: the company MAKES this much each month and "
                        "its cash balance is GROWING)\n" % f"{abs(net):,.2f}")

        if metrics.get('runway_months') is None:
            summary += ("- Runway: not applicable. The company is %s, so cash is "
                        "not running down and there is no finite runway. Do NOT "
                        "warn about running out of cash, and do NOT state a "
                        "number of months.\n"
                        % str(metrics.get('runway_display', 'profitable')).lower())
        else:
            summary += "- Runway: %.1f months (cash divided by net burn)\n" % metrics['runway_months']

        summary += "- Revenue in the most recent complete month: $%s\n" % f"{metrics['mrr']:,.2f}"
        summary += "- Revenue growth month over month: %.1f%%\n" % metrics['revenue_growth']
        if window <= 1:
            summary += ("- NOTE: only %d complete month%s of history exists, so "
                        "these averages are thin. Say so rather than drawing "
                        "strong conclusions from them.\n" % (window, plural))
        
        if monthly_data and len(monthly_data) >= 3:
            recent_months = monthly_data[-3:]
            summary += "\nRECENT MONTHLY TRENDS:\n"
            for month in recent_months:
                summary += f"- {month['month']}: Revenue ${month['revenue']:,.0f}, Expenses ${month['expenses']:,.0f}, Burn ${month['burn_rate']:,.0f}\n"
        
        if transactions:
            categories = {}
            for t in transactions:
                if t['type'] == 'expense':
                    cat = t['category']
                    amount = float(t['amount'])
                    categories[cat] = categories.get(cat, 0) + amount
            
            if categories:
                summary += "\nTOP EXPENSE CATEGORIES:\n"
                sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]
                for cat, amount in sorted_cats:
                    summary += f"- {cat}: ${amount:,.0f}\n"
        
        return summary
    
    @staticmethod
    def _call_groq(financial_summary: str) -> Dict:
        """Call Groq API with updated model"""
        
        try:
            prompt = f"""You are a seasoned startup CFO. Analyze this startup's financial data and provide actionable insights.

{financial_summary}

RULES FOR THESE FIGURES:
- Every number you state must be quoted from the overview above. Do not
  recalculate burn, runway or growth, and never divide cash by burn yourself.
- If runway is listed as not applicable, the company is profitable or at
  breakeven. Its cash is growing. Do not warn that it will run out of cash, do
  not state a number of months, and do not treat this as a risk.
- Only raise a cash warning when a runway figure is actually given and is under
  6 months.
- Negative net burn means the company is MAKING money, not losing it.
- Write plain English. Never print internal field names.

Provide analysis in this EXACT format:

FINANCIAL HEALTH SCORE: [Score 1-10 with brief explanation]

KEY INSIGHTS:
- [3-5 important observations]

URGENT WARNINGS:
- [Critical issues or "None - financials look stable"]

COST OPTIMIZATION OPPORTUNITIES:
- [2-3 ways to reduce costs]

GROWTH RECOMMENDATIONS:
- [2-3 suggestions to improve financial health]

NEXT STEPS:
- [3 concrete action items for this week]

Be concise and use specific numbers from the data."""

            # USE NEW MODEL: llama-3.3-70b-versatile
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are an expert startup CFO providing financial analysis."},
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.3-70b-versatile",  # ✅ UPDATED MODEL
                temperature=0.7,
                max_tokens=1500,
            )
            
            ai_response = chat_completion.choices[0].message.content
            
            return {
                'success': True,
                'analysis': ai_response,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'model': 'Llama 3.3 70B (Groq - FREE)'
            }
            
        except Exception as e:
            # Fallback to simple insights if API fails
            print(f"Groq API Error: {e}")
            return AIFinancialAdvisor._simple_fallback(financial_summary)
    
    @staticmethod
    def _simple_fallback(summary: str) -> Dict:
        """Fallback insights if API fails"""
        
        analysis = f"""FINANCIAL HEALTH SCORE: 6/10
Based on the data provided, moderate financial health with areas for improvement.

KEY INSIGHTS:
- Financial data successfully loaded
- Analysis shows standard startup metrics
- Growth trajectory visible in recent months

URGENT WARNINGS:
- Review cash runway and plan accordingly
- Monitor burn rate trends carefully

COST OPTIMIZATION OPPORTUNITIES:
- Review recurring software subscriptions
- Negotiate annual payment discounts
- Audit marketing spend effectiveness

GROWTH RECOMMENDATIONS:
- Focus on increasing Monthly Recurring Revenue
- Improve customer retention rates
- Optimize customer acquisition costs

NEXT STEPS:
1. Review top expense categories this week
2. Update 6-month financial forecast
3. Schedule monthly financial review meetings

Note: This is a simplified analysis. Connect your Groq API key for detailed AI insights."""
        
        return {
            'success': True,
            'analysis': analysis,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'model': 'Fallback (Rule-based)'
        }


def format_insights_for_display(insights: Dict) -> Dict:
    """Format AI insights for HTML display"""
    
    if not insights.get('success'):
        return {
            'error': True,
            'message': insights.get('error', 'Failed to generate insights')
        }
    
    analysis = insights['analysis']
    sections = {}
    current_section = None
    current_content = []
    
    for line in analysis.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        if line.endswith(':') and line.isupper():
            if current_section:
                sections[current_section] = '\n'.join(current_content)
            current_section = line[:-1]
            current_content = []
        else:
            current_content.append(line)
    
    if current_section:
        sections[current_section] = '\n'.join(current_content)
    
    return {
        'error': False,
        'sections': sections,
        'timestamp': insights['timestamp'],
        'model': insights.get('model', 'AI-powered')
    }