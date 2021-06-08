import os
import requests
from requests.exceptions import HTTPError
import logging
from dotenv import load_dotenv
import json

load_dotenv()
logger = logging.getLogger()

client_id = os.environ.get('TWITCH_CLIENT_ID')
client_secret = os.environ.get('TWITCH_CLIENT_SECRET')

class Twitch:
    def __init__(self):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = 'https://id.twitch.tv/oauth2/token'
        self.session = None

    def get_token(self):
        if not self.session:
            self.session = requests.Session()
        self.session.auth = (self.client_id, self.client_secret)
        self.session.params = {'grant_type': 'client_credentials'}

        response = None
        try:
            response = self.session.post(self.token_url, auth=self.session.auth, params=self.session.params)
            response.raise_for_status()
        except HTTPError as http_error:
            logger.error(f'HTTP error occurred: {http_error}')
        except Exception as error:
            logger.error(f'other error occurred in get_token: {error}')
        response_json = response.json()
        logger.debug(response_json)
        return response_json['access_token']
