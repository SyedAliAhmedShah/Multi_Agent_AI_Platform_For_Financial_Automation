# import os
# import json
# import base64
# import gspread
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

# load_dotenv()
# CLIENT_SECRETS_FILE = 'client_secret.json'
# SCOPES = ['https://www.googleapis.com/auth/gmail.send']
# GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# SPREADSHEET_NAME = "Invoices"
# SENDER_EMAIL = "syedaliahmed171@gmail.com"

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

# # ---------- Core Functions ----------

# def fetch_procurement_data(_=None):
#     try:
#         client = get_gsheet_client()
#         sheet = client.open(SPREADSHEET_NAME)

#         data = {
#             "POs": sheet.worksheet("PO's").get_all_records(),
#             "Budgets": sheet.worksheet("Budgets").get_all_records(),
#             "Spend": sheet.worksheet("Spend").get_all_records(),
#             "Inventory": sheet.worksheet("Inventory").get_all_records(),
#         }
#         return json.dumps({"status": "success", "data": data})
#     except Exception as e:
#         return json.dumps({"status": "error", "message": str(e)})


# def budget_checker(_=None) -> str:
#     try:
#         # Step 1: Fetch all data
#         response = fetch_procurement_data()
#         parsed = json.loads(response)

#         if parsed["status"] != "success":
#             return json.dumps({"status": "error", "message": parsed["message"]})

#         data = parsed["data"]
#         pos_list = data["POs"]
#         budgets = data["Budgets"]
#         spend = data["Spend"]

#         # Step 2: Build budget & spend map
#         budget_map = {b["Category"]: float(b["Budget Amount"]) for b in budgets}
#         spend_map = {}
#         for s in spend:
#             cat = s["Category"]
#             amt = float(s.get("Amount Spent", 0))
#             spend_map[cat] = spend_map.get(cat, 0) + amt

#         # Step 3: Loop over each PO and evaluate
#         results = []
#         for po in pos_list:
#             category = po["Category"]
#             total_cost = float(po["Qty"]) * float(po["Price"])
#             remaining_budget = budget_map.get(category, 0) - spend_map.get(category, 0)
#             status = "within" if total_cost <= remaining_budget else "exceeded"
            
#             # Instead of returning full PO, only return minimal useful info
#             results.append({
#                 "Item": po.get("Item"),
#                 "Category": category,
#                 "Qty": po.get("Qty"),
#                 "Price": po.get("Price"),
#                 "status": status,
#                 "cost": total_cost,
#                 "remaining_budget": remaining_budget,
#             })

#         return json.dumps({"status": "success", "results": results})

#     except Exception as e:
#         return json.dumps({"status": "error", "message": str(e)})



# def inventory_checker(input_str: str) -> str:
#     try:
#         data = json.loads(input_str)  # ✅ This converts JSON string to dict

#         item = data["Item"]
#         category = data["Category"]
#         stock = int(data["Current Stock"])
#         reorder_level = int(data["Reorder Level"])
#         supplier = data["Supplier"]

#         status = "sufficient" if stock > reorder_level else "low"

#         return json.dumps({
#             "status": "success",
#             "item": item,
#             "category": category,
#             "stock": stock,
#             "reorder_level": reorder_level,
#             "inventory_status": status,
#             "supplier": supplier
#         })

#     except Exception as e:
#         return json.dumps({"status": "error", "message": str(e)})


# def approval_agent_tool(_=None) -> str:
#     # Your existing logic here

#     try:
#         # Step 1: Fetch data
#         response = fetch_procurement_data()
#         parsed = json.loads(response)

#         if parsed["status"] != "success":
#             return json.dumps({"status": "error", "message": parsed["message"]})

#         data = parsed["data"]
#         pos = data["POs"]

#         # Step 2: Get budget results
#         budget_result = json.loads(budget_checker())
#         if budget_result["status"] != "success":
#             return json.dumps({"status": "error", "message": "Budget check failed"})

#         results = []

#         for po in pos:
#             # Find matching result in budget check
#             matched_result = next(
#                 (r for r in budget_result["results"]
#                  if r["Item"] == po["Item"]
#                  and int(r["Qty"]) == int(po["Qty"])
#                  and float(r["Price"]) == float(po["Price"])
#                  and r["Category"] == po["Category"]),
#                 None
#             )

#             if not matched_result:
#                 results.append({"PO": po, "status": "error", "message": "No match in budget results"})
#                 continue

#             if matched_result["status"] == "within":
#                 results.append({"PO": po, "status": "auto-approved", "suggestion": None})
#             else:
#                 # Use LLM to get a suggestion
#                 prompt = ChatPromptTemplate.from_template(
#                     "A PO for {qty} units of {item} from {vendor} exceeds budget. Suggest a solution."
#                 )
#                 chain = prompt | chat
#                 suggestion = chain.invoke({
#                     "qty": po["Qty"],
#                     "item": po["Item"],
#                     "vendor": po["Vendor"]
#                 })

#                 results.append({
#                     "PO": po,
#                     "status": "needs-approval",
#                     "suggestion": suggestion.content
#                 })

#         return json.dumps({"status": "success", "results": results})

#     except Exception as e:
#         return json.dumps({"status": "error", "message": str(e)})


# def notifier_tool(po_json):
#     po = json.loads(po_json)
#     approval = json.loads(approval_agent_tool(po_json))
#     status, suggestion = approval["status"], approval["suggestion"]

#     subject = f"PO Update: {po['item']} ({status})"
#     if status == "auto-approved":
#         body = f"Your purchase order for {po['qty']} x {po['item']} at PKR {po['price']} each has been auto-approved."
#     else:
#         body = f"The PO for {po['item']} exceeds budget. Suggested Action: {suggestion}"

#     send_email(subject, body, "manager@example.com")
#     return f"Notification sent for {po['item']} ({status})"

# def report_generator(_=None):
#     try:
#         pos = fetch_procurement_data()
#         filename = f"reports/procurement_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
#         pdf = FPDF()
#         pdf.add_page()
#         pdf.set_font("Arial", size=12)
#         pdf.cell(200, 10, txt="Weekly Procurement Report", ln=1, align="C")
#         pdf.ln(10)

#         for po in pos[-5:]:
#             pdf.cell(200, 10, txt=f"Date: {po.get('date', '-') } | Item: {po['item']} | Qty: {po['qty']} | Vendor: {po['vendor']} | Price: {po['price']} | Category: {po['category']}", ln=1)

#         pdf.output(filename)
#         return json.dumps({"status": "success", "file": filename})
#     except Exception as e:
#         return json.dumps({"status": "error", "message": str(e)})

# def send_report_tool(_=None):
#     try:
#         result = json.loads(report_generator())
#         if result["status"] != "success":
#             return f"Error generating report"

#         report_path = result["file"]
#         creds = get_gmail_credentials()
#         service = build('gmail', 'v1', credentials=creds)

#         msg = MIMEMultipart()
#         msg['From'] = SENDER_EMAIL
#         msg['To'] = SENDER_EMAIL
#         msg['Bcc'] = "procurement-team@example.com"
#         msg['Subject'] = f"Weekly Procurement Report - {datetime.now().strftime('%Y-%m-%d')}"

#         body = "Dear Team,\n\nPlease find attached the latest procurement report.\n\nRegards,\nProcurement Bot"
#         msg.attach(MIMEText(body, 'plain'))

#         with open(report_path, "rb") as f:
#             part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
#         part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
#         msg.attach(part)

#         raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
#         service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()

#         return f"Report emailed successfully"
#     except Exception as e:
#         return f"Error sending report: {str(e)}"

# # ---------- Tool Definitions ----------

# tools = [
#     Tool(name="FetchProcurementData", func=fetch_procurement_data, description="Fetch financial data from Google Sheets."),
#     Tool(name="BudgetChecker",func=budget_checker,description="Check if PO is within budget. Input: JSON with qty, price, category."),
#     Tool(name="InventoryChecker",func=inventory_checker,description="Check inventory status for an item. Input: JSON with item."),
#     Tool(name="ApprovalAgent", func=approval_agent_tool, description="Decide approval. Input: PO JSON."),
#     Tool(name="Notifier", func=notifier_tool, description="Send email to manager about PO status. Input: PO JSON."),
#     Tool(name="GenerateProcurementReport", func=report_generator, description="Create a PDF report of recent purchase orders."),
#     Tool(name="SendProcurementReport", func=send_report_tool, description="Email the generated procurement report.")
# ]

# # ---------- Agent Setup ----------

# chat = ChatGroq(
#     model="llama3-8b-8192",
#     temperature=0,
#     api_key=os.getenv("GROQ_API_KEY")
# )

# agent = initialize_agent(
#     tools=tools,
#     llm=chat,
#     agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
#     verbose=True,
#     handle_parsing_errors=True,
#     return_only_outputs=True,
#     max_iterations=10
# )

# # ---------- Main Execution ----------

# if __name__ == "__main__":
#     print("Starting Procurement Monitor Agent...")
#     task = """Process all pending purchase orders:
#     1. Fetch all procurement data from Google Sheets
#     2. Track all new POs
#     3. For each PO:
#         a. Check if budget is available
#         b. Check inventory levels
#         c. Approve or escalate via LLM
#         d. Notify manager
#     4. Generate PDF report of recent POs
#     5. Email report to procurement team"""
#     result = agent.run(task)
#     print("\nFinal Result:", result)
# ✅ UPDATED VERSION WITH BATCH LOGIC INTEGRATED FOR ALL TOOLS
# (original structure preserved; comments marked where changes are applied)



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

load_dotenv()
CLIENT_SECRETS_FILE = 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.send']
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SPREADSHEET_NAME = "Invoices"
SENDER_EMAIL = "syedaliahmed171@gmail.com"

BATCH_SIZE = 5  # ✅ Used in all batch operations

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

# ---------- Core Functions ----------
def fetch_procurement_data(_=None):
    try:
        client = get_gsheet_client()
        sheet = client.open(SPREADSHEET_NAME)

        data = {
            "POs": sheet.worksheet("PO's").get_all_records(),
            "Budgets": sheet.worksheet("Budgets").get_all_records(),
            "Spend": sheet.worksheet("Spend").get_all_records(),
            "Inventory": sheet.worksheet("Inventory").get_all_records(),
        }
        return json.dumps({"status": "success", "data": data})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def budget_checker(_=None) -> str:
    try:
        response = fetch_procurement_data()
        parsed = json.loads(response)
        if parsed["status"] != "success":
            return json.dumps({"status": "error", "message": parsed["message"]})

        data = parsed["data"]
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
        return json.dumps({"status": "success", "results": results})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def inventory_checker(_=None):
    try:
        response = fetch_procurement_data()
        parsed = json.loads(response)
        if parsed["status"] != "success":
            return json.dumps({"status": "error", "message": parsed["message"]})

        inventory = parsed["data"]["Inventory"]
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
        return json.dumps({"status": "success", "results": results})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def approval_agent_tool(_=None):
    try:
        response = fetch_procurement_data()
        parsed = json.loads(response)
        if parsed["status"] != "success":
            return json.dumps({"status": "error", "message": parsed["message"]})

        data = parsed["data"]
        pos = data["POs"]

        budget_result = json.loads(budget_checker())
        if budget_result["status"] != "success":
            return json.dumps({"status": "error", "message": "Budget check failed"})

        inventory_result = json.loads(inventory_checker())
        if inventory_result["status"] != "success":
            return json.dumps({"status": "error", "message": "Inventory check failed"})

        budget_map = {
            (r["Item"], r["Category"], str(r["Qty"]), str(r["Price"])): r
            for r in budget_result["results"]
        }
        inventory_map = {
            (r["item"], r["category"]): r
            for r in inventory_result["results"]
        }

        results = []
        for batch in batch_items(pos):
            for po in batch:
                key_budget = (po["Item"], po["Category"], str(po["Qty"]), str(po["Price"]))
                key_inventory = (po["Item"], po["Category"])
                b_result = budget_map.get(key_budget)
                i_result = inventory_map.get(key_inventory)

                if not b_result:
                    results.append({"PO": po, "status": "error", "message": "Missing in budget check"})
                    continue
                if not i_result:
                    results.append({"PO": po, "status": "error", "message": "Missing in inventory check"})
                    continue

                if b_result["status"] == "within" and i_result["inventory_status"] == "sufficient":
                    results.append({"PO": po, "status": "auto-approved"})
                else:
                    reason = []
                    if b_result["status"] != "within":
                        reason.append("Budget Exceeded")
                    if i_result["inventory_status"] != "sufficient":
                        reason.append("Inventory Low")
                    results.append({"PO": po, "status": "needs-review", "reason": ", ".join(reason)})

        return json.dumps({"status": "success", "results": results})

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def notifier_tool(_=None):
    try:
        approval_results = json.loads(approval_agent_tool())
        if approval_results["status"] != "success":
            return "Approval process failed"

        for batch in batch_items(approval_results["results"]):
            for res in batch:
                po = res["PO"]
                status = res["status"]
                suggestion = res.get("suggestion", "N/A")

                subject = f"PO Update: {po['Item']} ({status})"
                if status == "auto-approved":
                    body = f"Your PO for {po['Qty']} x {po['Item']} at PKR {po['Price']} is auto-approved."
                else:
                    body = f"The PO for {po['Item']} exceeds budget. Suggested Action: {suggestion}"

                send_email(subject, body, "manager@example.com")
        return "All notifications sent"
    except Exception as e:
        return f"Error sending notifications: {str(e)}"


def report_generator(_=None):
    try:
        parsed = json.loads(fetch_procurement_data())
        pos = parsed["data"]["POs"]

        filename = f"reports/procurement_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(200, 10, txt="Weekly Procurement Report", ln=1, align="C")
        pdf.ln(10)

        for po in pos[-50:]:  # ✅ recent 50 for more detail
            pdf.cell(200, 10,
                     txt=f"Date: {po.get('Date', '-') } | Item: {po['Item']} | Qty: {po['Qty']} | Vendor: {po['Vendor']} | Price: {po['Price']} | Category: {po['Category']}",
                     ln=1)

        pdf.output(filename)
        return json.dumps({"status": "success", "file": filename})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def send_report_tool(_=None):
    try:
        result = json.loads(report_generator())
        if result["status"] != "success":
            return f"Error generating report"

        report_path = result["file"]
        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = SENDER_EMAIL
        msg['Bcc'] = "procurement-team@example.com"
        msg['Subject'] = f"Weekly Procurement Report - {datetime.now().strftime('%Y-%m-%d')}"

        body = "Dear Team,\n\nPlease find attached the latest procurement report.\n\nRegards,\nProcurement Bot"
        msg.attach(MIMEText(body, 'plain'))

        with open(report_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
        msg.attach(part)

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()

        return f"Report emailed successfully"
    except Exception as e:
        return f"Error sending report: {str(e)}"

# ---------- Tool Definitions ----------

tools = [
    Tool(name="FetchProcurementData", func=fetch_procurement_data, description="Fetch financial data from Google Sheets."),
    Tool(name="BudgetChecker",func=budget_checker,description="Check if PO is within budget."),
    Tool(name="InventoryChecker",func=inventory_checker,description="Check inventory status for items."),
    Tool(name="ApprovalAgent", func=approval_agent_tool, description="Decide approval for all POs."),
    Tool(name="Notifier", func=notifier_tool, description="Send email to manager about PO status."),
    Tool(name="GenerateProcurementReport", func=report_generator, description="Create PDF report of recent purchase orders."),
    Tool(name="SendProcurementReport", func=send_report_tool, description="Email the generated procurement report.")
]

# ---------- Agent Setup ----------

chat = ChatGroq(
    model="llama3-8b-8192",
    temperature=0,
    api_key=os.getenv("GROQ_API_KEY")
)

agent = initialize_agent(
    tools=tools,
    llm=chat,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
    return_only_outputs=True,
    max_iterations=10
)

if __name__ == "__main__":
    print("Starting Procurement Monitor Agent...")
    task = """Process all pending purchase orders:
    1. Fetch all procurement data from Google Sheets
    2. Track all new POs
    3. For each PO:
        a. Check if budget is available
        b. Check inventory levels
        c. Approve or escalate via LLM
        d. Notify manager
    4. Generate PDF report of recent POs
    5. Email report to procurement team"""
    result = agent.run(task)
    print("\nFinal Result:", result)


    
