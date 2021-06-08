import logging
import requests
from requests.exceptions import HTTPError

log = logging.getLogger()

class API:
    def __init__(self, client_id, client_secret, token_url):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_info
        self.token_url = None
        self.session = None

    def get_token(self):
        # check if session is already cached and set params
        if not self.session:
            self.session = requests.Session()
        self.session.auth = (self.client_id, self.client_secret)
        self.session.params = {"grant_type": "client_credentials"}

        # check if refresh token exists
        if self.session.refresh_token:
            log.info(self.session.refresh_token)
            self.session.params["grant_type"] = "refresh_token"
            self.session.params["refresh_token"] = self.session.refresh_token

        # POST to auth
        response = None
        try:
            response = self.session.post(self.token_url, auth=self.session.auth, params=self.session.params)
            response.raise_for_status()
        except HTTPError as http_error:
            log.error(f"HTTP error occurred: {http_error}")
        except Exception as error:
            log.error(f"other error occurred in get_token: {error}")
        
        # handle response
        response_json = response.json()
        log.info(response_json)

        #  
        self.token_info = {'access_token': response_json.get('access_token'),
                'expires': response_json.get('expires'),
                'refresh_token': response_json.get('refresh_token')}
        return self.token_info['access-token']

