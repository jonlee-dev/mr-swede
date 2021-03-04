import os
import discord
import pafy
import vlc
import re

from discord.ext import commands

bot = commands.Bot(command_prefix='$')

@bot.command()
async def join(ctx):
    channel = ctx.author.voice.channel
    await channel.connect()

@bot.command()
async def play(ctx, url):
    voice_channel = ctx.author.voice.channel
    voice = ctx.channel.guild.voice_client
    if voice is None:
        voice = await voice_channel.connect()
    elif voice.channel != voice_channel:
        voice.move_to(voice_channel)
    video = pafy.new(url)
    audio_stream = video.getbestaudio(preftype='m4a')
    title = audio_stream.title
    extension = audio_stream.extension
    print(title)
    print(get_sanitized_filename(title))
    filename = audio_stream.download(filepath='/home/jlee/Github/jlee/mr-swede/music')
    path_to_file = "/home/jlee/Github/jlee/mr-swede/music/{}.{}"
    # voice.play(discord.FFmpegPCMAudio(os.path.abspath(path_to_file.format(title))))
    voice.play(discord.FFmpegPCMAudio(os.path.abspath(path_to_file.format(get_sanitized_filename(title), extension))))

def get_sanitized_filename(filename):
    # replace / with _ etc.
    new_filename = re.sub("[\\/]+", '_', filename)
    return new_filename
bot.run('NTMwODIzMTQzMjQ2NTI4NTI5.XC-tkw.cBnGFbjA0Tn5b0aOuEkEUK3DtX8')

