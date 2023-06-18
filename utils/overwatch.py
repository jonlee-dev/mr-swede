import requests
import json
import logging

log = logging.getLogger()

spreadsheet_url = "https://docs.google.com/spreadsheets/d/1x4WYPo3QApn7WQlyIBvH642MdOuBBecko9ZuJMWFbGY"

def update_sr_in_google_sheets(account_index, tank_rank, damage_rank, support_rank):
    row_index = account_index + 2  # Assuming the first row is for headers
    params = {
        "valueInputOption": "RAW"
    }
    data = {
        "range": f"Sheet1!B{row_index}:D{row_index}",
        "majorDimension": "ROWS",
        "values": [[tank_rank, damage_rank, support_rank]]
    }
    response = requests.put(f"{spreadsheet_url}/values/Sheet1!B{row_index}:D{row_index}", params=params, json=data)
    if response.status_code != 200:
        print(f"Failed to update SR for account at index {account_index}")


def fetch_player_rank(battletag: str):
    # API Reference: https://overfast-api.tekrop.fr/#tag/Players/operation/get_player_summary_players__player_id__summary_get
    endpoint = f"https://overfast-api.tekrop.fr/players/{battletag.replace('#','-')}/summary"
    response = requests.get(endpoint, params={"gamemode":"competitive", "platform":"pc"})
    if response.status_code == 200:
        player_data = response.json()
        tank_rank = player_data['competitive']['season']['tank']['division'] + " " + player_data['competitive']['season']['tank']['tier']
        damage_rank = player_data['competitive']['season']['damage']['division'] + " " + player_data['competitive']['season']['damage']['tier']
        support_rank = player_data['competitive']['season']['support']['division'] + " " + player_data['competitive']['season']['support']['tier']

    else:
        log.info(f'failed to fetch_player_rank for {battletag}. validate btag format and/or casing')
    return tank_rank, damage_rank, support_rank

def update_sr(battletag: str):
    response = requests.get(f"{spreadsheet_url}/gviz/tq?tqx=out:json")
    if response.status_code == 200:
        data = response.text

    
        #spreadsheet = client.open_by_url(spreadsheet_url)
        #worksheet = spreadsheet.get_worksheet(0)  # Assuming you want to update the first worksheet

        # Get all rows of data (excluding the header row) if no battletag was given
        # rework this to actually get all values
        rows_to_process = worksheet.get_all_values()[1:] if battletag is None else worksheet.find(battletag).row
    
        # init vars for rank data
        tank_rank = "err"
        damage_rank = "err"
        support_rank = "err"
        
        for index, row in enumerate(rows_to_process):
            # Assuming the columns are as follows: A - BattleTag, B - Tank SR, C - DPS SR, D - Support SR
            battletag = row[0]
            tank_rank, damage_rank, support_rank = fetch_player_rank(battletag)
            
            # Update the corresponding cells with the SR values
            row_index = account_index + 2  # Assuming the first row is for headers
            worksheet.update_cell(row_index, 2, tank_rank)  # Update tank SR in column B
            worksheet.update_cell(row_index, 3, damage_rank)  # Update DPS SR in column C
            worksheet.update_cell(row_index, 4, support_rank)  # Update support SR in column D
    
        log.info('update_sr completed')
        return f"{battletag}'s tank: {tank_rank}, dps: {damage_rank}, support: {support_rank}"
