import os
import requests
from requests.exceptions import HTTPError
import logging

log = logging.getLogger()

def get_deck(code: str, token: str):
    headers = {'Authorization': f'Bearer {token}'}
    params = {'locale': 'en_US', 'code': code, 'access_token': token}
    endpoint = "https://us.api.blizzard.com/hearthstone/deck"

    response = None
    try:
        response = requests.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
    except HTTPError as http_error:
        log.error(f"HTTP error occurred: {http_error}")
    except Exception as error:
        log.error(f"other error occurred in get_deck: {error}")
    deck = response.json()
    cards = {}
    for card in deck['cards']:
        log.info(card)
        cards[card['id']] = {'name': card['name']}
    return cards
