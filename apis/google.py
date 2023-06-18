import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Google Sheets credentials and spreadsheet details
google_credentials = "path/to/your/credentials.json"
spreadsheet_url = "https://docs.google.com/spreadsheets/d/your_spreadsheet_id/edit"

# Authenticate with Google Sheets API
def authenticate_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_name(google_credentials, scope)
    client = gspread.authorize(credentials)
    return client

# Update SR in Google Sheets
def update_sr_in_google_sheets(account_index, tank_sr, dps_sr, support_sr):
    client = authenticate_google_sheets()
    spreadsheet = client.open_by_url(spreadsheet_url)
    worksheet = spreadsheet.get_worksheet(0)  # Assuming you want to update the first worksheet

    # Update the corresponding cells with the SR values
    row_index = account_index + 2  # Assuming the first row is for headers
    worksheet.update_cell(row_index, 2, tank_sr)  # Update tank SR in column B
    worksheet.update_cell(row_index, 3, dps_sr)  # Update DPS SR in column C
    worksheet.update_cell(row_index, 4, support_sr)  # Update support SR in column D
