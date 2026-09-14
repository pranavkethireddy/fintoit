"""
Export utilities for Fintoit
Handles CSV and PDF generation
"""

import csv
import io
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch


def generate_csv(transactions, filename="transactions.csv"):
    """Generate CSV file from transactions"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Date', 'Type', 'Category', 'Description', 'Amount', 'Recurring'])
    
    # Write data
    for t in transactions:
        writer.writerow([
            t['date'],
            t['type'],
            t['category'],
            t.get('description', ''),
            float(t['amount']),
            'Yes' if t.get('recurring') else 'No'
        ])
    
    output.seek(0)
    return output.getvalue()


def parse_csv(file_content):
    """Parse CSV file and return list of transactions"""
    transactions = []
    
    # Decode if bytes
    if isinstance(file_content, bytes):
        file_content = file_content.decode('utf-8')
    
    # Parse CSV
    reader = csv.DictReader(io.StringIO(file_content))
    
    for row in reader:
        try:
            transaction = {
                'date': row.get('Date', row.get('date', '')),
                'type': row.get('Type', row.get('type', '')).lower(),
                'category': row.get('Category', row.get('category', '')),
                'description': row.get('Description', row.get('description', '')),
                'amount': float(row.get('Amount', row.get('amount', 0))),
                'recurring': row.get('Recurring', row.get('recurring', 'No')).lower() in ['yes', 'true', '1']
            }
            if transaction['date'] and transaction['type'] and transaction['amount']:
                transactions.append(transaction)
        except (KeyError, ValueError) as e:
            print(f"Skipping invalid row: {e}")
            continue
    
    return transactions


def generate_pdf_report(company_name, metrics, monthly_data, transactions):
    """Generate PDF financial report"""
    buffer = io.BytesIO()
    
    # Create PDF canvas
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Title
    c.setFont("Helvetica-Bold", 24)
    c.drawString(1*inch, height - 1*inch, company_name)
    
    c.setFont("Helvetica", 16)
    c.drawString(1*inch, height - 1.4*inch, "Financial Report")
    
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, height - 1.7*inch, f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}")
    
    # Draw line
    c.line(1*inch, height - 1.9*inch, width - 1*inch, height - 1.9*inch)
    
    # Metrics Summary
    y_position = height - 2.3*inch
    c.setFont("Helvetica-Bold", 14)
    c.drawString(1*inch, y_position, "Executive Summary")
    
    y_position -= 0.4*inch
    c.setFont("Helvetica", 11)
    
    # USE CORRECT KEY NAMES
    current_cash = metrics.get('current_cash', 0)
    monthly_burn = metrics.get('monthly_burn', 0)
    runway = metrics.get('runway_months', 0)
    mrr = metrics.get('mrr', 0)
    growth = metrics.get('revenue_growth', 0)
    
    # runway is None whenever the company is profitable or at breakeven. "N/A"
    # reads like missing data; say what is actually true.
    net_burn = metrics.get('net_burn')
    if runway is None and metrics.get('has_data'):
        runway_line = "Runway: not applicable (profitable)"
    elif runway and runway > 0:
        runway_line = f"Runway: {runway:.1f} months"
    else:
        runway_line = "Runway: N/A"

    summary_items = [
        f"Current Cash Balance: ${current_cash:,.2f}",
        f"Gross Monthly Burn (expenses only): ${monthly_burn:,.2f}",
    ]
    if net_burn is not None:
        summary_items.append(
            f"Net Monthly Burn (after revenue): ${net_burn:,.2f}" if net_burn > 0
            else f"Net Cash Generated: ${abs(net_burn):,.2f}/month")
    summary_items += [
        runway_line,
        f"Monthly Recurring Revenue: ${mrr:,.2f}",
        f"Revenue Growth Rate: {growth:.1f}%"
    ]
    
    for item in summary_items:
        c.drawString(1.2*inch, y_position, f"• {item}")
        y_position -= 0.25*inch
    
    # Monthly Data
    if monthly_data and len(monthly_data) > 0:
        y_position -= 0.4*inch
        
        c.setFont("Helvetica-Bold", 14)
        c.drawString(1*inch, y_position, "Monthly Breakdown")
        
        y_position -= 0.35*inch
        c.setFont("Helvetica-Bold", 9)
        
        # Headers
        c.drawString(1*inch, y_position, "Month")
        c.drawString(2*inch, y_position, "Revenue")
        c.drawString(3*inch, y_position, "Expenses")
        c.drawString(4*inch, y_position, "Burn")
        c.drawString(5*inch, y_position, "Cash")
        
        y_position -= 0.05*inch
        c.line(1*inch, y_position, width - 1*inch, y_position)
        y_position -= 0.2*inch
        
        c.setFont("Helvetica", 9)
        
        # Data rows (show last 10 months)
        for month in monthly_data[-10:]:
            if y_position < 2*inch:
                c.showPage()  # New page
                y_position = height - 1*inch
                c.setFont("Helvetica", 9)
            
            c.drawString(1*inch, y_position, str(month.get('month', '')))
            c.drawString(2*inch, y_position, f"${month.get('revenue', 0):,.0f}")
            c.drawString(3*inch, y_position, f"${month.get('expenses', 0):,.0f}")
            c.drawString(4*inch, y_position, f"${month.get('burn_rate', 0):,.0f}")
            c.drawString(5*inch, y_position, f"${month.get('cash_balance', 0):,.0f}")
            
            y_position -= 0.2*inch
    
    # Recent Transactions Section
    if transactions and len(transactions) > 0:
        if y_position < 3*inch:
            c.showPage()
            y_position = height - 1*inch
        
        y_position -= 0.4*inch
        c.setFont("Helvetica-Bold", 14)
        c.drawString(1*inch, y_position, "Recent Transactions")
        
        y_position -= 0.35*inch
        c.setFont("Helvetica-Bold", 8)
        
        c.drawString(1*inch, y_position, "Date")
        c.drawString(1.8*inch, y_position, "Type")
        c.drawString(2.5*inch, y_position, "Category")
        c.drawString(3.5*inch, y_position, "Description")
        c.drawString(5.5*inch, y_position, "Amount")
        
        y_position -= 0.15*inch
        c.line(1*inch, y_position, width - 1*inch, y_position)
        y_position -= 0.2*inch
        
        c.setFont("Helvetica", 8)
        
        # Show last 15 transactions
        for t in transactions[-15:]:
            if y_position < 1*inch:
                c.showPage()
                y_position = height - 1*inch
                c.setFont("Helvetica", 8)
            
            c.drawString(1*inch, y_position, str(t.get('date', ''))[:10])
            c.drawString(1.8*inch, y_position, str(t.get('type', '')).capitalize())
            c.drawString(2.5*inch, y_position, str(t.get('category', ''))[:15])
            c.drawString(3.5*inch, y_position, str(t.get('description', ''))[:25])
            c.drawString(5.5*inch, y_position, f"${float(t.get('amount', 0)):,.2f}")
            
            y_position -= 0.18*inch
    
    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(1*inch, 0.5*inch, "Generated by Fintoit - The AI CFO Platform for Startups")
    
    # Save PDF
    c.save()
    
    buffer.seek(0)
    return buffer.getvalue()

def generate_investor_report(company_name, metrics, monthly_data, transactions, ai_narrative, company_info):
    """Generate a polished investor-ready PDF report"""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            leftMargin=0.8*inch, rightMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)

    styles = getSampleStyleSheet()
    story = []

    # ── Custom styles ──────────────────────────────────────────
    title_style = ParagraphStyle('Title', fontSize=28, fontName='Helvetica-Bold',
                                  textColor=colors.HexColor('#1e3a5f'), spaceAfter=6, alignment=TA_LEFT)
    subtitle_style = ParagraphStyle('Subtitle', fontSize=13, fontName='Helvetica',
                                     textColor=colors.HexColor('#64748b'), spaceAfter=4)
    section_style = ParagraphStyle('Section', fontSize=14, fontName='Helvetica-Bold',
                                    textColor=colors.HexColor('#1e3a5f'), spaceBefore=16, spaceAfter=8)
    body_style = ParagraphStyle('Body', fontSize=10, fontName='Helvetica',
                                 textColor=colors.HexColor('#374151'), leading=16, spaceAfter=6)
    small_style = ParagraphStyle('Small', fontSize=8, fontName='Helvetica',
                                  textColor=colors.HexColor('#9ca3af'))

    # ── Cover ──────────────────────────────────────────────────
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(company_name, title_style))
    story.append(Paragraph("Investor Financial Report", subtitle_style))
    story.append(Paragraph(f"Prepared {datetime.now().strftime('%B %d, %Y')} · Confidential", small_style))
    story.append(HRFlowable(width='100%', thickness=2, color=colors.HexColor('#1e3a5f'), spaceAfter=16))

    # ── Company info ───────────────────────────────────────────
    if company_info.get('tagline'):
        story.append(Paragraph(company_info['tagline'], body_style))
        story.append(Spacer(1, 0.1*inch))

    # ── Key Metrics Table ──────────────────────────────────────
    story.append(Paragraph("Key Metrics", section_style))

    current_cash = metrics.get('current_cash', 0)
    monthly_burn = metrics.get('monthly_burn', 0)
    runway = metrics.get('runway_months', 0)
    mrr = metrics.get('mrr', 0)
    growth = metrics.get('revenue_growth', 0)

    metric_data = [
        ['Metric', 'Value', 'Status'],
        ['Current Cash', f"${current_cash:,.0f}", '✓ Healthy' if current_cash > 50000 else '⚠ Low'],
        ['Monthly Burn Rate', f"${monthly_burn:,.0f}/mo", '✓ OK' if monthly_burn < 50000 else '⚠ High'],
        # A profitable company has no finite runway. Reporting that as
        # "N/A · ⚠ Watch" to an investor understates the company.
        ['Runway',
         'Profitable' if runway is None else f"{runway:.1f} months",
         '✓ Healthy' if (runway is None or runway > 12) else '⚠ Watch'],
        ['MRR', f"${mrr:,.0f}/mo", '✓ Growing' if growth > 0 else '— Flat'],
        ['Revenue Growth', f"{growth:.1f}%", '✓ Positive' if growth > 0 else '⚠ Declining'],
    ]

    metric_table = Table(metric_data, colWidths=[2.5*inch, 2*inch, 2*inch])
    metric_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8fafc'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(metric_table)

    # ── AI Narrative ───────────────────────────────────────────
    story.append(Paragraph("Financial Analysis", section_style))
    for para in ai_narrative.split('\n\n'):
        if para.strip():
            story.append(Paragraph(para.strip(), body_style))

    # ── Monthly Breakdown Table ────────────────────────────────
    if monthly_data:
        story.append(Paragraph("Monthly Financial Breakdown", section_style))
        table_data = [['Month', 'Revenue', 'Expenses', 'Net Burn', 'Cash Balance']]
        for m in monthly_data[-6:]:
            table_data.append([
                m.get('month', ''),
                f"${m.get('revenue', 0):,.0f}",
                f"${m.get('expenses', 0):,.0f}",
                f"${m.get('burn_rate', 0):,.0f}",
                f"${m.get('cash_balance', 0):,.0f}",
            ])
        monthly_table = Table(table_data, colWidths=[1.3*inch, 1.3*inch, 1.3*inch, 1.3*inch, 1.3*inch])
        monthly_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8fafc'), colors.white]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ]))
        story.append(monthly_table)

    # ── Top Expenses ───────────────────────────────────────────
    if transactions:
        categories = {}
        for t in transactions:
            if t['type'] == 'expense':
                cat = t['category']
                categories[cat] = categories.get(cat, 0) + float(t['amount'])
        top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_cats:
            story.append(Paragraph("Top Expense Categories", section_style))
            exp_data = [['Category', 'Total Amount', '% of Expenses']]
            total_exp = sum(v for _, v in top_cats)
            for cat, amt in top_cats:
                pct = (amt / total_exp * 100) if total_exp > 0 else 0
                exp_data.append([cat, f"${amt:,.0f}", f"{pct:.1f}%"])
            exp_table = Table(exp_data, colWidths=[2.5*inch, 2*inch, 2*inch])
            exp_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8fafc'), colors.white]),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ]))
            story.append(exp_table)

    # ── Footer ─────────────────────────────────────────────────
    story.append(Spacer(1, 0.3*inch))
    story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph(
        f"Confidential · Generated by Fintoit · {datetime.now().strftime('%B %Y')}",
        ParagraphStyle('Footer', fontSize=8, textColor=colors.HexColor('#9ca3af'), alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def generate_board_report(company_name, metrics, kpis, monthly_data, narrative, info):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            rightMargin=0.75*inch, leftMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)

    styles = getSampleStyleSheet()
    story = []

    # Colors
    dark = colors.HexColor('#1e293b')
    blue = colors.HexColor('#3b82f6')
    green = colors.HexColor('#22c55e')
    red = colors.HexColor('#ef4444')
    light = colors.HexColor('#f8fafc')

    title_style = ParagraphStyle('title', fontSize=24, fontName='Helvetica-Bold', textColor=dark, spaceAfter=4)
    sub_style = ParagraphStyle('sub', fontSize=11, fontName='Helvetica', textColor=colors.HexColor('#64748b'), spaceAfter=16)
    h2_style = ParagraphStyle('h2', fontSize=14, fontName='Helvetica-Bold', textColor=dark, spaceBefore=16, spaceAfter=8)
    body_style = ParagraphStyle('body', fontSize=10, fontName='Helvetica', textColor=dark, leading=16, spaceAfter=12)
    label_style = ParagraphStyle('label', fontSize=8, fontName='Helvetica', textColor=colors.HexColor('#64748b'), spaceAfter=2)
    value_style = ParagraphStyle('value', fontSize=18, fontName='Helvetica-Bold', textColor=blue, spaceAfter=4)

    # Header
    story.append(Paragraph(f"{company_name}", title_style))
    story.append(Paragraph(f"Board Report: {info.get('period', '')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=blue, spaceAfter=16))

    # KPI Grid
    story.append(Paragraph("Financial Highlights", h2_style))
    # Runway is None for a profitable company, and burn multiple is None when
    # undefined (profitable, or not growing). Formatting either one blindly put
    # a literal "Nonex" and "None mo" into a board pack.
    _runway = metrics.get('runway_months')
    _runway_cell = 'Profitable' if _runway is None else f"{_runway:.1f} mo"
    _bm = kpis.get('burn_multiple')
    _bm_cell = 'N/A' if _bm is None else f"{_bm}x"

    kpi_data = [
        ['Cash Balance', 'Gross Burn', 'Runway', 'MRR'],
        [f"${metrics['current_cash']:,.0f}", f"${metrics['monthly_burn']:,.0f}",
         _runway_cell, f"${metrics['mrr']:,.0f}"],
        ['ARR', 'MoM Growth', 'Gross Margin', 'Burn Multiple'],
        [f"${kpis['arr']:,.0f}", f"{kpis['mom_growth']}%",
         f"{kpis['gross_margin']}%", _bm_cell],
    ]
    kpi_table = Table(kpi_data, colWidths=[1.7*inch]*4)
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), blue),
        ('BACKGROUND', (0,2), (-1,2), colors.HexColor('#6366f1')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('TEXTCOLOR', (0,2), (-1,2), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,2), (-1,2), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('FONTNAME', (0,1), (-1,1), 'Helvetica-Bold'),
        ('FONTNAME', (0,3), (-1,3), 'Helvetica-Bold'),
        ('FONTSIZE', (0,1), (-1,1), 14),
        ('FONTSIZE', (0,3), (-1,3), 14),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,1), [light]),
        ('ROWBACKGROUNDS', (0,3), (-1,3), [light]),
        ('ROWHEIGHT', (0,0), (-1,0), 24),
        ('ROWHEIGHT', (0,1), (-1,1), 32),
        ('ROWHEIGHT', (0,2), (-1,2), 24),
        ('ROWHEIGHT', (0,3), (-1,3), 32),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('ROUNDEDCORNERS', [4]),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 16))

    # Narrative
    story.append(Paragraph("Executive Summary", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceAfter=8))
    story.append(Paragraph(narrative, body_style))

    # Highlights / Challenges
    for title, content, color in [
        ("✓ Highlights", info.get('highlights', ''), green),
        ("⚠ Challenges", info.get('challenges', ''), red),
        ("→ Next Quarter", info.get('next_quarter', ''), blue),
        ("$ The Ask", info.get('ask', ''), colors.HexColor('#7c3aed')),
    ]:
        if content:
            story.append(Paragraph(title, ParagraphStyle('sec', fontSize=12, fontName='Helvetica-Bold',
                                                          textColor=color, spaceBefore=12, spaceAfter=6)))
            story.append(Paragraph(content, body_style))

    # Monthly table
    story.append(Paragraph("Monthly Performance", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceAfter=8))
    if monthly_data:
        table_data = [['Month', 'Revenue', 'Expenses', 'Net', 'Margin']]
        for m in monthly_data[-6:]:
            rev = m['revenue']
            exp = m['expenses']
            net = rev - exp
            margin = round((net / rev * 100) if rev > 0 else 0, 1)
            table_data.append([
                m['month'],
                f"${rev:,.0f}",
                f"${exp:,.0f}",
                f"${net:,.0f}",
                f"{margin}%"
            ])
        t = Table(table_data, colWidths=[1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch, 1.2*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), dark),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, light]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWHEIGHT', (0,0), (-1,-1), 20),
        ]))
        story.append(t)

    # Footer
    story.append(Spacer(1, 24))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceAfter=6))
    story.append(Paragraph(f"Generated by Fintoit · {company_name} · Confidential",
                            ParagraphStyle('footer', fontSize=8, textColor=colors.HexColor('#94a3b8'), alignment=1)))

    doc.build(story)
    return buffer.getvalue()