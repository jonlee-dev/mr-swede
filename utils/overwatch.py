import requests
import json
import logging
import apis.google as google

log = logging.getLogger()

def update_sr(battletag: str):
    client = google.authenticate_google_sheets()
    spreadsheet = client.open_by_url(spreadsheet_url)
    worksheet = spreadsheet.get_worksheet(0)  # Assuming you want to update the first worksheet

    # Get all rows of data (excluding the header row)
    data = worksheet.get_all_values()[1:]

    for index, row in enumerate(data):
        # Assuming the columns are as follows: A - BattleTag, B - Tank SR, C - DPS SR, D - Support SR
        battletag = row[0]

        # API Reference: https://overfast-api.tekrop.fr/#tag/Players/operation/get_player_summary_players__player_id__summary_get
        endpoint = f"https://overfast-api.tekrop.fr/players/{battletag.replace('#','-')}/summary"
        response = requests.get(endpoint)
        if response.status_code == 200:
            player_data = response.json()
            tank_rank = player_data['competitive']['season']['tank']['division'] + " " + player_data['competitive']['season']['tank']['tier']
            damage_rank = player_data['competitive']['season']['damage']['division'] + " " + player_data['competitive']['season']['damage']['tier']
            support_rank = player_data['competitive']['season']['support']['division'] + " " + player_data['competitive']['season']['support']['tier']

        else:
            log.info(f'failed to update_sr for {battletag}. validate btag format and/or casing') 

    # Update the corresponding cells with the SR values
    row_index = account_index + 2  # Assuming the first row is for headers
    worksheet.update_cell(row_index, 2, tank_rank)  # Update tank SR in column B
    worksheet.update_cell(row_index, 3, damage_rank)  # Update DPS SR in column C
    worksheet.update_cell(row_index, 4, support_rank)  # Update support SR in column D

    log.info('update_sr completed')
