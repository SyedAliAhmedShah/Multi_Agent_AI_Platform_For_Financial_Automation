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
        input_data = json.loads(input_json)
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
    """Generate insight text for a chart"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a financial analyst generating short chart insights."),
        ("human", "Given the chart data:\n\nChart Type: {chart_type}\nYear: {year}\nPercentage Breakdown: {values}\n\nWrite a short insight (1-2 lines).")
    ])
    chain = prompt | llm
    return chain.invoke({
        "chart_type": chart_type,
        "year": year,
        "values": data
    }).content
    


def generate_financial_summary_tool(_=None):
    """Generate an LLM-based financial performance summary"""
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
            ("human", "Given the following financial metrics:\n\n{summary_input}\n\nWrite a concise financial performance summary.")
        ])
        chain = prompt | llm

        response = chain.invoke({"summary_input": summary_input})
        return json.dumps({
            "status": "success", 
            "summary": response.content
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
        pdf.add_page()
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(200, 10, txt="ANNUAL FINANCIAL REPORT", ln=True, align='C')
        pdf.ln(10)
        
        # Metrics section
        for year, year_data in metrics.items():
            pdf.set_font("Arial", 'B', 12)
            pdf.cell(200, 10, txt=f"\nYear: {year}", ln=True,align='C')
            pdf.set_font("Arial", '', 12)
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
            
            pdf.set_font("Arial", size=12)
            pdf.cell(200, 10, txt=title, ln=True, align='C')
            pdf.ln(10)

            for item in chart_items:
                if item["path"] and os.path.exists(item["path"]):

                    # Optional: Add chart year heading
                    pdf.set_font("Arial", 'B', 12)
                    chart_year = item.get("year", "")
                    pdf.cell(200, 10, txt=f"Chart for {chart_year}", ln=True)
                    pdf.ln(5)

                    # ✅ Insert the chart
                    pdf.image(item["path"], x=10, w=180, h=120)
                    pdf.ln(15)

                    # Insert insight
                    pdf.set_font("Arial", size=12)
                    pdf.multi_cell(0, 10, f"Insight: {item['insight']}")
                    pdf.ln(5)


        
        insert_section("INCOME STATEMENT CHARTS", chart_data["income"])
        pdf.add_page()
        insert_section("BALANCE SHEET CHARTS", chart_data["balance"])
        pdf.add_page()
        insert_section("CASH FLOW ANALYSIS", chart_data["cashflow"])
        
        # Summary section
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

# ---------- Tool Definitions ----------
# ---------- Updated Tool Definitions ----------
tools = [
    Tool(name="FetchFinancialData", func=fetch_financial_data_tool, description="Fetch financial data from Google Sheets."),
    Tool(name="CalculateFinancialMetrics", func=calculate_financial_metrics_tool,
         description="Calculate key financial metrics such as Net Profit, Total Assets, Liabilities, Equity, and Cash Flows."),
    Tool(name="GenerateChartInsight", func=generate_chart_insight_tool,
         description="Generate financial charts and their narrative insights. Input should be a JSON string with 'chart_type' (income/balance/cashflow) and 'year'."),
    Tool(name="GenerateFinancialSummary", func=generate_financial_summary_tool,
         description="Generate an LLM-based summary and comparative financial analysis between years."),
    Tool(name="GenerateFinancialReport", func=generate_financial_report_tool,
         description="Generate a PDF report using pre-generated charts and insights."),
    Tool(name="SendFinancialReport", func=send_financial_report_tool,
         description="Email the financial report.")
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
    return_only_outputs=True,
    max_iterations=10,
)

# ---------- Main Execution ----------
if __name__ == "__main__":
    print("🚀 Starting Financial Report Generator Agent...")
    task = """Generate and send the annual financial report:
    1. Fetch all financial data from Google Sheets
    2. Calculate key metrics (profit, assets, liabilities, cash flow) 
    3. Generate all required charts with insights:
    4. Create financial performance summary using LLM
    5. Build PDF with metrics, charts, and summary
    6. Email the final report to stakeholders
    """

    result = agent.run(task)
    print("\nFinal Result:", result)
