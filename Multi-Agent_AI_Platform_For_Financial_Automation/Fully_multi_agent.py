# Unified Finance Router Agent with LangChain Router + Multi-Agent Logic

import os
import json
import base64
import gspread
import pandas as pd
import matplotlib.pyplot as plt
import traceback
import re
import time
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
from langchain.prompts import StringPromptTemplate, ChatPromptTemplate
from typing import List
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from dotenv import load_dotenv
from langchain.chains import LLMChain
from langchain.schema import AgentFinish
from langchain_groq import ChatGroq
from typing import List

# ---------- Load Environment ----------
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


# ----------Streamlit--------
from langchain.callbacks.base import BaseCallbackHandler

class StreamCaptureHandler(BaseCallbackHandler):
    def __init__(self):
        self.logs = []

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.logs.append(token)

    def get_logs(self):
        return "".join(self.logs)

    def reset(self):
        self.logs = []



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

# ---------- Shared Data Structures ----------
class SharedData:
    def __init__(self):
        self.payroll_data = None
        self.invoice_data = None
        self.last_execution = {}

shared_data = SharedData()


chat = ChatGroq(
    model="llama3-70b-8192",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY")
)

# ------------------ TOOL FUNCTION HEADS ONLY ------------------
# Payroll
def get_invoice_data_from_sheet():
    try:
        client = get_gsheet_client()
        spreadsheet = client.open(SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet("Invoices")
        return worksheet.get_all_records()
    except Exception as e:
        print(f"Error fetching invoice data: {e}")
        return []

def fetch_payroll_data_tool(_=None):
    """Fetch all payroll data from Google Sheets"""
    try:
        client = get_gsheet_client()
        spreadsheet = client.open(SPREADSHEET_NAME)
        
        data = {
            "employees": spreadsheet.worksheet("Employees").get_all_records(),
            "attendance": spreadsheet.worksheet("Attendance").get_all_records(),
            "policy": spreadsheet.worksheet("SalaryPolicy").get_all_records()
        }
        shared_data.payroll_data = data
        return json.dumps({"status": "success", "data": data})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Error fetching data: {str(e)}"})


def calculate_salaries_tool(_=None):
    """Calculate all employee salaries"""
    try:
        if shared_data.payroll_data is None:
            fetch_result = json.loads(fetch_payroll_data_tool())
            if fetch_result["status"] != "success":
                return fetch_result["message"]
            data = fetch_result["data"]
        else:
            data = shared_data.payroll_data
            
        employees = data["employees"]
        attendance = data["attendance"]
        policy_map = {p["rule_name"]: int(p["value"]) for p in data["policy"]}
        
        results = []
        for emp in employees:
            emp_id = emp["employee_id"]
            att = next((a for a in attendance if a["employee_id"] == emp_id), None)
            if not att:
                continue

            base = emp["base_salary"]
            if isinstance(base, str):
                base = int(base.replace(",", ""))
            else:
                base = int(base)
            department = emp.get("department", "Unassigned")
            leaves = int(att.get("leaves_taken", 0))
            allowed = int(att.get("allowed_leaves", 0))
            late = int(att.get("late_arrivals", 0))
            overtime = int(att.get("overtime_hours", 0))

            extra_leaves = max(0, leaves - allowed)
            deductions = (extra_leaves * policy_map.get("leave_penalty", 0)) + \
                         (late * policy_map.get("late_penalty", 0))
            bonus = min(overtime, policy_map.get("max_overtime_allowed", 20)) * \
                    policy_map.get("overtime_rate", 0)
            net = base - deductions + bonus

            results.append({
                "employee_id": emp_id,
                "name": emp["name"],
                "email": emp["email"],
                "base_salary": base,
                "deductions": deductions,
                "bonus": bonus,
                "net_salary": net,
                "department": department
            })
            
        return json.dumps({"status": "success", "data": results})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


from fpdf import FPDF
import os
import json
from datetime import datetime

def generate_payslips_tool(_=None, **kwargs):
    """Generate PDF payslips for all employees"""
    try:
        calc_result = json.loads(calculate_salaries_tool())
        if calc_result["status"] != "success":
            return calc_result["message"]
            
        employees = calc_result["data"]
        os.makedirs("payslips", exist_ok=True)
        
        client = get_gsheet_client()
        payslip_sheet = client.open(SPREADSHEET_NAME).worksheet("Payslips")
        existing_records = payslip_sheet.get_all_records()

        today_date = datetime.now().strftime('%Y-%m-%d')
        current_month = datetime.now().strftime('%Y-%m')
        results = []

        for emp in employees:
            emp_id = emp["employee_id"]
            filename = f"payslips/payslip_{emp_id}.pdf"

            # ---------------- PDF DESIGN ----------------
            pdf = FPDF()
            pdf.add_page()
            pdf.set_auto_page_break(auto=True, margin=15)

            # Header banner
            pdf.set_fill_color(40, 60, 120)  # Dark blue
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", "B", 16)
            pdf.cell(0, 15, "MONTHLY PAYSLIP", ln=True, align='C', fill=True)
            pdf.ln(10)

            # Employee Info Section
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Arial", "B", 12)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(0, 10, "Employee Information", ln=True, fill=True)

            pdf.set_font("Arial", "", 12)
            pdf.cell(100, 8, f"Name: {emp['name']}", ln=True)
            pdf.cell(100, 8, f"Employee ID: {emp_id}", ln=True)
            pdf.cell(100, 8, f"Department: {emp['department']}", ln=True)
            pdf.cell(100, 8, f"Period: {datetime.now().strftime('%B %Y')}", ln=True)
            pdf.ln(8)

            # Salary Breakdown Section
            pdf.set_font("Arial", "B", 12)
            pdf.set_fill_color(240, 240, 240)
            pdf.cell(0, 10, "Salary Breakdown", ln=True, fill=True)

            pdf.set_font("Arial", "", 12)
            pdf.cell(100, 8, f"Base Salary", border=1)
            pdf.cell(90, 8, f"${emp['base_salary']:,}", border=1, ln=True)

            pdf.cell(100, 8, f"Deductions", border=1)
            pdf.cell(90, 8, f"${emp['deductions']:,}", border=1, ln=True)

            pdf.cell(100, 8, f"Bonus", border=1)
            pdf.cell(90, 8, f"${emp['bonus']:,}", border=1, ln=True)

            pdf.set_font("Arial", "B", 12)
            pdf.cell(100, 10, f"Net Salary", border=1)
            pdf.cell(90, 10, f"${emp['net_salary']:,}", border=1, ln=True)

            # Footer
            pdf.set_y(-30)
            pdf.set_font("Arial", "I", 10)
            pdf.set_text_color(100)
            pdf.cell(0, 10, "This payslip is system generated. For queries, contact HR.", ln=True, align='C')

            pdf.output(filename)

            # ---------------- Sheet Record ----------------
            if not any(
                r.get("employee_id") == emp_id and r.get("month", "").startswith(current_month)
                for r in existing_records
            ):
                payslip_sheet.append_row([
                    emp_id,
                    emp["name"],
                    emp["net_salary"],
                    current_month
                ])
                results.append(f"✅ Payslip recorded for {emp['name']}")
            else:
                results.append(f"⚠️ Payslip already recorded for {emp['name']}")

        return json.dumps({
            "status": "success", 
            "message": "Payslips ready. NEXT STEP: Call SendPayslips to email them.",
            "completion_flag": "ready_for_email"
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def send_payslips_tool(_=None, **kwargs):
    """Send payslip emails to all employees"""
    try:
        if shared_data.payroll_data is None:
            fetch_result = json.loads(fetch_payroll_data_tool())
            if fetch_result["status"] != "success":
                return fetch_result["message"]
            employees = fetch_result["data"]["employees"]
        else:
            employees = shared_data.payroll_data["employees"]

        payslip_files = [f for f in os.listdir("payslips") if f.startswith("payslip_")]
        if not payslip_files:
            return "⚠️ No payslips found. Generate them first."

        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)
        results = []
        
        for emp in employees:
            filename = f"payslips/payslip_{emp['employee_id']}.pdf"
            if not os.path.exists(filename):
                results.append(f"⚠️ Payslip not found for {emp['name']}")
                continue

            msg = MIMEMultipart()
            msg['From'] = SENDER_EMAIL
            msg['To'] = emp['email']
            msg['Subject'] = f"Your Payslip - {datetime.now().strftime('%B %Y')}"

            body = f"""Dear {emp['name']},
Please find attached your payslip for {datetime.now().strftime('%B %Y')}.

Details:
* Employee ID: {emp['employee_id']}
* Department: {emp['department']}

If you have any questions, please contact HR.

Best regards,
Payroll Department
"""
            msg.attach(MIMEText(body, 'plain'))
            with open(filename, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(filename))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(filename)}"'
            msg.attach(part)

            raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()
            results.append(f"✅ Sent to {emp['name']} ({emp['email']})")

        return "📤 Email sending results:\n" + "\n".join(results)
    except Exception as e:
        return f"❌ Error sending payslips: {str(e)}"


# Invoice

from fpdf import FPDF
import os

def create_invoice_pdf(data, filename):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.set_font("Arial", "B", 16)
    
    # Invoice Header Banner
    pdf.set_fill_color(50, 60, 100)  # Dark blue
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 15, "INVOICE", ln=True, align='C', fill=True)
    pdf.ln(10)

    # Reset text color
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "", 12)
    
    # Customer Details
    pdf.set_fill_color(220, 220, 220)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Customer Details", ln=True, fill=True)

    pdf.set_font("Arial", "", 12)
    pdf.cell(100, 8, f"Name: {data.get('customer_name', 'N/A')}", ln=True)
    pdf.cell(100, 8, f"Email: {data.get('customer_email', 'N/A')}", ln=True)
    pdf.cell(100, 8, f"Invoice Date: {data.get('date', 'N/A')}", ln=True)
    pdf.cell(100, 8, f"Due Date: {data.get('due_date', 'N/A')}", ln=True)
    pdf.ln(10)

    # Invoice Table
    pdf.set_font("Arial", "B", 12)
    pdf.set_fill_color(80, 90, 150)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(140, 10, "Description", border=1, fill=True)
    pdf.cell(50, 10, "Amount", border=1, fill=True, ln=True)

    pdf.set_font("Arial", "", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.set_fill_color(245, 245, 245)
    pdf.cell(140, 10, "Services Rendered", border=1, fill=True)
    pdf.cell(50, 10, f"${data.get('amount', '0.00')}", border=1, fill=True, ln=True)

    # Total
    pdf.set_font("Arial", "B", 12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(140, 10, "Total", border=1, fill=True)
    pdf.cell(50, 10, f"${data.get('amount', '0.00')}", border=1, fill=True, ln=True)

    # Footer
    pdf.set_y(-30)
    pdf.set_font("Arial", "I", 10)
    pdf.set_text_color(100)
    pdf.cell(0, 10, "Thank you for your business!", ln=True, align='C')

    # Save
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    pdf.output(filename)

def send_invoice_via_gmail(to_email, subject, body, attachment_path, is_html=False):
    try:
        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html' if is_html else 'plain'))

        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
        msg.attach(part)

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()
        return True
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

def create_all_invoice_pdfs_tool(_=None, **kwargs):
    """Create invoice PDFs for all customers"""
    shared_data.invoice_data = get_invoice_data_from_sheet()
    for invoice in shared_data.invoice_data:
        filename = f"invoices/invoice_{invoice['customer_name'].replace(' ', '_')}.pdf"
        os.makedirs("invoices", exist_ok=True)
        create_invoice_pdf(invoice, filename)
    return "✅ All invoices created as PDF files."


def send_all_invoices_tool(_=None, **kwargs):
    """Send invoice emails with attached PDFs to all customers"""
    if shared_data.invoice_data is None:
        shared_data.invoice_data = get_invoice_data_from_sheet()
        
    responses = []
    for invoice in shared_data.invoice_data:
        filename = f"invoices/invoice_{invoice['customer_name'].replace(' ', '_')}.pdf"
        if not os.path.exists(filename):
            responses.append(f"❌ PDF not found for {invoice['customer_name']}. Skipping.")
            continue
            
        success = send_invoice_via_gmail(
            to_email=invoice['customer_email'],
            subject="Your Invoice from Our Company",
            body="Hello, please find your invoice attached.",
            attachment_path=filename
        )
        if success:
            responses.append(f"✅ Sent invoice to {invoice['customer_name']}")
        else:
            responses.append(f"❌ Failed to send invoice to {invoice['customer_name']}")
    return "\n".join(responses)


def remind_overdue_invoices_tool(_=None, **kwargs):
    """Send reminders for overdue unpaid invoices"""
    if shared_data.invoice_data is None:
        shared_data.invoice_data = get_invoice_data_from_sheet()
        
    today = datetime.today().strftime('%Y-%m-%d')
    results = []
    
    for invoice in shared_data.invoice_data:
        if invoice['status'] == 'unpaid' and invoice['due_date'] < today:
            filename = f"invoices/{invoice['customer_name'].replace(' ', '_')}_reminder.pdf"
            create_invoice_pdf(invoice, filename)

            pay_now_url = f"https://script.google.com/macros/s/AKfycbwmJ-cxnBxqtrbM0h3xobFvQ3nU9ATKhYPHflBx7fJcJTGtT2Hh3nZjwZNa28tC5b0W/exec?invoice_id={invoice['invoice_id']}"

            html_body = f"""
            <p>Dear {invoice['customer_name']},</p>
            <p>This is a reminder that your invoice dated <b>{invoice['date']}</b> is overdue.</p>
            <p>Amount Due: <b>${invoice['amount']}</b></p>
            <p>Please click the button below to mark your invoice as paid:</p>
            <a href="{pay_now_url}" style="background-color:#28a745;color:white;padding:10px 15px;text-decoration:none;border-radius:5px;">✅ Pay Now</a>
            <p>Thank you!</p>
            """

            sent = send_invoice_via_gmail(
                to_email=invoice['customer_email'],
                subject="⏰ Payment Reminder - Invoice Overdue",
                body=html_body,
                attachment_path=filename,
                is_html=True
            )

            if sent:
                results.append(f"{invoice['customer_name']} ⏰ Reminder sent.")

    return "\n".join(results) if results else "No overdue invoices."

def mark_paid_invoices_tool(_=None, **kwargs):
    """Mark invoices as paid based on sheet data. 
    This tool should be called only once. It will return which invoices are already paid and which are unpaid.
    """
    shared_data.invoice_data = get_invoice_data_from_sheet()

    if not shared_data.invoice_data:
        return "⚠️ No invoice data found."

    paid = []
    unpaid = []

    for invoice in shared_data.invoice_data:
        status = invoice['status'].strip().lower()
        if status == 'paid':
            paid.append(f"{invoice['customer_name']} (ID: {invoice['invoice_id']})")
        elif status == 'unpaid':
            unpaid.append(f"{invoice['customer_name']} (ID: {invoice['invoice_id']})")

    result = []

    if paid:
        result.append(f"✅ Paid invoices: {', '.join(paid)}")
    else:
        result.append("✅ No invoices are marked as paid.")

    if unpaid:
        result.append(f"⏳ Unpaid invoices: {', '.join(unpaid)}")
    else:
        result.append("🎉 All invoices are paid.")

    return "\n".join(result)


# Finance Report

def fetch_financial_data_tool(_=None):
    """Fetch financial data (Income Statement, Balance Sheet, Cash Flow) and stakeholder emails from Google Sheets"""
    try:
        client = get_gsheet_client()
        sheet = client.open(SPREADSHEET_NAME)

        data = {
            "income_statement": sheet.worksheet("Income_Statement").get_all_records(),
            "balance_sheet": sheet.worksheet("Balance_Sheet").get_all_records(),
            "cash_flow": sheet.worksheet("Cash_Flow").get_all_records(),
            "stakeholders": sheet.worksheet("Stakeholders").get_all_records()  # Now consistent

            
        }
        return json.dumps({"status": "success", "data": data})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ---------- Tool: Calculate Financial Metrics ----------
def calculate_financial_metrics_tool(_=None):
    """Calculate net profit, equity, and net cash flow per year"""
    try:
        raw = json.loads(fetch_financial_data_tool())
        if raw["status"] != "success":
            return json.dumps({"status": "error", "message": raw["message"]})

        data = raw["data"]
        income_df = pd.DataFrame(data["income_statement"]).set_index("Metric")
        bs_df = pd.DataFrame(data["balance_sheet"]).set_index("Metric")
        cf_df = pd.DataFrame(data["cash_flow"]).set_index("Category")

        # Clean and convert values to float
        for df in [income_df, bs_df, cf_df]:
            for year in ['2023 (PKR)', '2024 (PKR)', '2025 (PKR)']:
                df[year] = (df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float))


        results = {}

        for year in ['2023 (PKR)', '2024 (PKR)', '2025 (PKR)']:
            # Net Profit
            net_profit = (
                income_df.loc['Revenue', year] -
                income_df.loc['COGS', year] -
                income_df.loc['Operating Expenses', year] -
                income_df.loc['Other Expenses', year]
            )

            # Equity = Total Assets - Total Liabilities
            total_assets = (
                bs_df.loc['Cash', year] +
                bs_df.loc['Inventory', year] +
                bs_df.loc['Equipment', year]
            )
            total_liabilities = (
                bs_df.loc['Loans', year] +
                bs_df.loc['Accounts Payable', year]
            )
            equity = total_assets - total_liabilities

            # Net Cash Flow
            starting_balance = cf_df.loc['Starting Balance', year]
            operating_cash = cf_df.loc['Net Operating Cash Flow', year]
            investing_cash = cf_df.loc['Net Investing Cash Flow', year]
            financing_cash = cf_df.loc['Net Financing Cash Flow', year]
            net_cash_flow = operating_cash + investing_cash + financing_cash
            ending_balance = starting_balance + net_cash_flow

            results[year] = {
                "net_profit": round(net_profit),
                "total_assets": round(total_assets),
                "total_liabilities": round(total_liabilities),
                "equity": round(equity),
                "starting_cash": round(starting_balance),
                "net_cash_flow": round(net_cash_flow),
                "ending_cash": round(ending_balance)
            }

        return json.dumps({"status": "success", "data": results})

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
# ---------- Tool: Generate LLM Summary & Comparative Insights ----------

# ---------- Modified Tool: Generate Chart + Insight ----------
def generate_chart_insight_tool(input_json: str) -> str:
    """Generate financial charts and insights for ALL years"""
    try:
        try:
            input_data = json.loads(input_json)  # Try direct parse first
        except json.JSONDecodeError:
            # Extract JSON from text if wrapped (e.g., "Here is your data: {...}")
            json_start = input_json.find('{')
            json_end = input_json.rfind('}') + 1
            if json_start != -1 and json_end != 0:
                input_data = json.loads(input_json[json_start:json_end])
            else:
                return json.dumps({"status": "error", "message": "Invalid JSON input"})

        chart_type = input_data["chart_type"]
        os.makedirs("reports", exist_ok=True)
        client = get_gsheet_client()
        years = ["2023 (PKR)", "2024 (PKR)", "2025 (PKR)"]
        results = []

        if chart_type == "income":
            income_df = pd.DataFrame(client.open(SPREADSHEET_NAME).worksheet("Income_Statement").get_all_records())
            income_df.set_index("Metric", inplace=True)
            for year in years:
                income_df[year] = income_df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float)
                data = {
                    "COGS": income_df.at["COGS", year],
                    "Operating Expenses": income_df.at["Operating Expenses", year],
                    "Other Expenses": income_df.at["Other Expenses", year]
                }
                
                fig, ax = plt.subplots()
                ax.pie(data.values(), labels=data.keys(), autopct="%1.1f%%")
                ax.set_title(f"Income Statement {year}: Expense Breakdown")
                path = f"reports/income_{year.replace(' ', '_')}.png"
                plt.savefig(path)
                plt.close()
                                
                total = sum(data.values())
                percent_data = {k: round(v / total * 100, 1) for k, v in data.items()}
                insight = generate_insight("Income Statement", year, percent_data)
                results.append({
                    "year": year,
                    "chart_path": path,
                    "insight": insight
                })

        elif chart_type == "balance":
            balance_df = pd.DataFrame(client.open(SPREADSHEET_NAME).worksheet("Balance_Sheet").get_all_records())
            balance_df.set_index("Metric", inplace=True)
            for year in years:
                balance_df[year] = balance_df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float)
                assets = balance_df.loc[["Cash", "Inventory", "Equipment"], year].sum()
                liabilities = balance_df.loc[["Loans", "Accounts Payable"], year].sum()
                data = {"Assets": assets, "Liabilities": liabilities}
                
                fig, ax = plt.subplots()
                ax.pie(data.values(), labels=data.keys(), autopct="%1.1f%%")
                ax.set_title(f"Balance Sheet {year}: Assets vs Liabilities")
                path = f"reports/balance_{year.replace(' ', '_')}.png"
                plt.savefig(path)
                plt.close()
                
                total = sum(data.values())
                percent_data = {k: round(v / total * 100, 1) for k, v in data.items()}
                insight = generate_insight("Balance Sheet", year, percent_data)

                results.append({
                    "year": year,
                    "chart_path": path,
                    "insight": insight
                })

        elif chart_type == "cashflow":
            cf_df = pd.DataFrame(client.open(SPREADSHEET_NAME).worksheet("Cash_Flow").get_all_records())
            cf_df.set_index("Category", inplace=True)
            cf_data = []
            
            for year in years:
                cf_df[year] = cf_df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float)
                cf_data.append({
                    "Operating": cf_df.at["Net Operating Cash Flow", year],
                    "Investing": cf_df.at["Net Investing Cash Flow", year],
                    "Financing": cf_df.at["Net Financing Cash Flow", year]
                })
            
            # Single comparison chart
            fig, ax = plt.subplots()
            width = 0.2
            for i, label in enumerate(["Operating", "Investing", "Financing"]):
                ax.bar(
                    [x + i * width for x in range(len(years))],
                    [cf[label] for cf in cf_data],
                    width,
                    label=label
                )
            ax.set_title("Cash Flow Comparison by Year")
            ax.set_xticks([x + width for x in range(len(years))])
            ax.set_xticklabels([y.split()[0] for y in years])
            ax.legend()
            path = "reports/cashflow_comparison.png"
            plt.savefig(path)
            plt.close()
            
            insight = generate_insight("Cash Flow", "2023-2025", cf_data)
            
            results.append({
                "year": "2023-2025",
                "chart_path": path,
                "insight": insight
            })

        else:
            return json.dumps({"status": "error", "message": "Invalid chart type"})

        return json.dumps({
            "status": "success",
            "results": results,
            "message": f"Generated {len(results)} {chart_type} charts"
        })

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def generate_insight(chart_type: str, year: str, data: dict) -> str:
    """Generate insight text for a chart (70B-compatible)"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a financial analyst. Return ONLY the raw insight text with NO additional formatting, quotes, or JSON."),
        ("human", "Given the chart data:\n\nChart Type: {chart_type}\nYear: {year}\nPercentage Breakdown: {values}\n\nWrite a short insight (2-3 lines).")
    ])
    chain = prompt | chat
    
    # Get raw output (70B might wrap in extra text)
    raw_output = chain.invoke({
        "chart_type": chart_type,
        "year": year,
        "values": data
    }).content
    
    # Extract just the insight (remove quotes, extra text)
    insight = raw_output.strip().strip('"').strip("'").split("\n")[0]
    return insight

def write_summary_with_bold(pdf, summary_text):
    """Writes summary text with bold headings (optimized version)."""
    pdf.set_font("DejaVu", '', 12)  # Default font
    
    for line in summary_text.split('\n'):
        line = line.strip()  # Clean whitespace universally
        
        # Detect and process headings
        if line.startswith("**") and line.endswith("**"):
            heading_text = line.replace("*", "").strip()
            pdf.set_font("DejaVu", 'B', 12)
            pdf.cell(0, 10, heading_text, ln=True)
            pdf.set_font("DejaVu", '', 12)  # Reset font
        else:
            pdf.multi_cell(0, 10, line)
        
        pdf.ln(2)  # Balanced spacing

def generate_financial_summary_tool(_=None):
    """Generate an LLM-based financial performance summary (TEXT ONLY)"""
    try:
        calc_result = json.loads(calculate_financial_metrics_tool())
        if calc_result["status"] != "success":
            return json.dumps({"status": "error", "message": "Unable to get financial metrics."})

        metrics = calc_result["data"]

        summary_input = "\n".join([
            f"In {year}, the net profit was PKR {data['net_profit']:,}, total assets were PKR {data['total_assets']:,}, "
            f"liabilities PKR {data['total_liabilities']:,}, equity PKR {data['equity']:,}, net cash flow PKR {data['net_cash_flow']:,} "
            f"and ending cash PKR {data['ending_cash']:,}."
            for year, data in metrics.items()
        ])

        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a financial analyst generating summaries."),
            ("human", "Given the following financial metrics:\n\n{summary_input}\n\nWrite a detailed financial performance summary. "
            "Use headings (e.g., **headings**) and paragraphs.")
        ])
        chain = prompt | chat

        response = chain.invoke({"summary_input": summary_input})
        
        return json.dumps({
            "status": "success", 
            "summary": response.content  # Return the raw text with **markers**
        })

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
    
    
    


# ---------- Modified Tool: Generate Financial Report ----------
def generate_financial_report_tool(_=None):
    """Generate PDF report using pre-generated charts and insights"""
    try:
        # Get metrics
        calc_result = json.loads(calculate_financial_metrics_tool())
        if calc_result["status"] != "success":
            return json.dumps({"status": "error", "message": "Unable to get financial metrics."})
        
        metrics = calc_result["data"]
        
        # Get summary
        summary_result = json.loads(generate_financial_summary_tool())
        summary_text = summary_result["summary"] if summary_result["status"] == "success" else "Summary not available."
        
        # Generate all charts and insights
        years = ["2023 (PKR)", "2024 (PKR)", "2025 (PKR)"]
        chart_data = {
            "income": [],
            "balance": [],
            "cashflow": []
        }
        
                # Generate income and balance charts for each year
                # ✅ Generate income charts once
        income_result = json.loads(generate_chart_insight_tool(json.dumps({
            "chart_type": "income"
        })))
        if income_result["status"] == "success":
            for item in income_result["results"]:
                if "chart_path" in item and "insight" in item:
                    chart_data["income"].append({
                        "path": item["chart_path"],
                        "insight": item["insight"],
                        "year": item.get("year", "")
                    })

        # ✅ Generate balance charts once
        balance_result = json.loads(generate_chart_insight_tool(json.dumps({
            "chart_type": "balance"
        })))
        if balance_result["status"] == "success":
            for item in balance_result["results"]:
                if "chart_path" in item and "insight" in item:
                    chart_data["balance"].append({
                        "path": item["chart_path"],
                        "insight": item["insight"],
                        "year": item.get("year", "")
                    })

        # ✅ Generate cashflow chart once
        cashflow_result = json.loads(generate_chart_insight_tool(json.dumps({
            "chart_type": "cashflow"
        })))
        if cashflow_result["status"] == "success":
            for item in cashflow_result["results"]:
                if "chart_path" in item and "insight" in item:
                    chart_data["cashflow"].append({
                        "path": item["chart_path"],
                        "insight": item["insight"],
                        "year": item.get("year", "")
                    })

        
        # PDF Generation
        filename = f"reports/financial_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        pdf = FPDF()
         # Add Unicode-compatible fonts (REPLACEMENT FOR ARIAL)
        pdf.add_font('DejaVu', '', 'DejaVuSans.ttf', uni=True)
        pdf.add_font('DejaVu', 'B', 'DejaVuSans-Bold.ttf', uni=True)
        pdf.add_font('DejaVu', 'I', 'DejaVuSans-Oblique.ttf', uni=True)
        pdf.set_font('DejaVu', '', 12)  # Set default font

        pdf.add_page()
        pdf.set_font("DejaVu", 'B', 14)
        pdf.cell(200, 10, txt="ANNUAL FINANCIAL REPORT", ln=True, align='C')
        pdf.ln(10)
        
        # Metrics section
        for year, year_data in metrics.items():
            pdf.set_font('DejaVu', 'B', 12)
            pdf.ln(10) 
            pdf.cell(200, 10, txt=f"Year: {year}", ln=True,align='C')
            pdf.set_font('DejaVu', '', 12)
            pdf.cell(200, 10, txt=f"Net Profit: PKR {year_data['net_profit']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Total Assets: PKR {year_data['total_assets']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Total Liabilities: PKR {year_data['total_liabilities']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Equity: PKR {year_data['equity']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Net Cash Flow: PKR {year_data['net_cash_flow']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Ending Cash Balance: PKR {year_data['ending_cash']:,}", ln=True)
            pdf.ln(5)
              # Add page break after report summary

        # Chart sections
        def insert_section(title, chart_items):
            
            pdf.set_font('DejaVu', 'B', 14)
            pdf.cell(200, 10, txt=title, ln=True, align='C')
            pdf.ln(10)

            for item in chart_items:
                if item["path"] and os.path.exists(item["path"]):

                    # Optional: Add chart year heading
                    pdf.set_font('DejaVu', 'B', 12)
                    chart_year = item.get("year", "")
                    pdf.cell(200, 10, txt=f"Chart for {chart_year}", ln=True)
                    pdf.ln(5)

                    # ✅ Insert the chart
                    pdf.image(item["path"], x=10, w=180, h=120)
                    pdf.ln(15)

                    # Insert insight
                    pdf.set_font('DejaVu', size=12)
                    pdf.multi_cell(0, 10, f"Insight: {item['insight']}")
                    pdf.ln(5)


        
        insert_section("INCOME STATEMENT CHARTS", chart_data["income"])
        pdf.add_page()
        insert_section("BALANCE SHEET CHARTS", chart_data["balance"])
        pdf.add_page()
        insert_section("CASH FLOW ANALYSIS", chart_data["cashflow"])
        
        # Summary section
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 14)
        pdf.cell(200, 10, txt="SUMMARY & ANALYSIS", ln=True, align='C')
        pdf.ln(10)
        write_summary_with_bold(pdf, summary_text)
        
        pdf.output(filename)
        return json.dumps({"status": "success", "message": "Report generated", "file": filename})
        
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})




# ---------- Tool: Send Financial Report ----------
def send_financial_report_tool(_=None):
    """Send report to all stakeholders from sheet using consistent data access"""
    try:
        # Get all data including stakeholders
        data_result = json.loads(fetch_financial_data_tool())
        if data_result["status"] != "success":
            return f"❌ Error fetching data: {data_result['message']}"
        
        stakeholder_records = data_result["data"].get("stakeholders", [])
        # Extract just the emails
        stakeholder_emails = [s["stakeholders_email"] for s in stakeholder_records 
                            if "stakeholders_email" in s]
        
        if not stakeholder_emails:
            return "❌ No valid stakeholder emails found"

        # Generate report
        report_result = json.loads(generate_financial_report_tool())
        if report_result["status"] != "success":
            return f"❌ Error generating report: {report_result['message']}"

        # Email setup
        report_path = report_result["file"]
        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = SENDER_EMAIL  # Primary recipient
        msg['Bcc'] = ", ".join(stakeholder_emails)  # Now joining strings
        msg['Subject'] = f"Annual Financial Report - {datetime.now().strftime('%Y')}"

        # Email content
        body = """Dear Stakeholder,
Please find attached the annual financial report for your review.
Best regards,
Finance Team"""
        msg.attach(MIMEText(body, 'plain'))

        # Attach report
        with open(report_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
        msg.attach(part)

        # Send email
        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()
        
        return f"✅ Report sent to {len(stakeholder_emails)} stakeholders successfully"
    except Exception as e:
        return f"❌ Error: {str(e)}"

# Procurement

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

def write_rich_text(pdf, text, font='DejaVu', size=12):
    """Enhanced version with proper subheading support"""
    # Step 1: Fix all numbered lists (1.\nText → 1. Text)
    text = re.sub(r'(\n|^)(\d+\.)\s*\n\s*', r'\1\2 ', text)
    
    # Step 2: Process text with bold/normal formatting
    pdf.set_font(font, '', size)
    parts = re.split(r'(\*\*.*?\*\*)', text)
    
    for part in parts:
        if not part.strip():
            continue
            
        if part.startswith('**') and part.endswith('**'):
            # Bold heading processing
            pdf.set_font(font, 'B', size)
            heading_text = part[2:-2]
            
            # Check if this is a main heading (e.g., "1. Introduction")
            if re.match(r'^\d+\.', heading_text):
                pdf.ln(5)  # Extra space before main headings
                pdf.set_font(font, 'B', size + 2)  # Slightly larger for main headings
            else:
                pdf.set_font(font, 'B', size)  # Regular bold for subheadings
                
            pdf.multi_cell(0, 8, heading_text)
            pdf.set_font(font, '', size)
        else:
            # Normal text processing with line-by-line handling
            for line in part.split('\n'):
                if line.strip():
                    # Detect sub-items (a, b, c or • items)
                    if re.match(r'^(\s*[•a-z]\.?|\s*\d+\.\d+)', line.lower()):
                        pdf.set_font(font, 'I', size - 1)  # Italic for sub-items
                    pdf.multi_cell(0, 8, line.strip())
                    pdf.set_font(font, '', size)  # Reset to normal



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
        
        # Add Unicode-compatible fonts (REPLACEMENT FOR ARIAL)
        pdf.add_font('DejaVu', '', 'DejaVuSans.ttf', uni=True)
        pdf.add_font('DejaVu', 'B', 'DejaVuSans-Bold.ttf', uni=True)
        pdf.set_font('DejaVu', '', 12)  # Set default font
        pdf.add_font('DejaVu', 'I', 'DejaVuSans-Oblique.ttf', uni=True)
        # ------ Cover Page ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 24)  # CHANGED: Arial → DejaVu
        pdf.cell(0, 20, 'Procurement Analytics Report', 0, 1, 'C')
        pdf.ln(10)
        pdf.set_font('DejaVu', '', 14)  # CHANGED
        pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}", 0, 1, 'C')
        pdf.ln(15)
        
        # ------ Table of Contents ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)  # CHANGED
        pdf.cell(0, 10, 'Table of Contents', 0, 1)
        pdf.set_font('DejaVu', '', 12)  # CHANGED
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
        pdf.set_font('DejaVu', 'B', 16)  # CHANGED
        pdf.cell(0, 10, '1. Executive Summary', 0, 1)
        pdf.ln(5)
        
        # Generate comprehensive summary using LLM
        summary_prompt = f"""Create a detailed executive summary for a procurement report covering these aspects:
        
        **Budget Status**:
        - Total POs processed: {len(GLOBAL_BUDGET_RESULTS)}
        - Within budget: {sum(1 for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'within')}
        - Exceeded budget: {sum(1 for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'exceeded')}
        - Largest budget overage: {max((x['cost'] - x['remaining_budget'] for x in GLOBAL_BUDGET_RESULTS if x['status'] == 'exceeded'), default=0):,.2f}
        
        **Inventory Status**:
        - Total items tracked: {len(GLOBAL_INVENTORY_RESULTS)}
        - Items with sufficient stock: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'sufficient')}
        - Items below reorder level: {sum(1 for x in GLOBAL_INVENTORY_RESULTS if x['inventory_status'] == 'low')}
        
        **Approval Status**:
        - Auto-approved POs: {len(GLOBAL_APPROVAL_RESULTS['auto_approved'])}
        - POs needing manual approval: {len(GLOBAL_APPROVAL_RESULTS['needs_approval'])}
        - Total value requiring approval: {sum(x['Qty']*x['Price'] for x in GLOBAL_APPROVAL_RESULTS['needs_approval']):,.2f}
        
        Provide 3-4 paragraphs highlighting key findings, risks, and opportunities in professional business language."""
        
        llm_summary = safe_chat_invoke(chat, summary_prompt)
        pdf.set_font('DejaVu', '', 12)
        write_rich_text(pdf, llm_summary.content)

        
        # ------ 2. Budget Analysis ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)
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

        budget_prompt = f"""
            You are generating a clean, professional budget analysis for a PDF report. 
            Follow these formatting rules exactly:

            1. Use bolded, numbered headings for each section using double asterisks (**), like:
            **1. Top 3 Categories by Spending**

            2. Under each heading, list items using simple bullet points (`-`), and include:
            - The category name
            - The amount spent (use $ and thousands separator)
            - A one-line insight that explains why the amount is significant, recurring, unexpected, or noteworthy

            Example format (structure only – content must come from data analysis):
            - Category Name – $123,456 (Used primarily for ongoing operations and annual subscriptions)

            3. Strictly avoid:
            - Asterisks (*) as bullets
            - Colons (:)
            - Sub-bullets like a, b, c or (1), (2)
            - Markdown like italics or inline bold – only use ** for section headings

            Now analyze this budget data:

            {budget_by_category.to_string()}

            Cover the following:
            1. Top 3 Categories by Spending
            2. Categories with Highest Budget Utilization
            3. Warning Signs for Potential Overspending
            4. Recommendations for Budget Adjustments
            """

      

        
        llm_budget = safe_chat_invoke(chat, budget_prompt)
        pdf.set_font('DejaVu', '', 12)
        write_rich_text(pdf, llm_budget.content)
        
        # ------ 3. Inventory Status ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)
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
        pdf.set_font('DejaVu', '', 12)
        write_rich_text(pdf,llm_inventory.content)
        
        # ------ 4. Approval Recommendations ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)
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
        pdf.set_font('DejaVu', '', 12)
        write_rich_text(pdf, llm_approval.content)
        
        # ------ 5. Vendor Analysis ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)
        pdf.cell(0, 10, '5. Vendor Performance', 0, 1)
        
        # Vendor performance analysis
        vendor_prompt = f"""Analyze vendor performance from this data. Use **bold** to highlight top vendors, spend amounts, and metrics:
        {pd.DataFrame(GLOBAL_PROCUREMENT_DATA['POs']).groupby('Vendor').agg({
            'Price': ['count', 'mean', 'sum'],
            'Qty': 'sum'
        }).to_string()}

        Provide:
        1. **Top performing vendors**
        2. **Vendors needing performance review**
        3. **Recommendations for vendor consolidation**
        4. **Suggested negotiation points**"""


        
        llm_vendor = safe_chat_invoke(chat, vendor_prompt)
        pdf.set_font('DejaVu', '', 12)
        write_rich_text(pdf,  llm_vendor.content)
        
        # ------ 6. Category Spending Trends ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)
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
        pdf.set_font('DejaVu', '', 12)
        write_rich_text(pdf,llm_trend.content)
        
        # ------ 7. Detailed PO Records ------
        pdf.add_page()
        pdf.set_font('DejaVu', 'B', 16)  # CHANGED
        pdf.cell(0, 10, '7. Detailed PO Records', 0, 1)
        pdf.set_font('DejaVu', 'B', 10)  # CHANGED
        
        # Table header
        pdf.cell(30, 10, 'Date', 1)
        pdf.cell(50, 10, 'Item', 1)
        pdf.cell(40, 10, 'Vendor', 1)
        pdf.cell(20, 10, 'Qty', 1)
        pdf.cell(25, 10, 'Price', 1)
        pdf.cell(25, 10, 'Total', 1, 1)
        
        # Table rows
        pdf.set_font('DejaVu', '', 8)
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
        pdf.set_font('DejaVu', 'I', 8) 
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
    

# ------------------ TOOL GROUPS ------------------

payroll_tools = [
    Tool(
        name="FetchPayrollData",
        func=fetch_payroll_data_tool,
        description="Fetch employee data and attendance records from Google Sheets. This MUST be done before CalculateSalaries."
    ),
    Tool(
        name="CalculateSalaries",
        func=calculate_salaries_tool,
        description="Calculate net salaries for employees based on their data. Only run this AFTER FetchPayrollData. This MUST be done before GeneratePayslips."
    ),
    Tool(
        name="GeneratePayslips",
        func=generate_payslips_tool,
        description="Generate payslip PDFs for each employee. Only run this AFTER CalculateSalaries. This MUST be done before SendPayslips."
    ),
    Tool(
        name="SendPayslips",
        func=send_payslips_tool,
        description="Send payslips to each employee via email. Only run this AFTER GeneratePayslips. This is the FINAL step."
    )
]



invoice_tools = [
    Tool(
        name="CreateInvoices",
        func=create_all_invoice_pdfs_tool,
        description="Create invoice PDFs for all customers. This MUST be done before SendInvoices."
    ),
    Tool(
        name="SendInvoices",
        func=send_all_invoices_tool,
        description="Send invoice emails to all customers. Only run this AFTER CreateInvoices has completed successfully. This MUST be done before RemindOverdueInvoices."
    ),
    Tool(
        name="RemindOverdueInvoices",
        func=remind_overdue_invoices_tool,
        description="Send reminders for overdue unpaid invoices. Only run this AFTER SendInvoices. This MUST be done before MarkPaidInvoices."
    ),
    Tool(
        name="MarkPaidInvoices",
        func=mark_paid_invoices_tool,
        description="Display the status of all invoices as paid or unpaid based on the sheet. Run only once after reminders."
    )
]


report_tools = [
    Tool(name="FetchFinancialData", func=fetch_financial_data_tool,
          description="Fetch financial data from Google Sheets."),
    Tool(name="CalculateFinancialMetrics", func=calculate_financial_metrics_tool,
         description="Calculate key financial metrics such as Net Profit, Total Assets, Liabilities, Equity, and Cash Flows."),
    Tool(name="GenerateChartInsight", func=generate_chart_insight_tool,
        description="Generate financial charts and their narrative insights. Input should be a JSON string with 'chart_type' (income/balance/cashflow) and 'year'."),
    Tool(name="GenerateFinancialSummary", func=generate_financial_summary_tool,
         description="Generate an LLM-based summary and comparative financial analysis between years. Only run this AFTER GenerateChartInsight. This MUST be done before GenerateFinancialReport."),
    Tool(name="GenerateFinancialReport", func=generate_financial_report_tool,
         description="Generate a PDF report using pre-generated charts and insights.After this, you MUST run SendFinancialReport to complete the task."),
    Tool(name="SendFinancialReport", func=send_financial_report_tool,
         description="Email the final PDF financial report to stakeholders. This MUST run AFTER GenerateFinancialReport. This is the FINAL step.")
]


# report_tools = [
#     Tool(name="FetchFinancialData", func=fetch_financial_data_tool,
#           description="Fetch financial data from Google Sheets."),
#     Tool(name="CalculateFinancialMetrics", func=calculate_financial_metrics_tool,
#          description="Calculate key financial metrics such as Net Profit, Total Assets, Liabilities, Equity, and Cash Flows."),
#     Tool(name="GenerateChartInsight", func=generate_chart_insight_tool,
#         description="Generate financial charts and their narrative insights. Input should be a JSON string with 'chart_type' (income/balance/cashflow) and 'year'."),
#     Tool(name="GenerateFinancialSummary", func=generate_financial_summary_tool,
#          description="Generate an LLM-based summary and comparative financial analysis between years. Only run this AFTER GenerateChartInsight. This MUST be done before GenerateFinancialReport."),
#     Tool(name="GenerateFinancialReport", func=generate_financial_report_tool,
#          description="Generate a PDF report using pre-generated charts and insights. Only run this AFTER GenerateFinancialSummary. This MUST be done before SendFinancialReport."),
#     Tool(name="SendFinancialReport", func=send_financial_report_tool,
#          description="Email the final PDF financial report to stakeholders. MUST run this AFTER GenerateFinancialReport.")
# ]

procurement_tools = [
     Tool(name="FetchProcurementData", func=fetch_procurement_data, description="Fetch financial data from Google Sheets. Must be done first."),
    Tool(name="BudgetProcessor", func=budget_processor, description="Process all POs for budget checks. Returns success when done. MUST be followed by BudgetSummary."),
    Tool(name="BudgetSummary", func=budget_summary, description="Get summary of budget status. After this, you MUST call InventoryProcessor next."),
    Tool(name="InventoryProcessor", func=inventory_processor, description="Process all inventory items. After this, you MUST call InventorySummary next."),
    Tool(name="InventorySummary", func=inventory_summary, description="Get summary of inventory status. Requires InventoryProcessor to run first."),
    Tool(name="ApprovalProcessor", func=approval_processor,description="Process all POs for approval status"),
    Tool(name="ApprovalSummary", func=approval_summary,description="Get approval overview summary"),
    Tool(name="Notifier", func=notifier_tool, description="Send approval notifications via email. After this, you MUST call GenerateProcurementReport next. Requires ApprovalProcessor to run first."),
    Tool(name="GenerateProcurementReport", func=report_generator, description="Create PDF report of recent purchase orders.After this, you MUST run SendProcurementReport to complete the task."),
    Tool(name="SendProcurementReport", func=send_report_tool, description="Email the generated procurement report.This MUST run AFTER GenerateProcurementReport. This is the FINAL step.")
]

# ------------------ AGENT INITIALIZATIONS ------------------

def init_agent(tools):
    return initialize_agent(
        tools=tools,
        llm=chat,
        agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
        verbose=True,
        handle_parsing_errors=True,
        return_only_outputs=True,
        max_iterations=30,
        early_stopping_method="force"
    )

payroll_agent = init_agent(payroll_tools)
invoice_agent = init_agent(invoice_tools)
report_agent = init_agent(report_tools)
procurement_agent = init_agent(procurement_tools)

# ------------------ ROUTER PROMPT ------------------

class RouterPromptTemplate(StringPromptTemplate):
    template: str
    input_variables: List[str] = ["input"]

    def format(self, **kwargs) -> str:
        return self.template.format(**kwargs)

router_prompt = RouterPromptTemplate(
    input_variables=["input"],
    template="""
You are an intelligent task router for financial automation. Analyze the task carefully and determine which agent should handle it.

AGENT SPECIALIZATIONS:
1. PAYROLL AGENT handles:
   - Employee salaries, payslips, attendance
   - Payroll processing, deductions, bonuses
   - Keywords: salary, payslip, employee, payroll

2. INVOICE AGENT handles:
   - Customer invoices, billing, payments
   - Payment reminders, overdue notices
   - Keywords: invoice, customer, billing, overdue

3. REPORT AGENT handles:
   - Financial summaries, statements, charts
   - Metrics like profit, equity, and cash flow
   - Keywords: report, summary, income, balance, cash flow, KPIs

4. PROCUREMENT AGENT handles:
   - Inventory, purchase orders, budget status
   - Approvals, vendor management, inventory notifications
   - Keywords: procurement, inventory, PO, approval, vendor

TASK ANALYSIS GUIDELINES:
- Focus on the core action and subject of the task
- Ignore incidental or vague terms that don’t affect routing
- When in doubt, prefer the more specific agent
- Do not make assumptions beyond what is stated

EXAMPLES:
Task: "Send late payment reminders to clients"
Analysis: Involves overdue invoices  
AGENT: INVOICE

Task: "Generate May salary slips"
Analysis: Concerns employee payslips  
AGENT: PAYROLL

Task: "Analyze cash flow and generate summary"
Analysis: Financial performance and metrics  
AGENT: REPORT

Task: "Check inventory status and notify vendor"
Analysis: Involves inventory and vendor alerts  
AGENT: PROCUREMENT

Now analyze the following task:
Task: "{input}"
Analysis: (briefly explain your reasoning)
AGENT:
"""
)

router_chain = LLMChain(llm=chat, prompt=router_prompt)

def route_task(task: str) -> str:
    response = router_chain.run(input=task).strip().lower()
    
    # Normalize the decision from LLM
    if "payroll" in response:
        return "payroll"
    elif "invoice" in response:
        return "invoice"
    elif "procurement" in response:
        return "procurement"
    elif "report" in response:
        return "report"
    
    # Fallback to keyword matching
    invoice_keywords = ["invoice", "customer", "billing", "overdue", "payment"]
    payroll_keywords = ["payroll", "salary", "payslip", "employee"]
    procurement_keywords = ["purchase order", "inventory", "vendor", "procurement", "stock"]
    report_keywords = ["report", "summary", "net profit", "income", "balance", "cash flow"]

    if any(word in task.lower() for word in invoice_keywords):
        return "invoice"
    elif any(word in task.lower() for word in payroll_keywords):
        return "payroll"
    elif any(word in task.lower() for word in procurement_keywords):
        return "procurement"
    elif any(word in task.lower() for word in report_keywords):
        return "report"
    
    return "both"  # Default fallback


import io
import contextlib

def execute_task(task: str):
    agent_type = route_task(task)
    print(f"🔀 Routed to: {agent_type} agent")

    # Setup buffer to capture printed logs
    log_buffer = io.StringIO()

    try:
        with contextlib.redirect_stdout(log_buffer):  # 👈 captures terminal output
            if agent_type == "payroll":
                result = payroll_agent.invoke({"input": task})
            elif agent_type == "invoice":
                result = invoice_agent.invoke({"input": task})
            elif agent_type == "report":
                result = report_agent.invoke({"input": task})
            elif agent_type == "procurement":
                result = procurement_agent.invoke({"input": task})
            else:
                return {"output": "❌ Unknown agent type."}

        # Gather the log content
        reasoning_trace = log_buffer.getvalue()

        # Ensure result is a dict
        if isinstance(result, dict):
            result["trace_log"] = reasoning_trace
            return result
        else:
            return {"output": result, "trace_log": reasoning_trace}

    except Exception as e:
        return {"output": f"❌ Error during execution: {str(e)}"}



# def execute_task(task: str):
#     agent_type = route_task(task)

#     if agent_type == "payroll":
#         print("\n🚀 Payroll Agent Processing...")
#         return payroll_agent.invoke({"input": task})
    
#     elif agent_type == "invoice":
#         print("\n🚀 Invoice Agent Processing...")
#         return invoice_agent.invoke({"input": task})
    
#     elif agent_type == "procurement":
#         print("\n🚀 Procurement Agent Processing...")
#         return procurement_agent.invoke({"input": task})
    
#     elif agent_type == "report":
#         print("\n🚀 Report Agent Processing...")
#         return report_agent.invoke({"input": task})
    
#     else:
#         return "❌ Unknown agent type. Cannot process task."



# ------------------ MAIN ------------------

if __name__ == "__main__":
    print("\nUnified Finance Router Multi-Agent Started")
    while True:
        task = input("\nEnter a task (or 'exit'): ")
        if task.lower() in ["exit", "quit"]:
            break
        try:
            print("\n🤖 Response:")
            result = execute_task(task)
            print(result)
        except Exception as e:
            print("\nError:", str(e))



# def run(self, task):
    #     destination = router_chain.run(task).strip().upper()
    #     agent = self.routes.get(destination, report_agent)
    #     return agent.run(task)


#         template="""
# You are a task routing agent.
# Route to one of the following agents:
# - PAYROLL
# - INVOICE
# - REPORT
# - PROCUREMENT

# Examples:
# - 'generate payslip' → PAYROLL
# - 'send invoice to client' → INVOICE
# - 'generate finance report' → REPORT
# - 'analyze inventory status' → PROCUREMENT

# Input: {input}
# Return only one: PAYROLL, INVOICE, REPORT, PROCUREMENT
# """
# )


#Generate all customer invoices, send them via email, remind overdue ones, and mark paid invoices in the sheet.
#Fetch payroll data, calculate employee salaries based on attendance, generate payslips, and email them to employees and HR.
#Generate and send the annual financial report by fetching data from Google Sheets, calculating key metrics, generating income, balance sheet, and cash flow charts with insights, creating an LLM-based summary, compiling a PDF.
#Fetch procurement data, process budgets and inventory, summarize results, handle approvals, send notifications, generate the report, and email it. Follow all steps in exact sequence. Never stop or return a final answer before the last step.



#Generate and send the annual financial report by fetching data from Google Sheets, calculating key metrics, generating income, balance sheet, and cash flow charts with insights, creating an LLM-based summary, compiling a PDF.
#Fetch data from Google Sheets, calculating key metrics, generate income, balance sheet, and cash flow charts with insights, create an LLM-based summary, compile a PDF, and email it to stakeholders."

#Generate the report by fetching data from Google Sheets, calculating key metrics, generating income, balance sheet, and cash flow charts with insights, creating an LLM-based summary, compiling a PDF, and emailing it to stakeholders."
