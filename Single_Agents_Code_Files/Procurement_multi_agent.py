import os
import json
import base64
import gspread
import time
import matplotlib.pyplot as plt
import pandas as pd
from fpdf import FPDF
from datetime import datetime
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials
from langchain.agents import Tool, initialize_agent, AgentType
from langchain_groq import ChatGroq
from langchain.prompts import ChatPromptTemplate
from tenacity import retry, stop_after_attempt, wait_exponential
import traceback
load_dotenv()
CLIENT_SECRETS_FILE = 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.send']
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SPREADSHEET_NAME = "Invoices"
SENDER_EMAIL = "syedaliahmed171@gmail.com"

BATCH_SIZE = 5  # Used in all batch operations

# ---------- Global State ----------
GLOBAL_BUDGET_RESULTS = None
GLOBAL_INVENTORY_RESULTS = None
GLOBAL_PROCUREMENT_DATA = None

# ---------- Google Sheets Client ----------
def get_gsheet_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    return gspread.authorize(creds)

# ---------- Gmail Auth ----------
def get_gmail_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

# ---------- Utility ----------
def batch_items(items, batch_size=BATCH_SIZE):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]

# ---------- Email Function ----------
def send_email(subject, body, recipient):
    try:
        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw_msg}
        ).execute()
        return True
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

# ---------- Safe LLM Invocation ----------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def safe_chat_invoke(chain, prompt: str):
    try:
        return chain.invoke(prompt)
    except Exception as e:
        print(f"[safe_chat_invoke] Error: {str(e)}")
        raise e

# ---------- Core Functions ----------
def fetch_procurement_data(_=None):
    global GLOBAL_PROCUREMENT_DATA
    try:
        client = get_gsheet_client()
        sheet = client.open(SPREADSHEET_NAME)

        data = {
            "POs": sheet.worksheet("PO's").get_all_records(),
            "Budgets": sheet.worksheet("Budgets").get_all_records(),
            "Spend": sheet.worksheet("Spend").get_all_records(),
            "Inventory": sheet.worksheet("Inventory").get_all_records(),
        }
        GLOBAL_PROCUREMENT_DATA = data
        return json.dumps({"status": "success", "data": data})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def budget_processor(_=None) -> str:
    global GLOBAL_BUDGET_RESULTS, GLOBAL_PROCUREMENT_DATA
    
    GLOBAL_BUDGET_RESULTS = None  # Reset at start
    # Skip if already processed
    if GLOBAL_BUDGET_RESULTS is not None:
        return json.dumps({"status": "success", "message": "Budget already processed"})
    try:
        if not GLOBAL_PROCUREMENT_DATA:
            response = fetch_procurement_data()
            parsed = json.loads(response)
            if parsed["status"] != "success":
                return json.dumps({"status": "error", "message": parsed["message"]})
        
        data = GLOBAL_PROCUREMENT_DATA
        pos_list = data["POs"]
        budgets = data["Budgets"]
        spend = data["Spend"]

        budget_map = {b["Category"]: float(b["Budget Amount"]) for b in budgets}
        spend_map = {}
        for s in spend:
            cat = s["Category"]
            amt = float(s.get("Amount Spent", 0))
            spend_map[cat] = spend_map.get(cat, 0) + amt

        results = []
        for batch in batch_items(pos_list):
            for po in batch:
                category = po["Category"]
                total_cost = float(po["Qty"]) * float(po["Price"])
                remaining_budget = budget_map.get(category, 0) - spend_map.get(category, 0)
                status = "within" if total_cost <= remaining_budget else "exceeded"
                results.append({
                    "Item": po.get("Item"),
                    "Category": category,
                    "Qty": po.get("Qty"),
                    "Price": po.get("Price"),
                    "status": status,
                    "cost": total_cost,
                    "remaining_budget": remaining_budget,
                })
        
        GLOBAL_BUDGET_RESULTS = results
        print(GLOBAL_BUDGET_RESULTS)
        return f"Budget processing ready ({len(results)} POs analyzed). Next: BudgetSummary"

    except Exception as e:
        return f"Budget processing not ready "
    
def budget_summary(_=None) -> str:
    global GLOBAL_BUDGET_RESULTS
    
    if not GLOBAL_BUDGET_RESULTS:
        return "No budget results available. Run BudgetProcessor first."
    
    within = sum(1 for r in GLOBAL_BUDGET_RESULTS if r["status"] == "within")
    exceeded = sum(1 for r in GLOBAL_BUDGET_RESULTS if r["status"] == "exceeded")
    
    return f"Budget Overview: {within} within, {exceeded} exceeded"  # Plain string

def inventory_processor(_=None):                                                    #Provides quick overview of inventory health
    global GLOBAL_INVENTORY_RESULTS, GLOBAL_PROCUREMENT_DATA
    
    try:
        if not GLOBAL_PROCUREMENT_DATA:
            response = fetch_procurement_data()
            parsed = json.loads(response)
            if parsed["status"] != "success":
                return json.dumps({"status": "error", "message": parsed["message"]})
        
        inventory = GLOBAL_PROCUREMENT_DATA["Inventory"]
        results = []
        for batch in batch_items(inventory):
            for item in batch:
                stock = int(item["Current Stock"])
                reorder_level = int(item["Reorder Level"])
                status = "sufficient" if stock > reorder_level else "low"
                results.append({
                    "item": item["Item"],
                    "category": item["Category"],
                    "stock": stock,
                    "reorder_level": reorder_level,
                    "inventory_status": status,
                    "supplier": item["Supplier"]
                })
        
        GLOBAL_INVENTORY_RESULTS = results
        return f"Inventory processing ready ({len(results)} items). Next: InventorySummary"

    except Exception as e:
        return f"Budget processing not ready "

def inventory_summary(_=None):
    global GLOBAL_INVENTORY_RESULTS
    
    if not GLOBAL_INVENTORY_RESULTS:
        inventory_processor()

    if not GLOBAL_INVENTORY_RESULTS:
        return "No inventory results available. Run InventoryProcessor first."
    
    sufficient = sum(1 for r in GLOBAL_INVENTORY_RESULTS if r["inventory_status"] == "sufficient")
    low = sum(1 for r in GLOBAL_INVENTORY_RESULTS if r["inventory_status"] == "low")
    
    return f"Inventory Status: {sufficient} sufficient, {low} low. Next: ApprovalAgent"

GLOBAL_APPROVAL_RESULTS = {
    'auto_approved': [],
    'needs_approval': []
}

def approval_processor(_=None):
    global GLOBAL_APPROVAL_RESULTS
    
    try:
        # Reset previous results
        GLOBAL_APPROVAL_RESULTS = {'auto_approved': [], 'needs_approval': []}
        
        if not GLOBAL_PROCUREMENT_DATA:
            fetch_procurement_data()
            
        for po in GLOBAL_PROCUREMENT_DATA['POs']:
            # Find matching budget result
            matched = next(
                (r for r in GLOBAL_BUDGET_RESULTS 
                 if r['Item'] == po['Item'] 
                 and r['Category'] == po['Category']),
                None
            )
            
            if matched and matched['status'] == 'within':
                GLOBAL_APPROVAL_RESULTS['auto_approved'].append(po)
            else:
                GLOBAL_APPROVAL_RESULTS['needs_approval'].append(po)
                
        return "Approval processing complete. Next: ApprovalSummary"
        
    except Exception as e:
        return f"Approval processing failed: {str(e)}"

def approval_summary(_=None):
    data = GLOBAL_APPROVAL_RESULTS
    if not data:
        return "No approval results. Run ApprovalProcessor first."
    
    summary = f"""📋 Approval Overview:
✅ {len(data['auto_approved'])} POs auto-approved
⚠️ {len(data['needs_approval'])} POs need review
    
Top Items Requiring Approval:"""
    
    # Show just 3 most expensive items needing approval
    for po in sorted(data['needs_approval'], 
                    key=lambda x: x['Qty']*x['Price'], 
                    reverse=True)[:3]:
        summary += f"\n• {po['Item']} (PKR {po['Qty']*po['Price']:,})"
    
    return summary + "\n\nNext: Run Notifier for detailed suggestions"

import re

def clean_markdown(text):
    # Replace bold markdown **text** with uppercase
    text = re.sub(r"\*\*(.*?)\*\*", r"\1".upper(), text)
    # Replace numbered list formatting
    text = text.replace("\\n", "\n").replace("\n", "\n")
    return text.strip()



from tenacity import RetryError  # Add this at the top

def generate_suggestion(po):
    prompt = f"""Suggest solutions for approving this PO:
    
Item: {po['Item']}
Vendor: {po['Vendor']}
Quantity: {po['Qty']}
Price: PKR {po['Price']}
Total: PKR {po['Qty'] * po['Price']:,}
Category: {po['Category']}

Provide 3 specific, numbered recommendations:"""

    try:
        response = safe_chat_invoke(chat, prompt)
        return response.content  # Extract actual LLM response
    except RetryError as re:
        return f"Could not generate suggestion after retries: {str(re.last_attempt.exception())}"
    except Exception as e:
        return f"Could not generate suggestion: {str(e)}"


def notifier_tool(_=None):
    if not GLOBAL_APPROVAL_RESULTS:
        return "Error: No approval data available"
    
    if 'needs_approval' not in GLOBAL_APPROVAL_RESULTS:
        return "Error: No POs requiring approval"
    
    results = []
    for po in GLOBAL_APPROVAL_RESULTS['needs_approval']:
        try:
            suggestion = clean_markdown(generate_suggestion(po))
            subject = f"APPROVAL REQUIRED: {po['Item']}"
            body = f"""PO DETAILS:
Vendor: {po['Vendor']}
Amount: PKR {po['Qty']*po['Price']:,}
Category: {po['Category']}

SUGGESTED ACTIONS:
{suggestion}"""
            
            if send_email(subject, body, "syedaliahmedshah677@gmail.com"):
                results.append(f"Sent: {po['Item']}")
            else:
                results.append(f"Failed: {po['Item']}")
                
        except Exception as e:
            results.append(f"Error processing {po['Item']}: {str(e)}")
    
    return "\n".join(results)

import re

def write_rich_text(pdf, text, font='Arial', size=12):
    """Parse **bold** text and render with alternating bold and normal style"""
    pdf.set_font(font, '', size)
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            content = part[2:-2]
            pdf.set_font(font, 'B', size)
            pdf.multi_cell(0, 8, content)
            pdf.set_font(font, '', size)
        else:
            pdf.multi_cell(0, 8, part)



def report_generator(_=None):
    try:
        # Verify all data exists
        if not all([GLOBAL_PROCUREMENT_DATA, GLOBAL_BUDGET_RESULTS, 
                  GLOBAL_INVENTORY_RESULTS, GLOBAL_APPROVAL_RESULTS]):
            return "Missing data. Complete all previous steps first."
        
        # Create reports directory if it doesn't exist
        os.makedirs("reports", exist_ok=True)
        filename = f"reports/procurement_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # Initialize PDF with better settings
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        
        
        # ------ Cover Page ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 24)
        pdf.cell(0, 20, 'Procurement Analytics Report', 0, 1, 'C')
        pdf.ln(10)
        pdf.set_font('Arial', '', 14)
        pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}", 0, 1, 'C')
        pdf.ln(15)
        
        # ------ Table of Contents ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, 'Table of Contents', 0, 1)
        pdf.set_font('Arial', '', 12)
        pdf.cell(0, 10, '1. Executive Summary', 0, 1)
        pdf.cell(0, 10, '2. Budget Analysis', 0, 1)
        pdf.cell(0, 10, '3. Inventory Status', 0, 1)
        pdf.cell(0, 10, '4. Approval Recommendations', 0, 1)
        pdf.cell(0, 10, '5. Vendor Analysis', 0, 1)
        pdf.cell(0, 10, '6. Category Spending Trends', 0, 1)
        pdf.cell(0, 10, '7. Detailed PO Records', 0, 1)
        pdf.cell(0, 10, '8. Action Items', 0, 1)
        
        # ------ 1. Executive Summary ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '1. Executive Summary', 0, 1)
        pdf.ln(5)
        
        # Generate comprehensive summary using LLM
        summary_prompt = f"""Create a detailed executive summary for a procurement report covering these aspects:
        
        Budget Status:
        - Total POs processed: {len(GLOBAL_BUDGET_RESULTS)}
        - Within budget: {sum(1 for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'within')}
        - Exceeded budget: {sum(1 for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'exceeded')}
        - Largest budget overage: {max((x['cost'] - x['remaining_budget'] for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'exceeded'), default=0):,.2f}
        
        Inventory Status:
        - Total items tracked: {len(GLOBAL_INVENTORY_RESULTS)}
        - Items with sufficient stock: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'sufficient')}
        - Items below reorder level: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')}
        
        Approval Status:
        - Auto-approved POs: {len(GLOBAL_APPROVAL_RESULTS['auto_approved'])}
        - POs needing manual approval: {len(GLOBAL_APPROVAL_RESULTS['needs_approval'])}
        - Total value requiring approval: {sum(x['Qty']*x['Price'] for x in GLOBAL_APPROVAL_RESULTS['needs_approval']):,.2f}
        
        Provide 3-4 paragraphs highlighting key findings, risks, and opportunities in professional business language."""
        
        llm_summary = safe_chat_invoke(chat, summary_prompt)
        pdf.set_font('Arial', '', 12)
        write_rich_text(pdf, llm_summary.content)

        
        # ------ 2. Budget Analysis ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '2. Budget Analysis', 0, 1)
        
        # Create budget chart
        budget_df = pd.DataFrame(GLOBAL_BUDGET_RESULTS)
        budget_by_category = budget_df.groupby('Category').agg({
            'cost': 'sum',
            'remaining_budget': 'first'
        }).reset_index()
        
        plt.figure(figsize=(14, 7))
        budget_by_category.set_index('Category')[['cost', 'remaining_budget']].plot(kind='bar', stacked=True)
        plt.title('Budget Utilization by Category')
        plt.ylabel('Amount')
        plt.xlabel('Category')
        plt.xticks(rotation=65, ha='right')

        chart_path = "reports/budget_chart.png"
        plt.tight_layout()
        plt.savefig(chart_path)
        plt.close()
        
        # Add chart to PDF
        pdf.image(chart_path, x=10, y=30, w=180)
        pdf.ln(150)  # Space after image
        
        # Budget analysis text
        budget_prompt = f"""Analyze this budget data in detail:
        {budget_by_category.to_string()}
        
        Provide insights on:
        1. Top 3 categories by spending
        2. Categories with highest budget utilization
        3. Warning signs for potential overspending
        4. Recommendations for budget adjustments"""
        
        llm_budget = safe_chat_invoke(chat, budget_prompt)
        pdf.set_font('Arial', '', 12)
        write_rich_text(pdf, llm_budget.content)
        
        # ------ 3. Inventory Status ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '3. Inventory Status', 0, 1)
        
        # Inventory analysis
        inventory_prompt = f"""Analyze this inventory data:
        - Total items: {len(GLOBAL_INVENTORY_RESULTS)}
        - Low stock items: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')}
        - Critical items (stock < 50% of reorder level): {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['stock'] < (x['reorder_level'] * 0.5))}
        
        Provide:
        1. List of top 5 most critical inventory items
        2. Supplier performance analysis
        3. Recommendations for inventory optimization"""
        
        llm_inventory = safe_chat_invoke(chat, inventory_prompt)
        pdf.set_font('Arial', '', 12)
        write_rich_text(pdf,llm_inventory.content)
        
        # ------ 4. Approval Recommendations ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '4. Approval Recommendations', 0, 1)
        
        # Detailed approval analysis
        approval_prompt = f"""Analyze these POs requiring approval:
        {pd.DataFrame(GLOBAL_APPROVAL_RESULTS['needs_approval']).to_string()}
        
        Provide:
        1. Priority ranking of approvals needed
        2. Alternative solutions for high-cost items
        3. Negotiation strategies with vendors
        4. Process improvement suggestions"""
        
        llm_approval = safe_chat_invoke(chat, approval_prompt)
        pdf.set_font('Arial', '', 12)
        write_rich_text(pdf, llm_approval.content)
        
        # ------ 5. Vendor Analysis ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '5. Vendor Performance', 0, 1)
        
        # Vendor performance analysis
        vendor_prompt = f"""Analyze vendor performance from this data:
        {pd.DataFrame(GLOBAL_PROCUREMENT_DATA['POs']).groupby('Vendor').agg({
            'Price': ['count', 'mean', 'sum'],
            'Qty': 'sum'
        }).to_string()}
        
        Provide:
        1. Top performing vendors
        2. Vendors needing performance review
        3. Recommendations for vendor consolidation
        4. Suggested negotiation points"""
        
        llm_vendor = safe_chat_invoke(chat, vendor_prompt)
        pdf.set_font('Arial', '', 12)
        write_rich_text(pdf,  llm_vendor.content)
        
        # ------ 6. Category Spending Trends ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '6. Category Spending Trends', 0, 1)
        
        # Spending trend analysis
        trend_prompt = f"""Analyze spending trends by category:
        {pd.DataFrame(GLOBAL_PROCUREMENT_DATA['POs']).groupby(['Category', 'Date']).agg({
            'Price': 'sum',
            'Qty': 'sum'
        }).to_string()}
        
        Provide:
        1. Seasonal spending patterns
        2. Unexpected spikes/drops
        3. Category growth trends
        4. Forecasting for next quarter"""
        
        llm_trend = safe_chat_invoke(chat, trend_prompt)
        pdf.set_font('Arial', '', 12)
        write_rich_text(pdf,llm_trend.content)
        
        # ------ 7. Detailed PO Records ------
        pdf.add_page()
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, '7. Detailed PO Records', 0, 1)
        pdf.set_font('Arial', 'B', 10)
        
        # Table header
        pdf.cell(30, 10, 'Date', 1)
        pdf.cell(50, 10, 'Item', 1)
        pdf.cell(40, 10, 'Vendor', 1)
        pdf.cell(20, 10, 'Qty', 1)
        pdf.cell(25, 10, 'Price', 1)
        pdf.cell(25, 10, 'Total', 1, 1)
        
        # Table rows
        pdf.set_font('Arial', '', 8)
        for po in sorted(GLOBAL_PROCUREMENT_DATA['POs'], 
                        key=lambda x: x['Date'], 
                        reverse=True)[:50]:  # Show last 50 POs
            pdf.cell(30, 10, po['Date'], 1)
            pdf.cell(50, 10, po['Item'][:30], 1)
            pdf.cell(40, 10, po['Vendor'][:20], 1)
            pdf.cell(20, 10, str(po['Qty']), 1)
            pdf.cell(25, 10, f"{po['Price']:,.2f}", 1)
            pdf.cell(25, 10, f"{po['Qty']*po['Price']:,.2f}", 1, 1)
        
        # # ------ 8. Action Items ------
        # pdf.add_page()
        # pdf.set_font('Arial', 'B', 16)
        # pdf.cell(0, 10, '8. Recommended Action Items', 0, 1)
        
        # # Generate action items
        # action_prompt = """Based on all the previous analysis, create a numbered list of 
        # 10 specific, actionable recommendations for procurement process improvement, 
        # cost savings, and risk mitigation. Prioritize by impact and urgency."""
        
        # llm_actions = safe_chat_invoke(chat, action_prompt)
        # pdf.set_font('Arial', '', 12)
        # pdf.multi_cell(0, 8, llm_actions.content)
        
        # Footer
        pdf.set_y(-15)
        pdf.set_font('Arial', 'I', 8)
        pdf.cell(0, 10, f'Generated by Procurement Analytics Bot on {datetime.now().strftime("%Y-%m-%d")}', 0, 0, 'C')
        
        # Save PDF
        pdf.output(filename)
        
        # Clean up temporary files
        if os.path.exists(chart_path):
            os.remove(chart_path)
            
        return json.dumps({
            "status": "success",
            "file": filename,
            "pages": pdf.page_no(),
            "generated_at": datetime.now().isoformat(),
            "message": f"Procurement report generated at {filename}. Step 9 complete. YOU MUST NOW CALL SendProcurementReport to email it. DO NOT STOP."
        })

    
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc()
        })
    

def send_report_tool(_=None):
    try:
        # 1. Generate the report first
        result = json.loads(report_generator())
        if result["status"] != "success":
            return "Error generating report: " + result.get("message", "Unknown error")

        report_path = result["file"]
        
        # 2. Fetch stakeholder emails from Google Sheet
        try:
            client = get_gsheet_client()
            sheet = client.open("Invoices").worksheet("Stakeholders")
            stakeholders = sheet.get_all_records()
            recipient_emails = [s["stakeholders_email"] for s in stakeholders if s.get("stakeholders_email")]
            
            if not recipient_emails:
                return "Error: No valid emails found in Stakeholders sheet"
        except Exception as e:
            return f"Error fetching stakeholder emails: {str(e)}"

        # 3. Prepare email
        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = SENDER_EMAIL  # Main recipient
        msg['Bcc'] = ", ".join(recipient_emails)  # All stakeholders as BCC
        msg['Subject'] = f"Procurement Report - {datetime.now().strftime('%d %b %Y')}"

        # Improved email body
        body = f"""Dear Stakeholders,
        
Attached is the latest procurement report generated on {datetime.now().strftime('%d %B %Y')}.

Key Highlights:
- Generated report with {len(GLOBAL_PROCUREMENT_DATA['POs'])} purchase orders analyzed
- {len(GLOBAL_APPROVAL_RESULTS.get('needs_approval', []))} items require approval
- {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')} inventory items below threshold

Please review and let us know if you need any clarification.

Best regards,
Procurement Automation System
"""
        msg.attach(MIMEText(body, 'plain'))

        # Attach report
        with open(report_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
        msg.attach(part)

        # 4. Send email
        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()

        # 5. Cleanup
        if os.path.exists(report_path):
            os.remove(report_path)

        return "✅ Report successfully sent to all stakeholders"
    except Exception as e:
        return f"❌ Error sending report: {str(e)}"
    

# ---------- Tool Definitions ----------
tools = [
    Tool(name="FetchProcurementData", func=fetch_procurement_data, description="Fetch financial data from Google Sheets. Must be done first."),
    Tool(name="BudgetProcessor", func=budget_processor, description="Process all POs for budget checks. Returns success when done. MUST be followed by BudgetSummary."),
    Tool(name="BudgetSummary", func=budget_summary, description="Get summary of budget status. After this, you MUST call InventoryProcessor next."),
    Tool(name="InventoryProcessor", func=inventory_processor, description="Process all inventory items. After this, you MUST call InventorySummary next."),
    Tool(name="InventorySummary", func=inventory_summary, description="Get summary of inventory status. Requires InventoryProcessor to run first."),
    Tool(name="ApprovalProcessor", func=approval_processor,description="Process all POs for approval status"),
    Tool(name="ApprovalSummary", func=approval_summary,description="Get approval overview summary"),
    Tool(name="Notifier", func=notifier_tool, description="Send approval notifications via email. After this, you MUST call GenerateProcurementReport next. Requires ApprovalProcessor to run first."),
    Tool(name="GenerateProcurementReport", func=report_generator, description="Create PDF report of recent purchase orders."),
    Tool(name="SendProcurementReport", func=send_report_tool, description="Email the generated procurement report.")
]

# Initialize chat model with error handling

chat = ChatGroq(
    model="llama3-8b-8192",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY")
)


# Initialize agent
# Remove the SystemMessage import and initialization
# Keep the original agent initialization:
agent = initialize_agent(
    tools=tools,
    llm=chat,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,  # Changed agent type
    verbose=True,
    handle_parsing_errors=True,
    return_only_outputs=True,
    max_iterations=30,
    early_stopping_method="force"  # Prevents early termination
)

# Modify your task to be very explicit:
# Update your task prompt to be more explicit and sequential:
if __name__ == "__main__":
    print("Starting Procurement Monitor Agent...")
    task = """EXECUTE ALL STEPS IN THIS EXACT ORDER:

1. FetchProcurementData: Fetch all procurement data
2. BudgetProcessor: Process all budget checks
3. BudgetSummary: Show budget overview
4. InventoryProcessor: Process inventory status
5. InventorySummary: Show inventory overview
6. ApprovalProcessor: Determine approval statuses
7. ApprovalSummary: Show approval overview
8. Notifier: Send notifications with suggestions
9. GenerateProcurementReport: Create detailed report
10. SendProcurementReport: Send final Email report

STRICT RULES:
1. NEVER STOP BEFORE STEP 10
2. NEVER SAY 'FINAL ANSWER' BEFORE STEP 10
3. AFTER EACH STEP, MOVE TO THE NEXT TOOL.
4. IF YOU COMPLETE A STEP, SAY "Step X complete. Now call ToolY.
6. SUMMARY STEPS MUST FOLLOW PROCESSORS IMMEDIATELY
7. USE THIS EXACT SEQUENCE"""

    try:
        result = agent.run(task)
        print("\nFinal Result:", result)
    except Exception as e:
        print(f"Error running agent: {str(e)}")













# import os
# import json
# import base64
# import gspread
# import time
# import matplotlib.pyplot as plt
# import pandas as pd
# from fpdf import FPDF
# from datetime import datetime
# from dotenv import load_dotenv
# from email.mime.text import MIMEText
# from email.mime.multipart import MIMEMultipart
# from email.mime.application import MIMEApplication
# from google.oauth2.credentials import Credentials
# from google_auth_oauthlib.flow import InstalledAppFlow
# from googleapiclient.discovery import build
# from oauth2client.service_account import ServiceAccountCredentials
# from langchain.agents import Tool, initialize_agent, AgentType
# from langchain_groq import ChatGroq
# from langchain.prompts import ChatPromptTemplate
# from tenacity import retry, stop_after_attempt, wait_exponential
# import traceback
# load_dotenv()
# CLIENT_SECRETS_FILE = 'client_secret.json'
# SCOPES = ['https://www.googleapis.com/auth/gmail.send']
# GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# SPREADSHEET_NAME = "Invoices"
# SENDER_EMAIL = "syedaliahmed171@gmail.com"

# BATCH_SIZE = 5  # Used in all batch operations

# # ---------- Global State ----------
# GLOBAL_BUDGET_RESULTS = None
# GLOBAL_INVENTORY_RESULTS = None
# GLOBAL_PROCUREMENT_DATA = None

# # ---------- Google Sheets Client ----------
# def get_gsheet_client():
#     scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
#     creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
#     return gspread.authorize(creds)

# # ---------- Gmail Auth ----------
# def get_gmail_credentials():
#     creds = None
#     if os.path.exists('token.json'):
#         creds = Credentials.from_authorized_user_file('token.json', SCOPES)
#     if not creds or not creds.valid:
#         flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
#         creds = flow.run_local_server(port=0)
#         with open('token.json', 'w') as token:
#             token.write(creds.to_json())
#     return creds

# # ---------- Utility ----------
# def batch_items(items, batch_size=BATCH_SIZE):
#     for i in range(0, len(items), batch_size):
#         yield items[i:i + batch_size]

# # ---------- Email Function ----------
# def send_email(subject, body, recipient):
#     try:
#         creds = get_gmail_credentials()
#         service = build('gmail', 'v1', credentials=creds)

#         msg = MIMEMultipart()
#         msg['From'] = SENDER_EMAIL
#         msg['To'] = recipient
#         msg['Subject'] = subject
#         msg.attach(MIMEText(body, 'plain'))

#         raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
#         service.users().messages().send(
#             userId="me",
#             body={"raw": raw_msg}
#         ).execute()
#         return True
#     except Exception as e:
#         print(f"Failed to send email: {str(e)}")
#         return False

# # ---------- Safe LLM Invocation ----------
# @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
# def safe_chat_invoke(chain, input_dict):
#     try:
#         return chain.invoke(input_dict)
#     except Exception as e:
#         if "rate_limit_exceeded" in str(e):
#             print("Rate limit hit, waiting before retry...")
#             time.sleep(10)  # Wait 10 seconds before retrying
#         raise e

# # ---------- Core Functions ----------
# def fetch_procurement_data(_=None):
#     global GLOBAL_PROCUREMENT_DATA
#     try:
#         client = get_gsheet_client()
#         sheet = client.open(SPREADSHEET_NAME)

#         data = {
#             "POs": sheet.worksheet("PO's").get_all_records(),
#             "Budgets": sheet.worksheet("Budgets").get_all_records(),
#             "Spend": sheet.worksheet("Spend").get_all_records(),
#             "Inventory": sheet.worksheet("Inventory").get_all_records(),
#         }
#         GLOBAL_PROCUREMENT_DATA = data
#         return json.dumps({"status": "success", "data": data})
#     except Exception as e:
#         return json.dumps({"status": "error", "message": str(e)})

# def budget_processor(_=None) -> str:
#     global GLOBAL_BUDGET_RESULTS, GLOBAL_PROCUREMENT_DATA
    
#     GLOBAL_BUDGET_RESULTS = None  # Reset at start
#     # Skip if already processed
#     if GLOBAL_BUDGET_RESULTS is not None:
#         return json.dumps({"status": "success", "message": "Budget already processed"})
#     try:
#         if not GLOBAL_PROCUREMENT_DATA:
#             response = fetch_procurement_data()
#             parsed = json.loads(response)
#             if parsed["status"] != "success":
#                 return json.dumps({"status": "error", "message": parsed["message"]})
        
#         data = GLOBAL_PROCUREMENT_DATA
#         pos_list = data["POs"]
#         budgets = data["Budgets"]
#         spend = data["Spend"]

#         budget_map = {b["Category"]: float(b["Budget Amount"]) for b in budgets}
#         spend_map = {}
#         for s in spend:
#             cat = s["Category"]
#             amt = float(s.get("Amount Spent", 0))
#             spend_map[cat] = spend_map.get(cat, 0) + amt

#         results = []
#         for batch in batch_items(pos_list):
#             for po in batch:
#                 category = po["Category"]
#                 total_cost = float(po["Qty"]) * float(po["Price"])
#                 remaining_budget = budget_map.get(category, 0) - spend_map.get(category, 0)
#                 status = "within" if total_cost <= remaining_budget else "exceeded"
#                 results.append({
#                     "Item": po.get("Item"),
#                     "Category": category,
#                     "Qty": po.get("Qty"),
#                     "Price": po.get("Price"),
#                     "status": status,
#                     "cost": total_cost,
#                     "remaining_budget": remaining_budget,
#                 })
        
#         GLOBAL_BUDGET_RESULTS = results
#         print(GLOBAL_BUDGET_RESULTS)
#         return f"Budget processing ready ({len(results)} POs analyzed). Next: BudgetSummary"

#     except Exception as e:
#         return f"Budget processing not ready "
    
# def budget_summary(_=None) -> str:
#     global GLOBAL_BUDGET_RESULTS
    
#     if not GLOBAL_BUDGET_RESULTS:
#         return "No budget results available. Run BudgetProcessor first."
    
#     within = sum(1 for r in GLOBAL_BUDGET_RESULTS if r["status"] == "within")
#     exceeded = sum(1 for r in GLOBAL_BUDGET_RESULTS if r["status"] == "exceeded")
    
#     return f"Budget Overview: {within} within, {exceeded} exceeded"  # Plain string

# def inventory_processor(_=None):                                                    #Provides quick overview of inventory health
#     global GLOBAL_INVENTORY_RESULTS, GLOBAL_PROCUREMENT_DATA
    
#     try:
#         if not GLOBAL_PROCUREMENT_DATA:
#             response = fetch_procurement_data()
#             parsed = json.loads(response)
#             if parsed["status"] != "success":
#                 return json.dumps({"status": "error", "message": parsed["message"]})
        
#         inventory = GLOBAL_PROCUREMENT_DATA["Inventory"]
#         results = []
#         for batch in batch_items(inventory):
#             for item in batch:
#                 stock = int(item["Current Stock"])
#                 reorder_level = int(item["Reorder Level"])
#                 status = "sufficient" if stock > reorder_level else "low"
#                 results.append({
#                     "item": item["Item"],
#                     "category": item["Category"],
#                     "stock": stock,
#                     "reorder_level": reorder_level,
#                     "inventory_status": status,
#                     "supplier": item["Supplier"]
#                 })
        
#         GLOBAL_INVENTORY_RESULTS = results
#         return f"Inventory processing ready ({len(results)} items). Next: InventorySummary"

#     except Exception as e:
#         return f"Budget processing not ready "

# def inventory_summary(_=None):
#     global GLOBAL_INVENTORY_RESULTS
    
#     if not GLOBAL_INVENTORY_RESULTS:
#         inventory_processor()

#     if not GLOBAL_INVENTORY_RESULTS:
#         return "No inventory results available. Run InventoryProcessor first."
    
#     sufficient = sum(1 for r in GLOBAL_INVENTORY_RESULTS if r["inventory_status"] == "sufficient")
#     low = sum(1 for r in GLOBAL_INVENTORY_RESULTS if r["inventory_status"] == "low")
    
#     return f"Inventory Status: {sufficient} sufficient, {low} low. Next: ApprovalAgent"

# GLOBAL_APPROVAL_RESULTS = {
#     'auto_approved': [],
#     'needs_approval': []
# }

# def approval_processor(_=None):
#     global GLOBAL_APPROVAL_RESULTS
    
#     try:
#         # Reset previous results
#         GLOBAL_APPROVAL_RESULTS = {'auto_approved': [], 'needs_approval': []}
        
#         if not GLOBAL_PROCUREMENT_DATA:
#             fetch_procurement_data()
            
#         for po in GLOBAL_PROCUREMENT_DATA['POs']:
#             # Find matching budget result
#             matched = next(
#                 (r for r in GLOBAL_BUDGET_RESULTS 
#                  if r['Item'] == po['Item'] 
#                  and r['Category'] == po['Category']),
#                 None
#             )
            
#             if matched and matched['status'] == 'within':
#                 GLOBAL_APPROVAL_RESULTS['auto_approved'].append(po)
#             else:
#                 GLOBAL_APPROVAL_RESULTS['needs_approval'].append(po)
                
#         return "Approval processing complete. Next: ApprovalSummary"
        
#     except Exception as e:
#         return f"Approval processing failed: {str(e)}"

# def approval_summary(_=None):
#     data = GLOBAL_APPROVAL_RESULTS
#     if not data:
#         return "No approval results. Run ApprovalProcessor first."
    
#     summary = f"""📋 Approval Overview:
# ✅ {len(data['auto_approved'])} POs auto-approved
# ⚠️ {len(data['needs_approval'])} POs need review
    
# Top Items Requiring Approval:"""
    
#     # Show just 3 most expensive items needing approval
#     for po in sorted(data['needs_approval'], 
#                     key=lambda x: x['Qty']*x['Price'], 
#                     reverse=True)[:3]:
#         summary += f"\n• {po['Item']} (PKR {po['Qty']*po['Price']:,})"
    
#     return summary + "\n\nNext: Run Notifier for detailed suggestions"




# def generate_suggestion(po):
#     prompt = f"""Suggest solutions for approving this PO:
    
#     Item: {po['Item']}
#     Vendor: {po['Vendor']}
#     Quantity: {po['Qty']}
#     Price: PKR {po['Price']}
#     Total: PKR {po['Qty']*po['Price']:,}
#     Category: {po['Category']}
    
#     Provide 3 specific, numbered recommendations:"""
    
#     try:
#         return safe_chat_invoke(chat, prompt)
#     except Exception as e:
#         return f"Could not generate suggestion: {str(e)}"
    

# def notifier_tool(_=None):
#     if not GLOBAL_APPROVAL_RESULTS:
#         return "Error: No approval data available"
    
#     if 'needs_approval' not in GLOBAL_APPROVAL_RESULTS:
#         return "Error: No POs requiring approval"
    
#     results = []
#     for po in GLOBAL_APPROVAL_RESULTS['needs_approval']:
#         try:
#             suggestion = generate_suggestion(po)
#             subject = f"APPROVAL REQUIRED: {po['Item']}"
#             body = f"""PO DETAILS:
# Vendor: {po['Vendor']}
# Amount: PKR {po['Qty']*po['Price']:,}
# Category: {po['Category']}

# SUGGESTED ACTIONS:
# {suggestion}"""
            
#             if send_email(subject, body, "syedaliahmedshah677@gmail.com"):
#                 results.append(f"Sent: {po['Item']}")
#             else:
#                 results.append(f"Failed: {po['Item']}")
                
#         except Exception as e:
#             results.append(f"Error processing {po['Item']}: {str(e)}")
    
#     return "\n".join(results)




# def report_generator(_=None):
#     try:
#         # Verify all data exists
#         if not all([GLOBAL_PROCUREMENT_DATA, GLOBAL_BUDGET_RESULTS, 
#                   GLOBAL_INVENTORY_RESULTS, GLOBAL_APPROVAL_RESULTS]):
#             return "Missing data. Complete all previous steps first."
        
#         # Create reports directory if it doesn't exist
#         os.makedirs("reports", exist_ok=True)
#         filename = f"reports/procurement_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
#         # Initialize PDF with better settings
#         pdf = FPDF()
#         pdf.set_auto_page_break(auto=True, margin=15)
        
        
#         # ------ Cover Page ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 24)
#         pdf.cell(0, 20, 'Procurement Analytics Report', 0, 1, 'C')
#         pdf.ln(10)
#         pdf.set_font('Arial', '', 14)
#         pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}", 0, 1, 'C')
#         pdf.ln(15)
        
#         # ------ Table of Contents ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, 'Table of Contents', 0, 1)
#         pdf.set_font('Arial', '', 12)
#         pdf.cell(0, 10, '1. Executive Summary', 0, 1)
#         pdf.cell(0, 10, '2. Budget Analysis', 0, 1)
#         pdf.cell(0, 10, '3. Inventory Status', 0, 1)
#         pdf.cell(0, 10, '4. Approval Recommendations', 0, 1)
#         pdf.cell(0, 10, '5. Vendor Analysis', 0, 1)
#         pdf.cell(0, 10, '6. Category Spending Trends', 0, 1)
#         pdf.cell(0, 10, '7. Detailed PO Records', 0, 1)
#         pdf.cell(0, 10, '8. Action Items', 0, 1)
        
#         # ------ 1. Executive Summary ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '1. Executive Summary', 0, 1)
#         pdf.ln(5)
        
#         # Generate comprehensive summary using LLM
#         summary_prompt = f"""Create a detailed executive summary for a procurement report covering these aspects:
        
#         Budget Status:
#         - Total POs processed: {len(GLOBAL_BUDGET_RESULTS)}
#         - Within budget: {sum(1 for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'within')}
#         - Exceeded budget: {sum(1 for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'exceeded')}
#         - Largest budget overage: {max((x['cost'] - x['remaining_budget'] for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'exceeded'), default=0):,.2f}
        
#         Inventory Status:
#         - Total items tracked: {len(GLOBAL_INVENTORY_RESULTS)}
#         - Items with sufficient stock: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'sufficient')}
#         - Items below reorder level: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')}
        
#         Approval Status:
#         - Auto-approved POs: {len(GLOBAL_APPROVAL_RESULTS['auto_approved'])}
#         - POs needing manual approval: {len(GLOBAL_APPROVAL_RESULTS['needs_approval'])}
#         - Total value requiring approval: {sum(x['Qty']*x['Price'] for x in GLOBAL_APPROVAL_RESULTS['needs_approval']):,.2f}
        
#         Provide 3-4 paragraphs highlighting key findings, risks, and opportunities in professional business language."""
        
#         llm_summary = safe_chat_invoke(chat, summary_prompt)
#         pdf.set_font('Arial', '', 12)
#         pdf.multi_cell(0, 8, llm_summary.content)
        
#         # ------ 2. Budget Analysis ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '2. Budget Analysis', 0, 1)
        
#         # Create budget chart
#         budget_df = pd.DataFrame(GLOBAL_BUDGET_RESULTS)
#         budget_by_category = budget_df.groupby('Category').agg({
#             'cost': 'sum',
#             'remaining_budget': 'first'
#         }).reset_index()
        
#         plt.figure(figsize=(14, 7))
#         budget_by_category.set_index('Category')[['cost', 'remaining_budget']].plot(kind='bar', stacked=True)
#         plt.title('Budget Utilization by Category')
#         plt.ylabel('Amount')
#         plt.xlabel('Category')
#         plt.xticks(rotation=65, ha='right')

#         chart_path = "reports/budget_chart.png"
#         plt.tight_layout()
#         plt.savefig(chart_path)
#         plt.close()
        
#         # Add chart to PDF
#         pdf.image(chart_path, x=10, y=30, w=180)
#         pdf.ln(150)  # Space after image
        
#         # Budget analysis text
#         budget_prompt = f"""Analyze this budget data in detail:
#         {budget_by_category.to_string()}
        
#         Provide insights on:
#         1. Top 3 categories by spending
#         2. Categories with highest budget utilization
#         3. Warning signs for potential overspending
#         4. Recommendations for budget adjustments"""
        
#         llm_budget = safe_chat_invoke(chat, budget_prompt)
#         pdf.set_font('Arial', '', 12)
#         pdf.multi_cell(0, 8, llm_budget.content)
        
#         # ------ 3. Inventory Status ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '3. Inventory Status', 0, 1)
        
#         # Inventory analysis
#         inventory_prompt = f"""Analyze this inventory data:
#         - Total items: {len(GLOBAL_INVENTORY_RESULTS)}
#         - Low stock items: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')}
#         - Critical items (stock < 50% of reorder level): {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['stock'] < (x['reorder_level'] * 0.5))}
        
#         Provide:
#         1. List of top 5 most critical inventory items
#         2. Supplier performance analysis
#         3. Recommendations for inventory optimization"""
        
#         llm_inventory = safe_chat_invoke(chat, inventory_prompt)
#         pdf.set_font('Arial', '', 12)
#         pdf.multi_cell(0, 8, llm_inventory.content)
        
#         # ------ 4. Approval Recommendations ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '4. Approval Recommendations', 0, 1)
        
#         # Detailed approval analysis
#         approval_prompt = f"""Analyze these POs requiring approval:
#         {pd.DataFrame(GLOBAL_APPROVAL_RESULTS['needs_approval']).to_string()}
        
#         Provide:
#         1. Priority ranking of approvals needed
#         2. Alternative solutions for high-cost items
#         3. Negotiation strategies with vendors
#         4. Process improvement suggestions"""
        
#         llm_approval = safe_chat_invoke(chat, approval_prompt)
#         pdf.set_font('Arial', '', 12)
#         pdf.multi_cell(0, 8, llm_approval.content)
        
#         # ------ 5. Vendor Analysis ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '5. Vendor Performance', 0, 1)
        
#         # Vendor performance analysis
#         vendor_prompt = f"""Analyze vendor performance from this data:
#         {pd.DataFrame(GLOBAL_PROCUREMENT_DATA['POs']).groupby('Vendor').agg({
#             'Price': ['count', 'mean', 'sum'],
#             'Qty': 'sum'
#         }).to_string()}
        
#         Provide:
#         1. Top performing vendors
#         2. Vendors needing performance review
#         3. Recommendations for vendor consolidation
#         4. Suggested negotiation points"""
        
#         llm_vendor = safe_chat_invoke(chat, vendor_prompt)
#         pdf.set_font('Arial', '', 12)
#         pdf.multi_cell(0, 8, llm_vendor.content)
        
#         # ------ 6. Category Spending Trends ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '6. Category Spending Trends', 0, 1)
        
#         # Spending trend analysis
#         trend_prompt = f"""Analyze spending trends by category:
#         {pd.DataFrame(GLOBAL_PROCUREMENT_DATA['POs']).groupby(['Category', 'Date']).agg({
#             'Price': 'sum',
#             'Qty': 'sum'
#         }).to_string()}
        
#         Provide:
#         1. Seasonal spending patterns
#         2. Unexpected spikes/drops
#         3. Category growth trends
#         4. Forecasting for next quarter"""
        
#         llm_trend = safe_chat_invoke(chat, trend_prompt)
#         pdf.set_font('Arial', '', 12)
#         pdf.multi_cell(0, 8, llm_trend.content)
        
#         # ------ 7. Detailed PO Records ------
#         pdf.add_page()
#         pdf.set_font('Arial', 'B', 16)
#         pdf.cell(0, 10, '7. Detailed PO Records', 0, 1)
#         pdf.set_font('Arial', 'B', 10)
        
#         # Table header
#         pdf.cell(30, 10, 'Date', 1)
#         pdf.cell(50, 10, 'Item', 1)
#         pdf.cell(40, 10, 'Vendor', 1)
#         pdf.cell(20, 10, 'Qty', 1)
#         pdf.cell(25, 10, 'Price', 1)
#         pdf.cell(25, 10, 'Total', 1, 1)
        
#         # Table rows
#         pdf.set_font('Arial', '', 8)
#         for po in sorted(GLOBAL_PROCUREMENT_DATA['POs'], 
#                         key=lambda x: x['Date'], 
#                         reverse=True)[:50]:  # Show last 50 POs
#             pdf.cell(30, 10, po['Date'], 1)
#             pdf.cell(50, 10, po['Item'][:30], 1)
#             pdf.cell(40, 10, po['Vendor'][:20], 1)
#             pdf.cell(20, 10, str(po['Qty']), 1)
#             pdf.cell(25, 10, f"{po['Price']:,.2f}", 1)
#             pdf.cell(25, 10, f"{po['Qty']*po['Price']:,.2f}", 1, 1)
        
#         # # ------ 8. Action Items ------
#         # pdf.add_page()
#         # pdf.set_font('Arial', 'B', 16)
#         # pdf.cell(0, 10, '8. Recommended Action Items', 0, 1)
        
#         # # Generate action items
#         # action_prompt = """Based on all the previous analysis, create a numbered list of 
#         # 10 specific, actionable recommendations for procurement process improvement, 
#         # cost savings, and risk mitigation. Prioritize by impact and urgency."""
        
#         # llm_actions = safe_chat_invoke(chat, action_prompt)
#         # pdf.set_font('Arial', '', 12)
#         # pdf.multi_cell(0, 8, llm_actions.content)
        
#         # Footer
#         pdf.set_y(-15)
#         pdf.set_font('Arial', 'I', 8)
#         pdf.cell(0, 10, f'Generated by Procurement Analytics Bot on {datetime.now().strftime("%Y-%m-%d")}', 0, 0, 'C')
        
#         # Save PDF
#         pdf.output(filename)
        
#         # Clean up temporary files
#         if os.path.exists(chart_path):
#             os.remove(chart_path)
            
#         return json.dumps({
#             "status": "success",
#             "file": filename,
#             "pages": pdf.page_no(),
#             "generated_at": datetime.now().isoformat(),
#             "message": f"Procurement report generated at {filename}. Step 9 complete. YOU MUST NOW CALL SendProcurementReport to email it. DO NOT STOP."
#         })

    
#     except Exception as e:
#         return json.dumps({
#             "status": "error",
#             "message": str(e),
#             "traceback": traceback.format_exc()
#         })
    

# def send_report_tool(_=None):
#     try:
#         # 1. Generate the report first
#         result = json.loads(report_generator())
#         if result["status"] != "success":
#             return "Error generating report: " + result.get("message", "Unknown error")

#         report_path = result["file"]
        
#         # 2. Fetch stakeholder emails from Google Sheet
#         try:
#             client = get_gsheet_client()
#             sheet = client.open("Invoices").worksheet("Stakeholders")
#             stakeholders = sheet.get_all_records()
#             recipient_emails = [s["stakeholders_email"] for s in stakeholders if s.get("stakeholders_email")]
            
#             if not recipient_emails:
#                 return "Error: No valid emails found in Stakeholders sheet"
#         except Exception as e:
#             return f"Error fetching stakeholder emails: {str(e)}"

#         # 3. Prepare email
#         creds = get_gmail_credentials()
#         service = build('gmail', 'v1', credentials=creds)

#         msg = MIMEMultipart()
#         msg['From'] = SENDER_EMAIL
#         msg['To'] = SENDER_EMAIL  # Main recipient
#         msg['Bcc'] = ", ".join(recipient_emails)  # All stakeholders as BCC
#         msg['Subject'] = f"Procurement Report - {datetime.now().strftime('%d %b %Y')}"

#         # Improved email body
#         body = f"""Dear Stakeholders,
        
# Attached is the latest procurement report generated on {datetime.now().strftime('%d %B %Y')}.

# Key Highlights:
# - Generated report with {len(GLOBAL_PROCUREMENT_DATA['POs'])} purchase orders analyzed
# - {len(GLOBAL_APPROVAL_RESULTS.get('needs_approval', []))} items require approval
# - {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')} inventory items below threshold

# Please review and let us know if you need any clarification.

# Best regards,
# Procurement Automation System
# """
#         msg.attach(MIMEText(body, 'plain'))

#         # Attach report
#         with open(report_path, "rb") as f:
#             part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
#         part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
#         msg.attach(part)

#         # 4. Send email
#         raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
#         service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()

#         # 5. Cleanup
#         if os.path.exists(report_path):
#             os.remove(report_path)

#         return "✅ Report successfully sent to all stakeholders"
#     except Exception as e:
#         return f"❌ Error sending report: {str(e)}"
    

# # ---------- Tool Definitions ----------
# tools = [
#     Tool(name="FetchProcurementData", func=fetch_procurement_data, description="Fetch financial data from Google Sheets. Must be done first."),
#     Tool(name="BudgetProcessor", func=budget_processor, description="Process all POs for budget checks. Returns success when done. MUST be followed by BudgetSummary."),
#     Tool(name="BudgetSummary", func=budget_summary, description="Get summary of budget status. After this, you MUST call InventoryProcessor next."),
#     Tool(name="InventoryProcessor", func=inventory_processor, description="Process all inventory items. After this, you MUST call InventorySummary next."),
#     Tool(name="InventorySummary", func=inventory_summary, description="Get summary of inventory status. Requires InventoryProcessor to run first."),
#     Tool(name="ApprovalProcessor", func=approval_processor,description="Process all POs for approval status"),
#     Tool(name="ApprovalSummary", func=approval_summary,description="Get approval overview summary"),
#     Tool(name="Notifier", func=notifier_tool, description="Send approval notifications via email. After this, you MUST call GenerateProcurementReport next. Requires ApprovalProcessor to run first."),
#     Tool(name="GenerateProcurementReport", func=report_generator, description="Create PDF report of recent purchase orders."),
#     Tool(name="SendProcurementReport", func=send_report_tool, description="Email the generated procurement report.")
# ]

# # Initialize chat model with error handling

# chat = ChatGroq(
#     model="llama3-8b-8192",
#     temperature=0,
#     api_key=os.getenv("GROQ_API_KEY")
# )


# # Initialize agent
# # Remove the SystemMessage import and initialization
# # Keep the original agent initialization:
# agent = initialize_agent(
#     tools=tools,
#     llm=chat,
#     agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,  # Changed agent type
#     verbose=True,
#     handle_parsing_errors=True,
#     return_only_outputs=True,
#     max_iterations=30,
#     early_stopping_method="force"  # Prevents early termination
# )

# # Modify your task to be very explicit:
# # Update your task prompt to be more explicit and sequential:
# if __name__ == "__main__":
#     print("Starting Procurement Monitor Agent...")
#     task = """EXECUTE ALL STEPS IN THIS EXACT ORDER:

# 1. FetchProcurementData: Fetch all procurement data
# 2. BudgetProcessor: Process all budget checks
# 3. BudgetSummary: Show budget overview
# 4. InventoryProcessor: Process inventory status
# 5. InventorySummary: Show inventory overview
# 6. ApprovalProcessor: Determine approval statuses
# 7. ApprovalSummary: Show approval overview
# 8. Notifier: Send notifications with suggestions
# 9. GenerateProcurementReport: Create detailed report
# 10. SendProcurementReport: Send final Email report

# STRICT RULES:
# 1. NEVER STOP BEFORE STEP 10
# 2. NEVER SAY 'FINAL ANSWER' BEFORE STEP 10
# 3. AFTER EACH STEP, MOVE TO THE NEXT TOOL.
# 4. IF YOU COMPLETE A STEP, SAY "Step X complete. Now call ToolY.
# 6. SUMMARY STEPS MUST FOLLOW PROCESSORS IMMEDIATELY
# 7. USE THIS EXACT SEQUENCE"""

#     try:
#         result = agent.run(task)
#         print("\nFinal Result:", result)
#     except Exception as e:
#         print(f"Error running agent: {str(e)}")