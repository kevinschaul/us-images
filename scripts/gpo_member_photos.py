#!/usr/bin/env python
"""
Scrape https://memberguide.gpo.gov and
save members' photos named after their Bioguide IDs.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlretrieve

# pip install -r requirements.txt
import mechanicalsoup

CURRENT_CONGRESS = 118

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36"
)

regex1 = re.compile(
    r'<a href="/member/[^/]+/(\w+)[^<]+</a></span>'
    '[^<]*<div[^<]+<div class="member-image"><img src="/img/member/([^"]+)"'
)

regex2 = re.compile('<a class="next" href="([^"]+)">')


def pause(last, delay):
    if last is None:
        return datetime.datetime.now()

    now = datetime.datetime.now()
    delta = (now - last).total_seconds()

    if delta < delay:
        sleep = delay - delta
        print(f"Sleep for {sleep} seconds")
        time.sleep(sleep)
    return datetime.datetime.now()


def get_members_pictorial(br, congress_number):
    """
    Get members for the given congress_number
    API documentation: https://pictorialapi.gpo.gov/index.html
    """
    response = br.get(
        f"https://pictorialapi.gpo.gov/api/GuideMember/GetMembers/{congress_number}"
    ).json()
    return [
        member
        for member in response["memberCollection"]
        if member["memberType"] in ("Senator", "Representative")
        and member["name"] != "Vacant, Vacant"
    ]


def get_legislators_current(br, include_historical=False):
    """
    Download legislators from sister project unitedstates/congress-legislators
    Optionally also include historical legislators (which significantly
    increases the download size)
    """
    legislators = br.get(
        "https://theunitedstates.io/congress-legislators/legislators-current.json"
    ).json()
    if include_historical:
        historical = br.get(
            "https://theunitedstates.io/congress-legislators/legislators-historical.json"
        ).json()
        legislators += historical
    return legislators


def match_bioguide_id(member_pictorial, legislators_current):
    """
    Attempt to find the corresponding Bioguide ID for the given member.

    There are many odd cases -- see tests/test_gpo_member_photos.py for
    examples.
    """
    name = member_pictorial["name"]

    # Map common nicknames from pictorial to legislators_current
    common_nicknames = {
        "Nick": "Nicolas",
        "Daniel": "Dan",
        "Mike": "Michael",
        "Richard": "Rich",
        "Christopher": "Chris",
    }

    matches = []
    for m in legislators_current:
        # First check whether the name matches
        name_matches = False
        m_name_last = m["name"]["last"].replace(" ", "")
        if m_name_last == member_pictorial["lastName"]:
            if m["name"]["first"] == member_pictorial["firstName"] or (
                "nickname" in m["name"]
                and m["name"]["nickname"] == member_pictorial["firstName"]
            ):
                name_matches = True
            # Sometimes the nickname is encoded in the first name
            elif member_pictorial["firstName"] in m["name"]["first"]:
                name_matches = True
            # Sometimes the nickname is encoded in the middle name
            elif (
                "middle" in m["name"]
                and member_pictorial["firstName"] in m["name"]["middle"]
            ):
                name_matches = True
            # Sometimes the nickname is not encoded
            elif (
                member_pictorial["firstName"] in common_nicknames
                and common_nicknames[member_pictorial["firstName"]]
                == m["name"]["first"]
            ):
                name_matches = True

        # The GPO has some first and last names swapped, so check those too
        if not name_matches and m["name"]["first"] == member_pictorial["lastName"]:
            if m_name_last == member_pictorial["firstName"] or (
                "nickname" in m["name"]
                and m["name"]["nickname"] == member_pictorial["firstName"]
            ):
                name_matches = True

        # If the name matches, check the office and state
        # Note: Assumes we're matching against most recent term
        if name_matches:
            most_recent_term = m["terms"][-1]
            mType = "sen" if member_pictorial["memberType"] == "Senator" else "rep"
            if (
                most_recent_term["state"] == member_pictorial["stateId"]
                and most_recent_term["type"] == mType
            ):
                matches.append(m)

    if len(matches) == 1:
        return matches[0]["id"]["bioguide"]
    else:
        if len(matches):
            raise ValueError(f"Multiple Bioguide ID matches found for {name}")
        else:
            raise ValueError(f"No Bioguide ID match found for {name}")


def save_metadata(bioguide_id):
    outdir = "congress/metadata"
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    outfile = os.path.join(outdir, bioguide_id + ".yaml")
    with open(outfile, "w") as f:
        f.write("name: GPO Member Guide\n")
        f.write("link: https://pictorial.gpo.gov\n")


def download_file(url, outfile):
    """Download file at url to outfile"""
    fn, info = urlretrieve(url, outfile)

    # Sanity check we got an image. urlretreive will still save
    # content on a 404. If we didn't get an image, kill the file
    # (since we already saved it, oops) and raise an exception.
    if info["Content-Type"] != "image/jpeg":
        os.unlink(fn)
        raise HTTPError()


def download_photos(br, photo_list, outdir, delay):
    last_request_time = None

    if not os.path.exists(outdir):
        os.makedirs(outdir)

    ok = 0

    for bioguide_id, photo_url in photo_list:
        print(bioguide_id, photo_url)

        filename = os.path.join(outdir, bioguide_id + ".jpg")
        if os.path.isfile(filename):
            print(" Image already exists:", filename)
        elif not args.test:
            last_request_time = pause(last_request_time, delay)
            try:
                download_file(photo_url, filename)
            except HTTPError as e:
                print("Image not available:", e)
            else:
                save_metadata(bioguide_id)
                ok += 1

    print("Downloaded", ok, "member photos.")
    return ok


def resize_photos():
    # Assumes they're congress/original/*.jpg
    os.system(os.path.join("scripts", "resize-photos.sh"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Save members' photos from pictorialapi.gpo.gov, named "
        "after their Bioguide IDs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-n",
        "--congress",
        default=CURRENT_CONGRESS,
        type=int,
        help="Congress session number, for example: 110, 111, 112, 113",
    )
    parser.add_argument(
        "-o",
        "--outdir",
        default="congress/original",
        help="Directory to save photos in",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=int,
        default=5,
        metavar="seconds",
        help="Rate-limiting delay between scrape requests",
    )
    parser.add_argument(
        "-t",
        "--test",
        action="store_true",
        help="Test mode: don't actually save images",
    )
    args = parser.parse_args()

    br = mechanicalsoup.Browser()
    br.set_user_agent(USER_AGENT)

    legislators_current = get_legislators_current(br, True)
    members_pictorial = get_members_pictorial(br, args.congress)

    photo_list = []
    errors = []
    for m in members_pictorial:
        if "nophotoimage.jpg" in m["imageUrl"]:
            name = m["name"]
            print(f"No photo available for {name}")
            errors.append(["No photo available", m])
        else:
            try:
                photo_list.append(
                    (match_bioguide_id(m, legislators_current), m["imageUrl"])
                )
            except ValueError as e:
                print(e)
                errors.append(["No Bioguide ID match", m])

    number = download_photos(br, photo_list, args.outdir, args.delay)

    if number:
        resize_photos()

    if len(errors):
        print(f"{len(errors)} legislators had errors. Details wrote to errors.json")
        with open("errors.json", "w") as f:
            json.dump(errors, f, indent=2)

# End of file
