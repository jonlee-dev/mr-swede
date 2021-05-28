import requests
import json
import traceback

headers = {"TRN-Api-Key": "a32ef3b1-384a-41c0-8e9c-5ff228d0af69"}

def stats(battletag: str):
    try:
        response = requests.get(f"https://public-api.tracker.gg/v2/overwatch/standard/profile/battlenet/{battletag}", headers=headers)
    except:
        return traceback.format_exc()

    return response
    
