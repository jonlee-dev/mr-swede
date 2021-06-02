### Setup

- install `python3`, `pip3`
- (Optional: Activate your venv)
- run `python3 install -r requirements.txt" to install package dependencies
- store discord token in environment before running in a `.env` file located in your root
  ```
  # .env
  DISCORD_TOKEN=<token>
  ```
- (optional): store `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET` as well to access Blizzard-API related commands

#### Starting the Bot

Run `python3 bot.py`


#### Notes

- 210602 Opted for `venv` + `requirements.txt` instead of `pipenv` + `Pipfile` in favor of less conflict resolution headaches.
