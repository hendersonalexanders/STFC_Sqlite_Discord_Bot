#!/usr/bin/env python3
#
# FILENAME: vision-test.py
# CREATED:  August 17, 2019
# AUTHOR:   buerge3
#
# A discord bot for uploading STFC roster image data to a database
# Usage: "python3 ./vision-test.py
import discord
from discord.ext import commands
from discord import Status

import sqlite3
from sqlite3 import Error

from PIL import Image
import pytesseract
import math
from spellchecker import SpellChecker

import aiohttp
import aiofiles

import datetime
import re

#import traceback
import logging

# MODIFIABLE PARAMETERS
db_name = "LVE.db"
x_percent = 14
bot = commands.Bot(command_prefix='!')

# -----------------------------------------------------------------------------
#                        DATABASE CONNECTION SCRIPT
# -----------------------------------------------------------------------------
def create_connection(db_file):
    """ create a database connection to the SQLite database
        specified by the db_file
    :param db_file: database file
    :return: Connection object or None
    """
    try:
        conn = sqlite3.connect(db_file)
        logging.info("connected to " + db_file);
        return conn
    except Error as e:
        logging.error(e, exc_info=True)
 
    return None

conn = create_connection(db_name)


# -----------------------------------------------------------------------------
#                                    FUNCTIONS
# -----------------------------------------------------------------------------
# add_name_to_alias
# @param name, the name to add to the alias table
def add_name_to_alias(name):
    cur = conn.cursor()
    sql = '''SELECT value FROM __state WHERE name="key"'''
    logging.debug("SQL: " + sql)
    cur.execute(sql)
    key = cur.fetchone()[0]
    sql = '''INSERT INTO alias (key, name) VALUES ("{}", "{}")'''.format(key, name)
    logging.debug("SQL: " + sql)
    cur.execute(sql)
    new_key = int(key) + 1
    sql = '''UPDATE __state SET value={} WHERE name="key"'''.format(new_key)
    logging.debug("SQL: " + sql)
    cur.execute(sql)
    return key

# IsImage
# @return true if the first command-line argument is an image
def isImage(context, num):
    pic_ext = ['.jpg','.png','.jpeg']
    for ext in pic_ext:
        if context.message.attachments[num].filename.endswith(ext):
            return True
    return False

# getImage
# @param url, a url to an image
# fetch the image at the specified url and save it as 'latest.jpg'
async def getImage(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            f = await aiofiles.open('latest.jpg', mode='wb')
            await f.write(await resp.read())
            await f.close()

# get_rgb_filter
# @param im, the STFC roster screenshot to find appropriate filter values for
# @returns rgb, a three-element list consisting of the rgb values for the filter
def get_rgb_filter(im):
    width, height = im.size
    rgb = [220, 220, 220]
    for i in range(3):
        im_rgb = im.crop((0, 0, width, math.floor(height/10)))
        logging.debug("trying r=" + str(rgb[0]), ", g=" + str(rgb[1]) + ", b=" + str(rgb[2]))
        apply_img_mask(im_rgb, rgb, x_percent)
        word = pytesseract.image_to_string(im_rgb)
        logging.debug("I read: " + word)
        if (bool(re.match(r"MEM", word))):
            logging.debug("found a working filter!")
            return rgb;
        else:
            rgb[0] -= 20
            rgb[1] -= 20
            rgb[2] -= 20
    return None

# apply_img_mask
# @param im, the image to apply a mask to
# @param rgb, a three-element list consisting of the rgb values for the mask threshold
# @param x_percent, what percentage of the width to crop off from the right. Used to
#        remove STFC rank symbols for premier, commodore, etc
def apply_img_mask(im, rgb, x_percent):
    pixdata = im.load()
    width, height = im.size
    x_cutoff = math.floor(width / x_percent)
    for x in range(width):
        for y in range(height):
            r,g,b = im.getpixel((x,y))
            if r < rgb[0] or g < rgb[1] or b < rgb[2] or x < x_cutoff:
                #out.putpixel((x,y), 0)
                pixdata[x,y] = (255, 255, 255);
            else:
                pixdata[x,y] = (0,0,0,0)

# process_image
# @param im, an STFC roster screenshot
# @param names_list, an empty list to populate with player names
# @param level_list, an empty list to populate with player levels
# @return True if success, False if an error occurred
async def process_image(ctx, im, names_list, level_list):
    width, height = im.size
    im_names = im.crop((0, math.floor(height/10), math.floor(width/2), height))
    names = pytesseract.image_to_string(im_names)
    tmp_list = names.replace("|", "").split('\n\n')
    success = False
    __flag = False
    for tmp in tmp_list:
        if (bool(re.match(r"^[0-9]+ [a-zA-Z0-9]", tmp))):
            lv, name = tmp.split(' ', 1)
            level_list.append(lv)
            names_list.append(name)
            success = True
        elif (bool(re.match(r"^[0-9]+$", tmp))):
            level_list.append(tmp)
            __flag = True
        elif (__flag):
            names_list.append(tmp)
            success = True
    if not success:
        msg = "Unable to process image, please try again."
        logging.error(msg + " MSG: " + tmp)
        await ctx.send(msg)
    return True;

# check_spelling
# @param names_list, a list of player names to check the spelling of
#         using the dictionary file 'STFC_dict.txt'
async def check_spelling(ctx, names_list):
    spell = SpellChecker(language=None, case_sensitive=False)
    spell.word_frequency.load_text_file("STFC_dict.txt")
    
    for i in range(len(names_list)):
        word = names_list[i].lower()

        if word in spell:
            names_list[i] = word
            logging.debug(word + " is spelled correctly!")
        else:
            cor = spell.correction(word)
            if (cor != word):
                logging.debug("Corrected '{}' to '{}'".format(word, cor))
                names_list[i] = cor;
            else:
                msg = "Unrecognized player name {} in row {}. If this is a new player, please add them to the dictionary by doing '!add <player name>'".format(word, i)
                logging.warning(msg)
                await ctx.send(msg)
                names_list[i] = "DELETE_ME"
                continue

# store_in_db
# @param names_list, a list of player names
# @param lv_list, a list of player levels
# @param power_list, a list of player power
# @param which alliance the roster screenshot belongs to
async def store_in_db(ctx, names_list, lv_list, power_list, team):
    for i in range(0, len(names_list)):
        if i < len(lv_list) and i < len(power_list) and names_list[i] != "DELETE_ME":
            cur = conn.cursor()
            sql = '''SELECT key FROM alias WHERE name="{}"'''.format(names_list[i]);
            logging.debug('SQL: ' + sql)
            cur.execute(sql)
            value_list = cur.fetchone()
            key = -1
            if value_list is None:
                key = add_name_to_alias(names_list[i])
            else:
                key = value_list[0]

            sql = '''SELECT * FROM LVE WHERE PlayerKey={} AND Date="{}"'''.format(key, datetime.datetime.now().strftime("%Y-%m-%d"))
            logging.debug("SQL: " + sql)
            cur.execute(sql)
            value_list = cur.fetchone()
            if value_list is not None:
                err_msg = "Data for player {} has already been entered today. Skipping this player...".format(names_list[i])
                logging.warning(err_msg)
                await ctx.send(err_msg)
                continue
            try:
                sql = '''INSERT INTO LVE (PlayerKey, Date, Alliance, Lv, Power) VALUES ("{}", "{}", "{}", "{}", "{}")'''.format(key,
                    datetime.datetime.now().strftime("%Y-%m-%d"),
                    team,
                    int(lv_list[i]),
                    int(power_list[i].replace(',', '')))
                logging.debug("SQL: " + sql)
                cur.execute(sql)
            except ValueError:
                err_msg = "Cannot interpret the power of player " + names_list[i] + " as an integer."
                logging.warning(err_msg, exc_info=True)
                await ctx.send(err_msg)
                continue

            msg = "Name: " + names_list[i] + ",\tLv: " + lv_list[i] + ",\tPower: " + power_list[i]
            logging.info(msg)
            await ctx.send(msg)
    conn.commit()

# init_logger
# initialize the logger to output msgs of lv INFO or higher to the console,
# and write messages of DEBUG or higher to a log file
def init_logger():
    logfile_name = datetime.datetime.now().strftime("%d-%m-%Y_%I-%M-%S_%p")
    #logging.basicConfig(filename='logs/'+logfile_name, filemode='w', format='[%(asctime)s] %(levelname)s: %(message)s')
    logFormatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.DEBUG)
    fileHandler = logging.FileHandler("{}/{}.log".format('logs', logfile_name))
    fileHandler.setFormatter(logFormatter)
    fileHandler.setLevel(logging.DEBUG)
    rootLogger.addHandler(fileHandler)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    consoleHandler.setLevel(logging.WARNING)
    rootLogger.addHandler(consoleHandler)

# -----------------------------------------------------------------------------
#                     DISCORD BOT COMMANDS & EVENTS
# -----------------------------------------------------------------------------
@bot.command()
async def ping(ctx):
    logging.debug("Player " + str(ctx.message.author) + " running command \'ping\'")
    logging.info('pong')
    await ctx.send('pong')

# Add a player name to the dictionary. !add <player_name>
@bot.command(description="Add a player name to the dictionary. !add <player_name>")
async def add(ctx):
    logging.debug("Player " + str(ctx.message.author) + " running command \'add\'")
    args = ctx.message.content[5:].split(' ')
    file = open("STFC_dict.txt", "a")
    for arg in args:
        file.write(arg + "\n")
        msg = 'Added \'' + arg + '\' to the dictionary'
        logging.info(msg)
        await ctx.send(msg)
    file.close()

# Add new roster screenshot data. !alliance <alliance_name> [attachment=image]
@bot.command(description="Add new roster screenshot data.")
async def alliance(ctx, alliance_name):
    logging.debug("Player " + str(ctx.message.author) + " running command \'alliance\'")
    await bot.change_presence(status=Status.dnd)
    num_attachments = len(ctx.message.attachments)
    if num_attachments < 1:
        msg = 'Please include a roster screenshot'
        logging.error(msg)
        await ctx.send(msg)
    else:
        for i in range(num_attachments):
            logging.debug("Looking at image " + str(i) + " of " + str(num_attachments))
            if not isImage(ctx, i):
                msg = 'Please only submit images. Stopping...'
                logging.error(msg)
                await ctx.send(msg)
                return False
            im_url = ctx.message.attachments[i].url
            await getImage(im_url)
            im = Image.open('latest.jpg')
            names_list = []
            level_list = []
            rgb = get_rgb_filter(im)
            if rgb is None:
                msg = "Unable to process screenshot"
                logging.error(msg)
                await ctx.send(msg)
                return False
            apply_img_mask(im, rgb, x_percent)
            if (await process_image(ctx, im, names_list, level_list)):
                await check_spelling(ctx, names_list)
                width, height = im.size
                power_list = []
                im_power = im.crop((math.floor(width/2), math.floor(height/10), width, height))
                power = pytesseract.image_to_string(im_power)
                power_list = power.split('\n')
                for i in range(len(power_list)):
                    power_list[i] = re.sub("[^0-9,]", "", power_list[i])
                power_list = list(filter(None, power_list))
                await store_in_db(ctx, names_list, level_list, power_list, alliance_name)
                await bot.change_presence(status=Status.online)


# Add a new alias. !alias <new_name> <old_name>
@bot.command()
async def alias(ctx):
    logging.debug("Player " + str(ctx.message.author) + " running command \'alias\'")
    args = ctx.message.content[7:].split(' ')
    cur = conn.cursor()
    if (len(args) < 2):
        msg = "Not enough arguments. Please add an alias using the format !alias <new_name> <old_name>"
        logging.error(msg)
        await ctx.send(msg)
        return False
    new_name = args[0].lower()
    old_name = args[1].lower()
    sql = '''SELECT key FROM alias WHERE name="{}"'''.format(old_name)
    logging.debug("SQL: " + sql)
    cur.execute(sql)
    value_list = cur.fetchone()
    if value_list is None:
        #add_name_to_alias(args[0])
        msg = "The player \"" + args[1] + "\" does not exist. Please add an alias using the format !alias <new_name> <old_name>"
        debug.error(msg)
        await ctx.send(msg)
    else:
        key = value_list[0]
        # check if the new name already exists in the database
        sql = '''SELECT key FROM alias WHERE name="{}"'''.format(new_name)
        logging.debug("SQL: " + sql)
        cur.execute(sql)
        value_list_2 = cur.fetchone()
        if value_list_2 is None:
            sql = '''INSERT INTO alias (key, name) VALUES ("{}", "{}")'''.format(key, new_name)
            logging.debug("SQL: " + sql)
            cur.execute(sql)
        else:
            sql = '''UPDATE alias SET key={} WHERE name="{}"'''.format(key, new_name)
            logging.debug("SQL: " + sql)
            cur.execute(sql)

        conn.commit()
        msg = "Created alias {} for player {}".format(args[0], args[1])
        logging.info(msg)
        await ctx.send(msg)

@bot.command()
async def time(ctx):
    logging.debug("Player " + str(ctx.message.author) + " running command \'time\'")
    midnight = datetime.datetime.combine(datetime.datetime.now().date(), datetime.time())
    time_diff = (midnight - datetime.datetime.now()).seconds;
    hours, remainder = divmod(time_diff, 3600)
    minutes, seconds = divmod(remainder, 60)
    #msg = "The current time is " + datetime.datetime.now().strftime("%H:%M:%S") +"\n"
    msg=""
    if (time_diff > 3600):
        msg = "The next reset is in " + str(hours) + " hours and " + str(minutes+1) + " minutes"
    elif (time_diff > 60):
        msg = "The next reset is in " + str(minutes+1) + " minutes"
    else:
        msg = "The next reset in " + str(seconds) + " seconds" 
    logging.info(msg)
    await ctx.send(msg)

@bot.event
async def on_ready():
    logging.info("Logged in as " + bot.user.name)


# ------------------------------------------------------------------------------
#                                 MAIN SCRIPT
# ------------------------------------------------------------------------------
init_logger()
f = open("secret.txt", "r")
TOKEN = f.read()
bot.run(TOKEN)