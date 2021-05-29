import os
import discord
import discord.utils
import pafy
from youtube_dl import YoutubeDL
import re
import ffmpeg
import logging, logging.config
import openpyxl
from discord.ext import commands
from dotenv import load_dotenv

import overwatch
import blizzard

load_dotenv()

logging.config.fileConfig("./logging.conf", disable_existing_loggers=False)
logger = logging.getLogger()

bot = commands.Bot(command_prefix='$')

# Utility Commands
@bot.command()
async def join(ctx):
    channel = ctx.author.voice.channel
    await channel.connect()

# Music Commands
@bot.command(aliases=['p'])
async def play(ctx, url):
    logger.debug("in play")
    voice_channel = ctx.author.voice.channel
    voice = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    # logic to move voice client to the correct channel
    if voice is None:
        voice = await voice_channel.connect()
    elif voice.channel != voice_channel:
        voice.move_to(voice_channel)

    # fetching audio url
    video = pafy.new(url)
    audio_stream = video.getbestaudio(preftype='m4a')
    title = audio_stream.title
    extension = audio_stream.extension
    play_url = audio_stream.url

    # initialize VLC and VLC Player and Media
    #vlc_instance = vlc.Instance()
    #player = vlc_instance.media_player_new()
    #media = vlc_instance.media_new(play_url)
    #media.get_mrl()

    #player.set_media(media)
    #player.play()

    YDL_OPTIONS = {'format': 'bestaudio', 'noplaylist':'True'}
    with YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(play_url, download=False)
    play_url = info['url']

    FFMPEG_OPTS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}

    voice.play(discord.FFmpegPCMAudio(play_url, **FFMPEG_OPTS))
    voice.is_playing()

def get_sanitized_filename(filename):
    # replace / with _ etc.
    new_filename = re.sub("[\\/]+", '_', filename)
    return new_filename

@bot.command()
async def stop(ctx):
    voice = ctx.channel.guild.voice_client
    voice.stop()

@bot.command()
async def pause(ctx):
    voice = ctx.channel.guild.voice_client
    if voice.is_playing():
        voice.pause()
    else:
        logger.debug("nothing is playing")

@bot.command()
async def resume(ctx):
    voice = ctx.channel.guild.voice_client
    if voice.is_paused():
        voice.resume()
    else:
        logger.debug("nothing is paused")

@bot.command()
async def disconnect(ctx):
    voice = ctx.channel.guild.voice_client
    voice.disconnect()

# Overwatch Commands
@bot.command()
async def stats(ctx):
    try:
        workbook = openpyxl.load_workbook("accounts.xlsx")
        sheet = workbook.active
        ow_accounts = {}
        for row in sheet.iter_rows(min_row=2, min_col=1):
            account = row[0].value
            if account:
                logger.debug(account)
                ow_accounts[account] = {'name': account,
                        'pw': row[1].value,
                        'sr': row[2].value,
                        'sr_all_roles': row[3].value}
                logger.debug(ow_accounts[account]['name'])

                sr_tuple = overwatch.stats(account)
                if sr_tuple:
                    highest_sr = int(sr_tuple[0]) if sr_tuple[0] else 0
                    ow_accounts[account]['sr_all_roles'] = sr_tuple[1] if sr_tuple[1] else []
                    if ow_accounts[account]['sr'] and highest_sr > ow_accounts[account]['sr']:
                        ow_accounts[account]['sr'] = highest_sr

        logger.debug("saving workbook")
        workbook.save(filename="accounts.xlsx")
    except AttributeError:
        logger.debug("Account name is case-sensitive")
    except Exception as e:
        logger.debug(e.message())
    await ctx.send(ow_accounts)

@bot.command()
async def deck(ctx, code):
    token = os.environ.get('BLIZZARD_TOKEN')
    deck = blizzard.get_deck(code)
    await ctx.send(deck)

logger.debug("running bot")
logger.debug(os.environ.get('DISCORD_TOKEN'))
bot.run(os.environ.get('DISCORD_TOKEN'))

