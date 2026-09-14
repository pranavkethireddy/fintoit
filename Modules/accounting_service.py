"""
Accounting Service for Fintoit
Handles all financial statements and tax documentation
"""

from datetime import datetime
from typing import Dict, List
from Modules.database import Database, TransactionDB
from Modules import tax_estimator
import uuid


class ChartOfAccountsDB:
    """Chart of Accounts database operations"""
    
    @staticmethod
    def get_all(company_id: str) -> List[Dict]:
        query = """
            SELECT * FROM chart_of_accounts 
            WHERE company_id = %s AND is_active = TRUE
            ORDER BY account_code
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []
    
    @staticmethod
    def create_standard_accounts(company_id: str):
        """Create standard chart of accounts for new company"""
        standard_accounts = [
            # Assets
            ('1000', 'Cash', 'Asset', 'Current Asset'),
            ('1010', 'Bank Account', 'Asset', 'Current Asset'),
            ('1100', 'Accounts Receivable', 'Asset', 'Current Asset'),
            ('1200', 'Inventory', 'Asset', 'Current Asset'),
            ('1500', 'Equipment', 'Asset', 'Fixed Asset'),
            # Liabilities
            ('2000', 'Accounts Payable', 'Liability', 'Current Liability'),
            ('2100', 'Credit Card Payable', 'Liability', 'Current Liability'),
            ('2200', 'Loans Payable', 'Liability', 'Long-term Liability'),
            # Equity
            ('3000', 'Owner Equity', 'Equity', 'Owner Equity'),
            ('3100', 'Retained Earnings', 'Equity', 'Retained Earnings'),
            # Revenue
            ('4000', 'Sales Revenue', 'Revenue', 'Operating Revenue'),
            ('4100', 'Service Revenue', 'Revenue', 'Operating Revenue'),
            ('4200', 'Other Income', 'Revenue', 'Non-operating Revenue'),
            # Expenses
            ('5000', 'Cost of Goods Sold', 'Expense', 'Cost of Sales'),
            ('6000', 'Salaries & Wages', 'Expense', 'Operating Expense'),
            ('6100', 'Rent', 'Expense', 'Operating Expense'),
            ('6200', 'Software & Technology', 'Expense', 'Operating Expense'),
            ('6300', 'Marketing & Advertising', 'Expense', 'Operating Expense'),
            ('6400', 'Office Supplies', 'Expense', 'Operating Expense'),
            ('6500', 'Professional Fees', 'Expense', 'Operating Expense'),
            ('6600', 'Taxes & Licenses', 'Expense', 'Operating Expense'),
            ('6700', 'Insurance', 'Expense', 'Operating Expense'),
            ('6800', 'Utilities', 'Expense', 'Operating Expense'),
            ('6900', 'Travel & Entertainment', 'Expense', 'Operating Expense'),
        ]
        
        with Database.get_connection() as conn:
            with conn.cursor() as cursor:
                for code, name, acc_type, subtype in standard_accounts:
                    cursor.execute("""
                        INSERT INTO chart_of_accounts 
                        (company_id, account_code, account_name, account_type, account_subtype)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (company_id, account_code) DO NOTHING
                    """, (company_id, code, name, acc_type, subtype))
    
    @staticmethod
    def get_by_type(company_id: str, account_type: str) -> List[Dict]:
        query = """
            SELECT * FROM chart_of_accounts 
            WHERE company_id = %s 
            AND account_type = %s 
            AND is_active = TRUE
            ORDER BY account_code
        """
        results = Database.execute(query, (company_id, account_type))
        return [dict(r) for r in results] if results else []


class FinancialStatements:
    """Generate financial statements from transactions"""
    
    @staticmethod
    def get_income_statement(company_id: str, start_date: str, end_date: str) -> Dict:
        """
        Generate Income Statement (P&L) from transactions table
        """
        
        # Get revenue transactions
        revenue_query = """
            SELECT 
                category,
                SUM(amount) as total_amount,
                COUNT(*) as transaction_count
            FROM transactions
            WHERE company_id = %s
            AND type = 'income'
            AND date BETWEEN %s AND %s
            GROUP BY category
            ORDER BY total_amount DESC
        """
        revenue_rows = Database.execute(revenue_query, (company_id, start_date, end_date))
        
        # Get expense transactions
        expense_query = """
            SELECT 
                category,
                SUM(amount) as total_amount,
                COUNT(*) as transaction_count
            FROM transactions
            WHERE company_id = %s
            AND type = 'expense'
            AND date BETWEEN %s AND %s
            GROUP BY category
            ORDER BY total_amount DESC
        """
        expense_rows = Database.execute(expense_query, (company_id, start_date, end_date))
        
        revenue_items = [dict(r) for r in revenue_rows] if revenue_rows else []
        expense_items = [dict(r) for r in expense_rows] if expense_rows else []
        
        total_revenue = sum(float(r['total_amount']) for r in revenue_items)
        total_expenses = sum(float(e['total_amount']) for e in expense_items)
        gross_profit = total_revenue - total_expenses
        net_income = gross_profit
        profit_margin = (net_income / total_revenue * 100) if total_revenue > 0 else 0
        
        return {
            'period_start': start_date,
            'period_end': end_date,
            'revenue_items': revenue_items,
            'total_revenue': round(total_revenue, 2),
            'expense_items': expense_items,
            'total_expenses': round(total_expenses, 2),
            'gross_profit': round(gross_profit, 2),
            'net_income': round(net_income, 2),
            'profit_margin': round(profit_margin, 2)
        }
    
    @staticmethod
    def get_balance_sheet(company_id: str, starting_cash: float, as_of_date: str = None) -> Dict:
        """
        Generate Balance Sheet from transactions
        """
        if not as_of_date:
            as_of_date = datetime.now().strftime('%Y-%m-%d')
        
        # Calculate current cash from transactions
        cash_query = """
            SELECT 
                SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END) as net_cash
            FROM transactions
            WHERE company_id = %s
            AND date <= %s
        """
        result = Database.execute_one(cash_query, (company_id, as_of_date))
        net_cash = float(result['net_cash']) if result and result['net_cash'] else 0
        current_cash = starting_cash + net_cash
        
        # Get accounts receivable (unpaid invoices)
        ar_query = """
            SELECT COALESCE(SUM(total_amount - paid_amount), 0) as total_ar
            FROM invoices
            WHERE company_id = %s 
            AND status IN ('unpaid', 'partial', 'overdue')
            AND invoice_date <= %s
        """
        ar_result = Database.execute_one(ar_query, (company_id, as_of_date))
        accounts_receivable = float(ar_result['total_ar']) if ar_result else 0
        
        # Get accounts payable (unpaid bills)
        ap_query = """
            SELECT COALESCE(SUM(total_amount - paid_amount), 0) as total_ap
            FROM bills
            WHERE company_id = %s 
            AND status IN ('unpaid', 'partial', 'overdue')
            AND bill_date <= %s
        """
        ap_result = Database.execute_one(ap_query, (company_id, as_of_date))
        accounts_payable = float(ap_result['total_ap']) if ap_result else 0
        
        # Calculate retained earnings from income/expense transactions
        income_query = """
            SELECT 
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END), 0) as net_income
            FROM transactions
            WHERE company_id = %s
            AND date <= %s
        """
        income_result = Database.execute_one(income_query, (company_id, as_of_date))
        transaction_net_income = float(income_result['net_income']) if income_result else 0
        
        # FIX: Unpaid bills represent incurred expenses, even though cash hasn't moved yet.
        # They must reduce retained earnings to keep the accounting equation balanced,
        # since they already increase Accounts Payable on the liabilities side.
        retained_earnings = transaction_net_income - accounts_payable
        
        # Build balance sheet
        total_current_assets = current_cash + accounts_receivable
        total_assets = total_current_assets
        total_current_liabilities = accounts_payable
        total_liabilities = total_current_liabilities
        owner_equity = starting_cash
        total_equity = owner_equity + retained_earnings
        
        return {
            'as_of_date': as_of_date,
            'assets': {
                'current': {
                    'Cash & Bank': round(current_cash, 2),
                    'Accounts Receivable': round(accounts_receivable, 2),
                },
                'total_current': round(total_current_assets, 2),
                'total': round(total_assets, 2)
            },
            'liabilities': {
                'current': {
                    'Accounts Payable': round(accounts_payable, 2),
                },
                'total_current': round(total_current_liabilities, 2),
                'total': round(total_liabilities, 2)
            },
            'equity': {
                'items': {
                    'Owner Equity': round(owner_equity, 2),
                    'Retained Earnings': round(retained_earnings, 2),
                },
                'total': round(total_equity, 2)
            },
            'total_liabilities_equity': round(total_liabilities + total_equity, 2),
            'balanced': abs(total_assets - (total_liabilities + total_equity)) < 1.0
        }
    
    @staticmethod
    def get_cash_flow(company_id: str, start_date: str, end_date: str, starting_cash: float) -> Dict:
        """
        Generate Cash Flow Statement
        """
        
        # Operating Activities
        operating_query = """
            SELECT 
                type,
                category,
                SUM(amount) as total
            FROM transactions
            WHERE company_id = %s
            AND date BETWEEN %s AND %s
            GROUP BY type, category
            ORDER BY type, total DESC
        """
        
        operating_rows = Database.execute(operating_query, (company_id, start_date, end_date))
        
        cash_inflows = []
        cash_outflows = []
        total_inflows = 0
        total_outflows = 0
        
        for row in (operating_rows or []):
            amount = float(row['total'])
            item = {
                'category': row['category'],
                'amount': round(amount, 2)
            }
            
            if row['type'] == 'income':
                cash_inflows.append(item)
                total_inflows += amount
            else:
                cash_outflows.append(item)
                total_outflows += amount
        
        net_cash = total_inflows - total_outflows
        
        # Beginning cash
        begin_query = """
            SELECT COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END), 0) as net
            FROM transactions
            WHERE company_id = %s AND date < %s
        """
        begin_result = Database.execute_one(begin_query, (company_id, start_date))
        beginning_cash = starting_cash + float(begin_result['net'] if begin_result and begin_result['net'] else 0)
        ending_cash = beginning_cash + net_cash
        
        return {
            'period_start': start_date,
            'period_end': end_date,
            'operating': {
                'inflows': cash_inflows,
                'outflows': cash_outflows,
                'total_inflows': round(total_inflows, 2),
                'total_outflows': round(total_outflows, 2),
                'net_operating': round(net_cash, 2)
            },
            'beginning_cash': round(beginning_cash, 2),
            'net_change': round(net_cash, 2),
            'ending_cash': round(ending_cash, 2)
        }
    
    @staticmethod
    def get_tax_summary(company_id: str, tax_year: int, starting_cash: float,
                        entity_type: str = None, state: str = None) -> Dict:
        """
        Generate Tax Summary for filing
        """
        start_date = f"{tax_year}-01-01"
        end_date = f"{tax_year}-12-31"
        
        # Get income statement for the year
        income_stmt = FinancialStatements.get_income_statement(company_id, start_date, end_date)
        
        # Get deductible expenses
        deductions_query = """
            SELECT 
                category,
                SUM(amount) as total,
                COUNT(*) as count
            FROM transactions
            WHERE company_id = %s
            AND type = 'expense'
            AND EXTRACT(YEAR FROM date) = %s
            GROUP BY category
            ORDER BY total DESC
        """
        deductions = Database.execute(deductions_query, (company_id, tax_year))
        deduction_items = [dict(d) for d in deductions] if deductions else []
        
        # Get quarterly breakdown
        quarterly = []
        for quarter in range(1, 5):
            if quarter == 1:
                q_start, q_end = f"{tax_year}-01-01", f"{tax_year}-03-31"
            elif quarter == 2:
                q_start, q_end = f"{tax_year}-04-01", f"{tax_year}-06-30"
            elif quarter == 3:
                q_start, q_end = f"{tax_year}-07-01", f"{tax_year}-09-30"
            else:
                q_start, q_end = f"{tax_year}-10-01", f"{tax_year}-12-31"
            
            q_query = """
                SELECT 
                    COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) as revenue,
                    COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) as expenses
                FROM transactions
                WHERE company_id = %s AND date BETWEEN %s AND %s
            """
            q_result = Database.execute_one(q_query, (company_id, q_start, q_end))
            
            if q_result:
                q_revenue = float(q_result['revenue'])
                q_expenses = float(q_result['expenses'])
                quarterly.append({
                    'quarter': f"Q{quarter} {tax_year}",
                    'revenue': round(q_revenue, 2),
                    'expenses': round(q_expenses, 2),
                    'net': round(q_revenue - q_expenses, 2)
                })
        
        # Estimated taxes. The previous flat 15.3% + 22% model was wrong for most
        # users (see Modules/tax_estimator). We now produce an explicit range
        # driven by entity type and state, with its assumptions surfaced.
        net_income = income_stmt['net_income']
        estimate = tax_estimator.estimate(net_income, entity_type, state)
        
        # Tax documents
        docs_query = """
            SELECT * FROM tax_documents
            WHERE company_id = %s AND tax_year = %s
            ORDER BY document_type, created_at DESC
        """
        tax_docs = Database.execute(docs_query, (company_id, tax_year))
        
        return {
            'tax_year': tax_year,
            'gross_revenue': income_stmt['total_revenue'],
            'total_deductions': income_stmt['total_expenses'],
            'net_income': net_income,
            'deduction_items': deduction_items,
            'quarterly_breakdown': quarterly,
            'estimate': estimate,
            'tax_documents': [dict(d) for d in tax_docs] if tax_docs else []
        }
    
    @staticmethod
    def get_pl_comparison(company_id: str, year: int) -> Dict:
        """P&L comparison — current year vs last year, plus MoM breakdown"""

        all_txs = TransactionDB.get_all(company_id)

        def get_monthly_data(txs, target_year):
            months = {}
            for i in range(1, 13):
                months[i] = {'revenue': 0.0, 'expenses': 0.0}
            for t in txs:
                date_raw = t['date']
                if isinstance(date_raw, str):
                    from datetime import datetime as dt
                    date_obj = dt.strptime(date_raw, '%Y-%m-%d')
                else:
                    from datetime import datetime as dt
                    date_obj = dt.combine(date_raw, dt.min.time())
                if date_obj.year == target_year:
                    m = date_obj.month
                    amt = float(t['amount'])
                    if t['type'] == 'income':
                        months[m]['revenue'] += amt
                    else:
                        months[m]['expenses'] += amt
            result = []
            for i in range(1, 13):
                rev = months[i]['revenue']
                exp = months[i]['expenses']
                result.append({
                    'month': i,
                    'month_name': ['Jan','Feb','Mar','Apr','May','Jun',
                                'Jul','Aug','Sep','Oct','Nov','Dec'][i-1],
                    'revenue': round(rev, 2),
                    'expenses': round(exp, 2),
                    'net': round(rev - exp, 2),
                    'gross_margin': round(((rev - exp) / rev * 100) if rev > 0 else 0, 1)
                })
            return result

        current = get_monthly_data(all_txs, year)
        previous = get_monthly_data(all_txs, year - 1)

        def totals(data):
            return {
                'revenue': sum(m['revenue'] for m in data),
                'expenses': sum(m['expenses'] for m in data),
                'net': sum(m['net'] for m in data)
            }

        curr_totals = totals(current)
        prev_totals = totals(previous)

        def pct_change(curr, prev):
            if prev == 0:
                return 100.0 if curr > 0 else 0.0
            return round(((curr - prev) / prev) * 100, 1)

        return {
            'year': year,
            'prev_year': year - 1,
            'current': current,
            'previous': previous,
            'curr_totals': curr_totals,
            'prev_totals': prev_totals,
            'revenue_change': pct_change(curr_totals['revenue'], prev_totals['revenue']),
            'expense_change': pct_change(curr_totals['expenses'], prev_totals['expenses']),
            'net_change': pct_change(curr_totals['net'], prev_totals['net']),
        }


class InvoiceDB:
    """Invoice database operations"""
    
    @staticmethod
    def get_all(company_id: str) -> List[Dict]:
        query = """
            SELECT * FROM invoices 
            WHERE company_id = %s 
            ORDER BY invoice_date DESC
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []
    
    @staticmethod
    def mark_paid(invoice_id: str, company_id: str):
        query = """
            UPDATE invoices 
            SET status = 'paid', paid_amount = total_amount
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (invoice_id, company_id), fetch=False)
    
    @staticmethod
    def get_summary(company_id: str) -> Dict:
        query = """
            SELECT 
                COUNT(*) as total_invoices,
                SUM(total_amount) as total_billed,
                SUM(paid_amount) as total_paid,
                SUM(CASE WHEN status = 'unpaid' OR status = 'overdue' THEN total_amount - paid_amount ELSE 0 END) as outstanding
            FROM invoices
            WHERE company_id = %s
        """
        result = Database.execute_one(query, (company_id,))
        if result:
            return {
                'total_invoices': int(result['total_invoices'] or 0),
                'total_billed': float(result['total_billed'] or 0),
                'total_paid': float(result['total_paid'] or 0),
                'outstanding': float(result['outstanding'] or 0)
            }
        return {'total_invoices': 0, 'total_billed': 0, 'total_paid': 0, 'outstanding': 0}
    @staticmethod
    def create(invoice: Dict, company_id: str) -> Dict:
        query = """
            INSERT INTO invoices 
            (company_id, invoice_number, customer_name, customer_email, invoice_date, due_date, total_amount, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            invoice['invoice_number'],
            invoice['customer_name'],
            invoice.get('customer_email', ''),
            invoice['invoice_date'],
            invoice['due_date'],
            invoice['total_amount'],
            invoice.get('notes', '')
        ))
        return dict(result) if result else {}
    @staticmethod
    def delete(invoice_id: str, company_id: str):
        query = "DELETE FROM invoices WHERE id = %s AND company_id = %s"
        Database.execute(query, (invoice_id, company_id), fetch=False)
    @staticmethod
    def get_aging(company_id: str) -> Dict:
        """Get accounts receivable aging buckets"""
        from datetime import date
        today = date.today()

        query = """
            SELECT *, 
                (CURRENT_DATE - due_date::date) as days_overdue
            FROM invoices
            WHERE company_id = %s 
            AND status != 'paid'
            ORDER BY due_date ASC
        """
        results = Database.execute(query, (company_id,))
        invoices = [dict(r) for r in results] if results else []

        buckets = {
            'current':    {'label': 'Current (not due)', 'days': '0',      'invoices': [], 'total': 0},
            'days_1_30':  {'label': '1-30 days overdue', 'days': '1-30',   'invoices': [], 'total': 0},
            'days_31_60': {'label': '31-60 days overdue','days': '31-60',  'invoices': [], 'total': 0},
            'days_61_90': {'label': '61-90 days overdue','days': '61-90',  'invoices': [], 'total': 0},
            'days_90':    {'label': '90+ days overdue',  'days': '90+',    'invoices': [], 'total': 0},
        }

        for inv in invoices:
            days = int(inv.get('days_overdue') or 0)
            amt = float(inv.get('total_amount', 0)) - float(inv.get('paid_amount', 0))
            inv['days_overdue'] = days
            inv['balance'] = round(amt, 2)

            if days <= 0:
                buckets['current']['invoices'].append(inv)
                buckets['current']['total'] += amt
            elif days <= 30:
                buckets['days_1_30']['invoices'].append(inv)
                buckets['days_1_30']['total'] += amt
            elif days <= 60:
                buckets['days_31_60']['invoices'].append(inv)
                buckets['days_31_60']['total'] += amt
            elif days <= 90:
                buckets['days_61_90']['invoices'].append(inv)
                buckets['days_61_90']['total'] += amt
            else:
                buckets['days_90']['invoices'].append(inv)
                buckets['days_90']['total'] += amt

        total_outstanding = sum(b['total'] for b in buckets.values())

        return {
            'buckets': buckets,
            'total_outstanding': round(total_outstanding, 2),
            'total_invoices': len(invoices)
        }

class BillDB:
    """Bills database operations"""
    
    @staticmethod
    def get_all(company_id: str) -> List[Dict]:
        query = """
            SELECT * FROM bills 
            WHERE company_id = %s 
            ORDER BY bill_date DESC
        """
        results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []
    
    @staticmethod
    def create(bill: Dict, company_id: str) -> Dict:
        query = """
            INSERT INTO bills 
            (company_id, bill_number, vendor_name, bill_date, due_date, total_amount, category, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            bill.get('bill_number', ''),
            bill['vendor_name'],
            bill['bill_date'],
            bill['due_date'],
            bill['total_amount'],
            bill.get('category', ''),
            bill.get('notes', '')
        ))
        return dict(result) if result else {}
    
    @staticmethod
    def get_summary(company_id: str) -> Dict:
        query = """
            SELECT 
                COUNT(*) as total_bills,
                SUM(total_amount) as total_amount,
                SUM(CASE WHEN status = 'unpaid' OR status = 'overdue' THEN total_amount - paid_amount ELSE 0 END) as outstanding
            FROM bills
            WHERE company_id = %s
        """
        result = Database.execute_one(query, (company_id,))
        if result:
            return {
                'total_bills': int(result['total_bills'] or 0),
                'total_amount': float(result['total_amount'] or 0),
                'outstanding': float(result['outstanding'] or 0)
            }
        return {'total_bills': 0, 'total_amount': 0, 'outstanding': 0}
    @staticmethod
    def delete(bill_id: str, company_id: str):
        query = "DELETE FROM bills WHERE id = %s AND company_id = %s"
        Database.execute(query, (bill_id, company_id), fetch=False)
    
    @staticmethod
    def mark_paid(bill_id: str, company_id: str):
        query = """
            UPDATE bills 
            SET status = 'paid', paid_amount = total_amount
            WHERE id = %s AND company_id = %s
        """
        Database.execute(query, (bill_id, company_id), fetch=False)


class TaxDocumentDB:
    """Tax document database operations"""

    @staticmethod
    def create(doc: Dict, company_id: str, file_data=None, file_name=None, file_type=None) -> Dict:
        query = """
            INSERT INTO tax_documents
            (company_id, document_type, tax_year, vendor_name, amount, category, notes, file_data, file_name, file_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """
        result = Database.execute_one(query, (
            company_id,
            doc['document_type'],
            doc['tax_year'],
            doc.get('vendor_name', ''),
            doc.get('amount', 0),
            doc.get('category', ''),
            doc.get('notes', ''),
            file_data,
            file_name,
            file_type
        ))
        return dict(result) if result else {}

    @staticmethod
    def get_all(company_id: str, tax_year: int = None) -> List[Dict]:
        if tax_year:
            query = "SELECT * FROM tax_documents WHERE company_id = %s AND tax_year = %s ORDER BY created_at DESC"
            results = Database.execute(query, (company_id, tax_year))
        else:
            query = "SELECT * FROM tax_documents WHERE company_id = %s ORDER BY tax_year DESC, created_at DESC"
            results = Database.execute(query, (company_id,))
        return [dict(r) for r in results] if results else []

    @staticmethod
    def get_by_id(doc_id: str, company_id: str) -> Dict:
        query = "SELECT * FROM tax_documents WHERE id = %s AND company_id = %s"
        result = Database.execute_one(query, (doc_id, company_id))
        return dict(result) if result else {}

    @staticmethod
    def delete(doc_id: str, company_id: str):
        query = "DELETE FROM tax_documents WHERE id = %s AND company_id = %s"
        Database.execute(query, (doc_id, company_id), fetch=False)