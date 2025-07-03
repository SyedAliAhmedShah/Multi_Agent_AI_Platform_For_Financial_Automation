import os
import json
import base64
import gspread
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

# ---------- Load Environment ----------
load_dotenv()
CLIENT_SECRETS_FILE = 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/gmail.send']
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SPREADSHEET_NAME = "Invoices"
SENDER_EMAIL = "syedaliahmed171@gmail.com"

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

# ---------- Tool: Fetch Financial Data ----------

def fetch_financial_data_tool(_=None):
    """Fetch financial data (Income Statement, Balance Sheet, Cash Flow) from Google Sheets"""
    try:
        client = get_gsheet_client()
        sheet = client.open(SPREADSHEET_NAME)

        data = {
            "income_statement": sheet.worksheet("Income_Statement").get_all_records(),
            "balance_sheet": sheet.worksheet("Balance_Sheet").get_all_records(),
            "cash_flow": sheet.worksheet("Cash_Flow").get_all_records()
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





def generate_financial_summary_tool(_=None):
    try:
        calc_result = json.loads(calculate_financial_metrics_tool())
        if calc_result["status"] != "success":
            return {"status": "error", "message": "Unable to get financial metrics."}

        metrics = calc_result["data"]

        summary_input = "\n".join([
            f"In {year}, the net profit was PKR {data['net_profit']:,}, total assets were PKR {data['total_assets']:,}, "
            f"liabilities PKR {data['total_liabilities']:,}, equity PKR {data['equity']:,}, net cash flow PKR {data['net_cash_flow']:,} "
            f"and ending cash PKR {data['ending_cash']:,}."
            for year, data in metrics.items()
        ])

        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a financial analyst generating summaries."),
            ("human", "Given the following financial metrics:\n\n{summary_input}\n\nWrite a concise financial performance summary.")
        ])
        chain = prompt | llm

        response = chain.invoke({"summary_input": summary_input})
        return {"status": "success", "summary": response.content}

    except Exception as e:
        return {"status": "error", "message": str(e)}


def generate_financial_report_tool(_=None): 
    try:
        os.makedirs("reports", exist_ok=True)
        calc_result = json.loads(calculate_financial_metrics_tool())
        if calc_result["status"] != "success":
            return calc_result["message"]

        metrics = calc_result["data"]

        client = get_gsheet_client()
        years = ["2023 (PKR)", "2024 (PKR)", "2025 (PKR)"]

        chart_paths = {"income": [], "balance": [], "cashflow": []}

        # Income Statement Charts
        income_df = pd.DataFrame(client.open(SPREADSHEET_NAME).worksheet("Income_Statement").get_all_records())
        income_df.set_index("Metric", inplace=True)

        for year in years:
            income_df[year] = income_df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float)
            expense_items = {
                "COGS": income_df.at["COGS", year],
                "Operating Expenses": income_df.at["Operating Expenses", year],
                "Other Expenses": income_df.at["Other Expenses", year]
            }
            fig, ax = plt.subplots()
            ax.pie(expense_items.values(), labels=expense_items.keys(), autopct="%1.1f%%")
            ax.set_title(f"Income Statement {year}: Expense Breakdown")
            path = f"reports/income_{year.replace(' ', '_')}.png"
            plt.savefig(path)
            plt.close()
            chart_paths["income"].append(path)

        # Balance Sheet Charts
        balance_df = pd.DataFrame(client.open(SPREADSHEET_NAME).worksheet("Balance_Sheet").get_all_records())
        balance_df.set_index("Metric", inplace=True)

        for year in years:
            balance_df[year] = balance_df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float)
            assets = balance_df.loc[["Cash", "Inventory", "Equipment"], year].sum()
            liabilities = balance_df.loc[["Loans", "Accounts Payable"], year].sum()
            fig, ax = plt.subplots()
            ax.pie([assets, liabilities], labels=["Assets", "Liabilities"], autopct="%1.1f%%")
            ax.set_title(f"Balance Sheet {year}: Assets vs Liabilities")
            path = f"reports/balance_{year.replace(' ', '_')}.png"
            plt.savefig(path)
            plt.close()
            chart_paths["balance"].append(path)

        # Cash Flow Charts (Bar chart per year)
        cf_df = pd.DataFrame(client.open(SPREADSHEET_NAME).worksheet("Cash_Flow").get_all_records())
        cf_df.set_index("Category", inplace=True)

        cf_years = []
        for year in years:
            cf_df[year] = cf_df[year].replace({',': '', '': '0', None: '0'}, regex=True).astype(float)
            cf_parts = {
                "Operating": cf_df.at["Net Operating Cash Flow", year],
                "Investing": cf_df.at["Net Investing Cash Flow", year],
                "Financing": cf_df.at["Net Financing Cash Flow", year]
            }
            cf_years.append(cf_parts)

        # Bar chart comparison across years
        labels = ["Operating", "Investing", "Financing"]
        x = range(len(years))
        width = 0.2
        fig, ax = plt.subplots()
        for i, label in enumerate(labels):
            values = [cf[label] for cf in cf_years]
            ax.bar([val + i * width for val in x], values, width, label=label)
        ax.set_title("Cash Flow Comparison by Year")
        ax.set_xticks([val + width for val in x])
        ax.set_xticklabels([y.split()[0] for y in years])
        ax.legend()
        cash_flow_chart_path = "reports/cashflow_bar.png"
        plt.savefig(cash_flow_chart_path)
        plt.close()
        chart_paths["cashflow"].append(cash_flow_chart_path)

        # Generate Summary Points
                # Fetch LLM summary
        summary_result = generate_financial_summary_tool()
        summary_text = summary_result["summary"] if summary_result["status"] == "success" else "Summary not available."

        # PDF generation
        filename = f"reports/financial_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        pdf.cell(200, 10, txt="ANNUAL FINANCIAL REPORT", ln=True, align='C')
        pdf.ln(10)

        for year, year_data in metrics.items():
            pdf.cell(200, 10, txt=f"\nYear: {year}", ln=True)
            pdf.cell(200, 10, txt=f"Net Profit: PKR {year_data['net_profit']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Total Assets: PKR {year_data['total_assets']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Total Liabilities: PKR {year_data['total_liabilities']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Equity: PKR {year_data['equity']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Net Cash Flow: PKR {year_data['net_cash_flow']:,}", ln=True)
            pdf.cell(200, 10, txt=f"Ending Cash Balance: PKR {year_data['ending_cash']:,}", ln=True)
            pdf.ln(5)

        def insert_section(title, paths):
            pdf.add_page()
            pdf.cell(200, 10, txt=title, ln=True, align='C')
            pdf.ln(10)
            for chart in paths:
                pdf.image(chart, x=10, y=pdf.get_y(), w=180)
                pdf.ln(80)

        insert_section("INCOME STATEMENT CHARTS", chart_paths["income"])
        insert_section("BALANCE SHEET CHARTS", chart_paths["balance"])
        insert_section("CASH FLOW CHART", chart_paths["cashflow"])

        # Add Summary Section
        pdf.add_page()
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(200, 10, txt="SUMMARY & ANALYSIS", ln=True, align='C')
        pdf.ln(10)
        pdf.set_font("Arial", size=12)
        pdf.multi_cell(0, 10, summary_text)


        pdf.output(filename)
        return json.dumps({"status": "success", "message": "Report generated", "file": filename})

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ---------- Tool: Send Financial Report ----------
def send_financial_report_tool(_=None):
    try:
        report_result = json.loads(generate_financial_report_tool())
        if report_result["status"] != "success":
            return f"❌ Error generating report: {report_result['message']}"

        report_path = report_result["file"]
        creds = get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = "stakeholder@example.com"
        msg['Subject'] = f"Annual Financial Report - {datetime.now().strftime('%Y')}"

        body = "Attached is the financial report summarizing annual performance."
        msg.attach(MIMEText(body, 'plain'))

        with open(report_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
        msg.attach(part)

        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_msg}).execute()
        return "✅ Report emailed successfully."
    except Exception as e:
        return f"❌ Error sending report: {str(e)}"


# ---------- Tool Definitions ----------
# ---------- Tool Definitions ----------
tools = [
    Tool(name="FetchFinancialData", func=fetch_financial_data_tool, description="Fetch financial data from Google Sheets."),
    Tool(name="CalculateFinancialMetrics", func=calculate_financial_metrics_tool,description="Calculate key financial metrics such as Net Profit, Total Assets, Liabilities, Equity, and Cash Flows from the Income Statement, Balance Sheet, and Cash Flow Statement."),
    Tool(name="GenerateFinancialSummary", func=generate_financial_summary_tool, description="Generate an LLM-based summary and comparative financial analysis between years."),
    Tool(name="GenerateFinancialReport", func=generate_financial_report_tool, description="Generate a PDF report with charts."),
    Tool(name="SendFinancialReport", func=send_financial_report_tool, description="Email the financial report.")
]

# ---------- Agent Setup ----------
llm = ChatGroq(
    model="llama3-8b-8192",
    temperature=0,
    api_key=GROQ_API_KEY
)

agent = initialize_agent(
    tools=tools,
    llm=llm,
    agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=7,
)

# ---------- Main Execution ----------
if __name__ == "__main__":
    print("🚀 Starting Financial Report Generator Agent...")
    task = """Generate and send the annual financial report.
1. Fetch financial data
2. Calculate metrics
4. Generate a financial summary using LLM
5. Create PDF with charts and summary
6. Email report
"""

    result = agent.run(task)
    print("\nFinal Result:", result)
