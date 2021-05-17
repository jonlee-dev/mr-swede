import os
import discord
import discord.utils
import pafy
from youtube_dl import YoutubeDL
import vlc
import re
import ffmpeg

from discord.ext import commands

bot = commands.Bot(command_prefix='$')

@bot.command()
async def join(ctx):
    channel = ctx.author.voice.channel
    await channel.connect()

@bot.command(aliases=['p'])
async def play(ctx, url):
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

    """
    print(title)
    print(get_sanitized_filename(title))
    filename = audio_stream.download(filepath='/home/jlee/Github/jlee/mr-swede/music')
    path_to_file = "/home/jlee/Github/jlee/mr-swede/music/{}.{}"
    
    # play the downloaded audio
    # voice.play(discord.FFmpegPCMAudio(os.path.abspath(path_to_file.format(title))))
    voice.play(discord.FFmpegPCMAudio(os.path.abspath(path_to_file.format(get_sanitized_filename(title), extension))))
    """

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
        print("nothing is playing")

@bot.command()
async def resume(ctx):
    voice = ctx.channel.guild.voice_client
    if voice.is_paused():
        voice.resume()
    else:
        print("nothing is paused")

@bot.command()
async def disconnect(ctx):
    voice = ctx.channel.guild.voice_client
    voice.disconnect()


bot.run('NTMwODIzMTQzMjQ2NTI4NTI5.XC-tkw.cBnGFbjA0Tn5b0aOuEkEUK3DtX8')

