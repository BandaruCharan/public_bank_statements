import streamlit as st
import pandas as pd
import requests
from nanonets import NANONETSOCR
from requests.exceptions import RequestException
from pdf2image import convert_from_path
from PIL import Image
import pytesseract
import re
import json
import tempfile
import subprocess

# Custom NANONETSOCR class to handle table extraction
class CustomNANONETSOCR(NANONETSOCR):
    def set_token(self, api_key):
        self.api_key = api_key
    
    def extract_tables(self, file_path, download=False, output_file_name=None):
        url = f'https://app.nanonets.com/api/v2/OCR/Model/5ff09935-9db3-475f-b8fc-e920684b0c80/LabelFile/'
        with open(file_path, 'rb') as file:
            data = {'file': file}
            response = requests.post(
                url,
                auth=requests.auth.HTTPBasicAuth(self.api_key, ''),
                files=data
            )
            if response.status_code == 200:
                return response.json()
            else:
                st.error(f"Error occurred: {response.status_code}")
                st.error(response.json())

# Function to extract tables from PDF/Image and return JSON object
def extract_tables(input_file_path):
    model = CustomNANONETSOCR()
    model.set_token('438a05b0-50ac-11ef-a2ab-debbf42d3711')
    try:
        tables_json = model.extract_tables(file_path=input_file_path)
        if 'result' in tables_json:
            return tables_json['result']
        else:
            st.error("Key 'result' not found in the response")
            return None
    except RequestException as e:
        st.error(f"Error occurred during table extraction: {e}")

# Function to extract text from images using pytesseract
def extract_text_with_pytesseract(images):
    image_content = []
    for image in images:
        if isinstance(image, Image.Image):
            text = pytesseract.image_to_string(image)
            image_content.append(text)
        else:
            raise TypeError("Each item in the list must be a PIL Image object.")
    return image_content

# Function to parse the extracted text and convert to JSON
def parse_bank_statement(text):
    bank_address_pattern = re.compile(r'(.*?)\nAccount Number:', re.DOTALL)
    account_number_pattern = re.compile(r'Account Number:\s+(\d{3}-\d{3}-\d{3}-\d{3})')
    statement_date_pattern = re.compile(r'Statement Date:\s+(\d{2}/\d{2}/\d{4})')
    period_covered_pattern = re.compile(r'Period Covered:\s+(\d{2}/\d{2}/\d{4}) to (\d{2}/\d{2}/\d{4})')
    customer_name_pattern = re.compile(r'\n(\w+\s\w+)\n')
    customer_address_pattern = re.compile(r'\n(\d+\s\w+\s\w+,\s\w+\s\d+)\n\n(\w+,\s\w+\s\d+)')
    branch_name_pattern = re.compile(r'\n<Branch Name>\n')
    financial_values_pattern = re.compile(r'\nPage 1 of 1\n(.*?)\n\n(.*?)\n\n(.*?)\n\n(.*?)\n\n(.*?)\n(.*?)\n', re.DOTALL)
    transaction_details_pattern = re.compile(r'Date Description\n(.*?)--- End of Transactions --\n\nCredit Debit(.*?)\nBalance\n', re.DOTALL)

    bank_address_match = bank_address_pattern.search(text)
    account_number_match = account_number_pattern.search(text)
    statement_date_match = statement_date_pattern.search(text)
    period_covered_match = period_covered_pattern.search(text)
    customer_name_match = customer_name_pattern.search(text)
    customer_address_match = customer_address_pattern.search(text)
    branch_name_match = branch_name_pattern.search(text)
    financial_values_match = financial_values_pattern.search(text)
    transaction_details_match = transaction_details_pattern.search(text)
    
    if financial_values_match:
        financial_values = [val.strip() for val in financial_values_match.groups()]
    else:
        financial_values = [None] * 6

    transactions = []
    if transaction_details_match:
        transaction_lines = transaction_details_match.group(1).strip().split('\n')
        credit_debit_text = transaction_details_match.group(2)
        balance_text = text.split('Balance\n', 1)[1]
        balance_lines = re.findall(r'\d{1,3}(?:,\d{3})*(?:\.\d{2})?', balance_text)
        segments = credit_debit_text.split('\n\n')

        credits = []
        debits = []

        for segment in segments:
            lines = [line for line in segment.split('\n') if line.strip()]

            if len(lines) == 1:
                credits.append('null')
                debits.append(lines[0])
            elif len(lines) > 1:
                debits.append(lines[0])
                credits.extend(['null'] * (len(debits) - len(credits)))
                credits.extend(lines[1:])
                debits.extend(['null'] * (len(lines) - 1))

        if len(credits) < len(debits):
            credits.extend(['null'] * (len(debits) - len(credits)))
        
        for i, line in enumerate(transaction_lines):
            if '|' in line:
                date_desc = line.split('|')
            elif '—' in line:
                date_desc = line.split('—')
            elif ' ' in line:
                date_desc = line.split(' ', 1)
            else:
                date_desc = [line.strip(), ""]

            date = date_desc[0].strip()
            description = date_desc[1].strip() if len(date_desc) > 1 else ""

            description = re.sub(r'[^\x00-\x7F]+', '', description)

            credit_value = credits[i] if credits[i] != 'null' else None
            debit_value = debits[i] if debits[i] != 'null' else None   
            balance = balance_lines[i].strip() if i < len(balance_lines) else "0.00"

            transactions.append({
                "date": date,
                "description": description,
                "credit": credit_value,
                "debit": debit_value,
                "balance": balance
            })

    json_extract = {
        "bank_address": bank_address_match.group(1).strip() if bank_address_match else None,
        "account_number": account_number_match.group(1) if account_number_match else None,
        "statement_date": statement_date_match.group(1) if statement_date_match else "mm/dd/yyyy",
        "period_covered": {
            "start_date": period_covered_match.group(1) if period_covered_match else "mm/dd/yyyy",
            "end_date": period_covered_match.group(2) if period_covered_match else "mm/dd/yyyy"
        },
        "customer_name": customer_name_match.group(1) if customer_name_match else None,
        "customer_address": f"{customer_address_match.group(1)}, {customer_address_match.group(2)}" if customer_address_match else None,
        "branch_name": branch_name_match.group().strip() if branch_name_match else None,
        "opening_balance": financial_values[0],
        "total_credit_amount": financial_values[1],
        "total_debit_amount": financial_values[2],
        "closing_balance": financial_values[3],
        "account_type": financial_values[4],
        "number_of_transactions": int(financial_values[5]) if financial_values[5] else 0,
        "transactions": transactions
    }

    return json_extract

# Function to extract tabular data from JSON and convert to DataFrame
def extract_tabular_data(json_data):
    dfs = []
    for table in json_data:
        table_data = table['prediction'][-1]['cells']
        max_col = max(cell['col'] for cell in table_data)
        df = pd.DataFrame(columns=range(1, max_col + 1))
        for cell in table_data:
            row, col = cell['row'], cell['col']
            text = cell['text']
            df.loc[row, col] = text
        dfs.append(df)
    return dfs

def main():
    st.title("Bank Transactions Extraction")
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #f0f0f0; /* Set background color */
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    
    st.header("Upload Bank Statement PDF or Image file")
    uploaded_file = st.file_uploader("Choose a file", type=["pdf", "jpg", "jpeg", "png"])

    if st.button("Extract"):
        if uploaded_file is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.read())
                tmp_file_path = tmp_file.name

            tables_json = extract_tables(tmp_file_path)
            if tables_json:
                st.header("Transaction Details:")
                tabular_data = extract_tabular_data(tables_json)
                for idx, df in enumerate(tabular_data, start=1):
                    st.write(df)

                    
    if st.button("Extract as JSON"):
    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(uploaded_file.read())
            tmp_file_path = tmp_file.name

        # Debugging step to check if `pdfinfo` is available
        try:
            result = subprocess.run(["which", "pdfinfo"], capture_output=True, text=True)
            pdfinfo_path = result.stdout.strip()
            if pdfinfo_path:
                st.write(f"`pdfinfo` located at: {pdfinfo_path}")
                POPPLER_PATH = pdfinfo_path  # Use the located path
            else:
                st.error("`pdfinfo` is not found. Ensure `poppler-utils` is installed correctly.")
                return  # Exit this section if `pdfinfo` is not available
        except Exception as e:
            st.error(f"Error locating `pdfinfo`: {e}")
            return  # Exit this section if an error occurs

        # Convert PDF to images using pdf2image
        try:
            images = convert_from_path(tmp_file_path, poppler_path=POPPLER_PATH)
            text_with_pytesseract = extract_text_with_pytesseract(images)
            text = " ".join(text_with_pytesseract)
            json_output = parse_bank_statement(text)
            json_str = json.dumps(json_output, indent=4)

            st.header("Extracted JSON")
            st.json(json_output)

            # Provide a download button for the JSON file
            st.download_button(
                label="Download JSON",
                data=json_str,
                file_name="bank_statement.json",
                mime="application/json"
            )
        except Exception as e:
            st.error(f"Error converting PDF to images or processing: {e}")


if __name__ == "__main__":
    main()
