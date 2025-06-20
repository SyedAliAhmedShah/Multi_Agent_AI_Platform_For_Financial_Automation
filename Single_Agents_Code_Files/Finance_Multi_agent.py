import os
import json
import base64
import gspread
import requests
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
from langchain.agents import Tool, initialize_agent, AgentType, AgentExecutor
from langchain.agents import AgentOutputParser
from langchain.schema import AgentAction, AgentFinish
from langchain.prompts import StringPromptTemplate
from langchain.chains import LLMChain
from langchain_groq import ChatGroq
from typing import List, Union, Dict, Optional, Type
from langchain.agents.agent import Agent, AgentOutputParser
from langchain.agents.agent_toolkits import create_conversational_retrieval_agent
from langchain.agents.agent_toolkits import create_retriever_tool
from langchain.agents.mrkl.base import ZeroShotAgent
from langchain.agents.agent_toolkits import create_conversational_retrieval_agent

# ---------- Load Environment ----------
load_dotenv()
CLIENT_SECRETS_FILE = 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.send']
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SPREADSHEET_NAME = "Invoices"
SENDER_EMAIL = "syedaliahmed171@gmail.com"  # change to yours

# ---------- Shared Data Structures ----------
class SharedData:
    def __init__(self):
        self.payroll_data = None
        self.invoice_data = None
        self.last_execution = {}

shared_data = SharedData()

# ---------- Shared Utility Functions ----------
def get_gsheet_client():
    scope = ["https://spreadsheets.google.com/feeds", 
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    return gspread.authorize(creds)

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

def get_invoice_data_from_sheet():
    try:
        client = get_gsheet_client()
        spreadsheet = client.open(SPREADSHEET_NAME)
        worksheet = spreadsheet.worksheet("Invoices")
        return worksheet.get_all_records()
    except Exception as e:
        print(f"Error fetching invoice data: {e}")
        return []

# ---------- Payroll Agent Tools ----------
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
            
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            pdf.cell(200, 10, txt="MONTHLY PAYSLIP", ln=True, align='C')
            pdf.ln(10)
            pdf.cell(200, 10, txt=f"Employee: {emp['name']} ({emp_id})", ln=True)
            pdf.cell(200, 10, txt=f"Department: {emp['department']}", ln=True)
            pdf.cell(200, 10, txt=f"Period: {datetime.now().strftime('%B %Y')}", ln=True)
            pdf.ln(10)
            pdf.cell(200, 10, txt=f"Base Salary: ${emp['base_salary']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Deductions: ${emp['deductions']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Bonus: ${emp['bonus']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Net Salary: ${emp['net_salary']:,}", ln=True, border=True)
            pdf.output(filename)

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

# ---------- Invoice Agent Tools ----------
def create_invoice_pdf(data, filename):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="INVOICE", ln=True, align='C')
    pdf.cell(200, 10, txt=f"Customer: {data['customer_name']}", ln=True)
    pdf.cell(200, 10, txt=f"Date: {data['date']}", ln=True)
    pdf.cell(200, 10, txt=f"Amount: ${data['amount']}", ln=True)
    pdf.cell(200, 10, txt=f"Due Date: {data['due_date']}", ln=True)
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
    """Mark paid invoices from sheet"""
    shared_data.invoice_data = get_invoice_data_from_sheet()
    updated = []
    for invoice in shared_data.invoice_data:
        if invoice['status'] == 'paid':
            updated.append(f"✅ {invoice['customer_name']}'s invoice is marked as paid.")
    return "\n".join(updated) if updated else "⚠️ No invoices marked as paid yet."

# ---------- LangChain Router Setup ----------
from langchain.agents.agent_types import AgentType

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

3. BOTH AGENTS should handle only when:
   - Explicitly asked to process all financial tasks
   - The task clearly involves both payroll and invoices

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

Task: "Prepare all financial documents for audit"
Analysis: Likely involves both payroll and invoices  
AGENT: BOTH

Now analyze the following task:

Task: "{input}"
Analysis: (briefly explain your reasoning)  
AGENT: 
""")




llm = ChatGroq(
    model="llama3-8b-8192",
    temperature=0,
    api_key=GROQ_API_KEY
)

router_chain = LLMChain(llm=llm, prompt=router_prompt)

def route_task(task: str) -> str:
    response = router_chain.run(input=task).strip().lower()
    
    # Smart extraction of agent type
    if "payroll" in response and "invoice" in response:
        return "both"
    elif "payroll" in response:
        return "payroll"
    elif "invoice" in response:
        return "invoice"
    
    # Fallback to keyword matching if LLM response is unclear
    invoice_keywords = ["invoice", "customer", "billing", "overdue", "payment"]
    payroll_keywords = ["payroll", "salary", "payslip", "employee"]
    
    if any(word in task.lower() for word in invoice_keywords):
        return "invoice"
    elif any(word in task.lower() for word in payroll_keywords):
        return "payroll"
    
    return "both"  # Default fallback

# ---------- Agent Initialization ----------
payroll_tools = [
    Tool(name="FetchPayrollData", func=fetch_payroll_data_tool,
         description="Fetches payroll data from Google Sheets"),
    Tool(name="CalculateSalaries", func=calculate_salaries_tool,
         description="Calculates net salaries for all employees"),
    Tool(name="GeneratePayslips", func=generate_payslips_tool,
         description="Generates PDF payslips for all employees"),
    Tool(name="SendPayslips", func=send_payslips_tool,
         description="Sends generated payslips to employees")
]

invoice_tools = [
    Tool(name="CreateInvoices", func=create_all_invoice_pdfs_tool,
         description="Create invoice PDFs for all customers"),
    Tool(name="SendInvoices", func=send_all_invoices_tool,
         description="Send invoice emails to all customers"),
    Tool(name="RemindOverdueInvoices", func=remind_overdue_invoices_tool,
         description="Send reminders for overdue unpaid invoices"),
    Tool(name="MarkPaidInvoices", func=mark_paid_invoices_tool,
         description="Mark paid invoices from sheet")
]

payroll_agent = initialize_agent(
    payroll_tools,
    llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=7
)

invoice_agent = initialize_agent(
    invoice_tools,
    llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=5
)

# ---------- Main Execution ----------
# Modify your execute_task function
def execute_task(task: str):
    agent_type = route_task(task)
    
    if agent_type == "payroll":
        print("\n🚀 Payroll Agent Processing...")
        return payroll_agent.run(task)
    elif agent_type == "invoice":
        print("\n🚀 Invoice Agent Processing...")
        return invoice_agent.run(task)
    else:
        print("\n🚀 Both Agents Processing...")
        payroll_result = payroll_agent.run(task)
        invoice_result = invoice_agent.run(task)
        return f"Payroll Results:\n{payroll_result}\n\nInvoice Results:\n{invoice_result}"

if __name__ == "__main__":
    print("Multi-Agent System Ready (LangChain Router Version)")
    while True:
        try:
            task = input("\nEnter your task (or 'quit' to exit): ")
            if task.lower() == 'quit':
                break
                
            result = execute_task(task)
            print("\nResult:\n", result)
            
        except Exception as e:
            print(f"Error: {str(e)}")

