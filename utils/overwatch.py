import requests
import json
import logging
from bs4 import BeautifulSoup

log = logging.getLogger()

def stats(battletag: str):
    try:
        endpoint = f"https://playoverwatch.com/en-us/career/pc/{battletag.replace('#','-')}" 
        page = requests.get(endpoint)
        soup = BeautifulSoup(page.content, 'html.parser')
        sr_elems = soup.find_all('div', class_='competitive-rank-level')
        sr_all_roles = [sr_elem.text.strip() for sr_elem in sr_elems]
        return (sr_all_roles[0], sr_all_roles[1:])
    except Exception as e:
        log.info("error occurred in overwatch stats")
