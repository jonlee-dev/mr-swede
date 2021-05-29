### Setup

- install `python3`, `pip3`, and `pipenv`
- install dependencies by using `pipenv install` in root directory
- execute `pipenv shell` to activate a subshell for your python environment
- store discord token in environment before running in a `.env` file located in your root
  ```
  # example.env
  DISCORD_TOKEN=<token>
  ```
- (optional): store `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET` as well to access Blizzard-API related commands

#### Starting the Bot

Run `python3 bot.py`
