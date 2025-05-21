# Scrape TV listings from Gracenote API
# Output in XMLTV format for Jellyfin

import argparse
import fnmatch
import random
import requests
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# Get command-line options
parser=argparse.ArgumentParser()
parser.add_argument("--lineup", type=str, help="LINEUP_ID", default="CAN-lineupId-DEFAULT")
parser.add_argument("--postal", type=str)
parser.add_argument("--country", type=str, default="CAN")
parser.add_argument("--days", type=int, default=1)
parser.add_argument("--output", "-o", type=str, help="Output file", default="gracenote.xml")
args=parser.parse_args()

if args.lineup is None:
    print ("Missing argument: lineup")
    sys.exit(1)
if args.postal is None:
    print ("Missing argument: postal")
    sys.exit(1)
if args.country is None:
    print ("Missing argument: country")
    sys.exit(1)

# Filter channels
# - Only these will be included in the output
allowed_channels = [
    "CIIIDT",
    "CKCODT",
    "CICADT",
    "CITYDT"
]

# Force series
# - Fix for shows not having episode numbers, but should be treated as a series so that "record series" function works.
# - Mainly intended for news or morning talk shows.
force_series = [
    "*News*",
    "CTV Your Morning"
]

BASE_URL = "https://tvlistings.gracenote.com/api/grid"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0',
    'Referer': 'https://tvlistings.gracenote.com/grid-affiliates.html?aid=gapzap'
}


# Generate random episode number
# This is used to trick Jellyfin into thinking this program is part of a series
def generate_random_episode_num(start_time, mode):
    """
    Generate a random episode number in xmltv_ns format: 'S.E.N'
    """
    # Fully random
    #season = random.randint(1, 99)
    #episode = random.randint(1, 99)
    #return f"{season}.{episode}.0"

    # Time-based
    utc_time = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ")
    utc_time = utc_time.replace(tzinfo=timezone.utc)
    dt = utc_time.astimezone()

    hour = dt.hour
    minute = dt.minute
    half_hours = (hour * 2) + (1 if minute >= 30 else 0)
    day_of_year = dt.timetuple().tm_yday

    # Note that xmltv_ns is zero-indexed
    if (mode == "xmltv_ns"):
        return f"{dt.month - 1}.{dt.day - 1}.{half_hours}/48" # xmltv_ns format
    if (mode == "dd_progid"):
        return f"{dt.month:02d}{dt.day:02d}{half_hours:02d}"  # dd_progid format
    if (mode == "xmltv_ns_doy"):
        return f"{day_of_year - 1}.{half_hours}.0"                # xmltv_ns day-of-year format
    return


# Convert UTC timestamps to local timezone with whatever format
def time_to_local(utc_timestamp):
    try:
        utc_time = datetime.strptime(utc_timestamp, "%Y-%m-%dT%H:%M:%SZ")
        utc_time = utc_time.replace(tzinfo=timezone.utc)
        #print (utc_time.strftime("%Y-%m-%d %H:%M"))

        local_dt = utc_time.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")

    except Exception as e:
        return "Unknown"


# Convert ISO8601 timestamp to XMLTV format
# Basically just drops everything that's not a number
def time_to_xmltv(utc_timestamp):
    try:
        utc_time = datetime.strptime(utc_timestamp, "%Y-%m-%dT%H:%M:%SZ")
        utc_time = utc_time.replace(tzinfo=timezone.utc)
        return utc_time.strftime("%Y%m%d%H%M%S")
    except Exception as e:
        return "Unknown"


# Send request to Gracenote API and return a list
def fetch_listings(lineup_id, postal_code, country, days):
    listings = []   # Start with empty list

    # Find timestamp for start of today
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Calculate nearest three-hour block (rounded down).  This is when we'll start pulling EPG data.
    nearest = (datetime.now().hour // 3) * 3

    # Pull data in three-hour chunks
    for hour in range(nearest, 24 * days, 3):
#    for hour in range(19, 20):
        ts = int((day + timedelta(hours=hour)).timestamp())

        # Send query for this time range
        params = {
            "lineupId": lineup_id,
            "timespan": "3",	# hours
            "headendId": "lineupId",
            "country": country,
            "postalCode": postal_code,
            "time": ts
        }
        res = requests.get(BASE_URL, params=params, headers=HEADERS)

        if res.status_code == 200:
            # Append JSON result to list.  Result contains a record for each channel that includes channel info and events (programs)
            data = res.json()
            listings.extend(data.get('channels', []))
        else:
            print (f"Failed to fetch at {ts} - HTTP {res.status_code}")

    return listings


def add_channel(channel, tv, added_channels):
    # Create XML entry for channel
    if channel['channelId'] in added_channels:
        return	# Already added, skip

    xmlchannel = ET.SubElement(tv, 'channel', id = channel.get('callSign', channel.get('channelId')))    # Channel ID is callSign, fallback to channelID.
    ET.SubElement(xmlchannel, 'display-name').text = channel.get('callSign', 'Unknown')

    if "thumbnail" in channel:
        if channel.get("thumbnail"):
            ET.SubElement(xmlchannel, 'icon').text = f"src={channel.get('thumbnail')}"

    added_channels.add(channel['channelId'])


def add_program(event, channel_id, tv):
    # Create XML entry for program
    prog = ET.SubElement(tv, 'programme', {
        'start': time_to_xmltv(event['startTime']),
        'stop': time_to_xmltv(event['endTime']),
        'channel': channel_id
    })

    # Now we need to read the program object inside this event and extract data
    program = event.get('program')
    # can add lang='en' to all these subelements later
    ET.SubElement(prog, 'title').text = program.get('title', 'No Title')
    # sub-title
    if 'episodeTitle' in program:
        if program.get('episodeTitle') is not None:
            ET.SubElement(prog, 'sub-title').text = program.get('episodeTitle')
    # desc
    if 'shortDesc' in program:
        if program.get('shortDesc') is not None:
            ET.SubElement(prog, 'desc').text = program.get('shortDesc')
    # episode-num
    if ('season' in program) and ('episode' in program):
        if program.get('season') is not None and program.get('episode') is not None:
            ET.SubElement(prog, 'episode-num', system='xmltv_ns').text = f"{program.get('season')}.{program.get('episode')}.0"

    # If there's no episode set already, see if we need to make it look like a series with a fake episode number
    if (prog.find('episode-num') is None):
        for pattern in force_series:
            if fnmatch.fnmatch(prog.find('title').text, pattern):
    #            if program.get('tmsId') is not None:
    #                progid = program.get('tmsId') + generate_random_episode_num(event['startTime'], "dd_progid")
    #                ET.SubElement(prog, 'episode-num', system='dd_progid').text = progid
    #            else:
    #                ET.SubElement(prog, 'episode-num', system='xmltv_ns').text = generate_random_episode_num(event['startTime'], "xmltv_ns")
                ET.SubElement(prog, 'episode-num', system='xmltv_ns').text = generate_random_episode_num(event['startTime'], "xmltv_ns_doy")


def main():
    # Create main XML doc to hold results
    tv = ET.Element('tv', {
        'generator-info-name': 'gracenote-xmltv',
        'generator-info-url': 'https://github.com/SolidElectronics/gracenote-xmltv.git'
    })

    added_channels = set()

    print("Fetching data from Gracenote...")
    grid = fetch_listings(args.lineup, args.postal, args.country, args.days)
    print(f"Processing {len(grid)} channel blocks...")

    # Add channels first so they're at the top of the file
    for channel in grid:
        if channel['callSign'] in allowed_channels:
            add_channel(channel, tv, added_channels)
    print(f"Found {len(added_channels)} channels")

    # Then add programs
    for channel in grid:
        if channel['callSign'] in allowed_channels:
            for event in channel.get('events', []):
                add_program(event, channel['callSign'], tv)

    # Write everything to output file as XML
    print(f"Writing {args.output}...")
    ET.ElementTree(tv).write(args.output, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    main()
