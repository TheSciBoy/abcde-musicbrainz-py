#!/usr/bin/python3
# abcde-musicbrainz-tool (Python port)
#
# Helper script for abcde to work with the MusicBrainz WS API (v2).
# Drop-in replacement for the Perl abcde-musicbrainz-tool shipped with abcde,
# using only the Python standard library plus python-discid for reading discs.
#
# Based on the original Perl script, Copyright (c) 2012-2018 Steve McIntyre
# <93sam@debian.org>, licensed under the GNU GPL v2 or greater.

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FRAMES_PER_S = 75
VERSION = "1.0"
WS_URL = "https://musicbrainz.org/ws/2/discid/"
USER_AGENT = f"abcde-musicbrainz-tool-py/{VERSION} ( https://abcde.einval.com/ )"


def join_artist_credit(credits):
    # Perl version puts the *previous* credit's joinphrase before each name,
    # which drops a trailing joinphrase; keep that behaviour.
    out = ""
    for i, credit in enumerate(credits or []):
        if i > 0:
            out += credits[i - 1].get("joinphrase") or ""
        out += credit.get("name") or ""
    return out


def cmd_id(device):
    try:
        import discid
    except ImportError:
        print("Error: python-discid is required for --command id", file=sys.stderr)
        return 1
    try:
        disc = discid.read(device)
    except (discid.DiscError, discid.LibDiscIdNotFound) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    fields = [disc.id, str(disc.last_track_num + 1 - disc.first_track_num)]
    fields += [str(t.offset) for t in disc.tracks]
    fields.append(str(disc.sectors // FRAMES_PER_S))
    print(" ".join(fields))
    return 0


def cmd_calcid(discinfo):
    # https://musicbrainz.org/doc/Disc_ID_Calculation
    if len(discinfo) < 5:
        print("Insufficient or missing discinfo data.", file=sys.stderr)
        return 1
    first, last, leadin, leadout, *offsets = discinfo
    if len(offsets) > 99:
        print("Too many track offsets in discinfo data.", file=sys.stderr)
        return 1

    frames = [0] * 100
    for i, o in enumerate([leadout] + offsets):
        frames[i] = o + leadin

    s = hashlib.sha1()
    s.update(b"%02X%02X" % (first, last))
    for f in frames:
        s.update(b"%08X" % f)

    mbid = base64.b64encode(s.digest()).decode("ascii")
    mbid = mbid.translate(str.maketrans("+/=", "._-"))
    sys.stdout.write(mbid)
    if sys.stdout.isatty():
        sys.stdout.write("\n")
    return 0


def ws_lookup(discid):
    query = urllib.parse.urlencode({
        "inc": "artists artist-credits recordings",
        "fmt": "json",
    })
    url = WS_URL + urllib.parse.quote(discid, safe="") + "?" + query
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    # MusicBrainz rate limits to ~1 req/s; retry a few times on 503.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < 3:
                time.sleep(1 + attempt)
                continue
            try:
                body = json.load(e)
                return {"error": body.get("error") or str(e)}
            except ValueError:
                return {"error": str(e)}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"error": str(e)}


def xmcd_entry(header, discid, dtitle, year, titles):
    lines = ["# xmcd style database file", "#"]
    lines += header
    lines += [
        "#blues,classical,country,data,folk,jazz,newage,reggae,rock,soundtrack,misc",
        "#CATEGORY=none",
        f"DISCID={discid}",
        f"DTITLE={dtitle}",
        f"DYEAR={year}",
        "DGENRE=",
    ]
    lines += [f"TTITLE{i}={t}" for i, t in enumerate(titles)]
    lines.append("EXTD=")
    lines += [f"EXTT{i}=" for i in range(len(titles))]
    lines += ["PLAYORDER=", "."]
    return "\n".join(lines) + "\n"


def disc_length_header(tracks):
    total_ms = sum(t.get("length") or 0 for t in tracks)
    return [
        f"# Disc length: {int(total_ms / 1000.0)} seconds",
        "#",
        "# Submitted via: XXXXXX",
        "#",
    ]


def release_entry(release, discid, offsets):
    a_artist = join_artist_credit(release.get("artist-credit"))
    va = "Various Artists" in a_artist

    rel_year = ""
    events = release.get("release-events") or []
    if events:
        rel_year = (events[0].get("date") or "")[:4]

    # Locate the medium that contains a disc with the discid we requested.
    # The API may return multiple media for the release, including media
    # with different discids.
    media = [m for m in release.get("media") or []
             if any(d.get("id") == discid for d in m.get("discs") or [])]
    if not media:
        return None
    tracks = media[0].get("tracks") or []

    header = ["# Track frame offsets:"]
    header += [f"#       {o}" for o in offsets]
    header.append("#")
    header += disc_length_header(tracks)

    titles = []
    for track in tracks:
        name = track.get("title") or ""
        credits = (track.get("recording") or {}).get("artist-credit") or []
        if va and credits:
            titles.append(f"{join_artist_credit(credits)} / {name}")
        else:
            titles.append(name)

    return xmcd_entry(header, discid, f"{a_artist} / {release.get('title') or ''}",
                      rel_year, titles)


def stub_entry(response, discid):
    a_artist = response.get("artist") or ""
    a_title = response.get("title") or ""
    va = "Various Artists" in a_artist
    tracks = response.get("tracks") or []

    header = ["# Musicbrainz stub entry - check carefully!", "#"]
    header += disc_length_header(tracks)

    titles = []
    for track in tracks:
        name = track.get("title") or ""
        if va:
            titles.append(f"{track.get('artist') or ''} / {name}")
        else:
            titles.append(name)

    return xmcd_entry(header, discid, f"{a_artist} / {a_title}", "", titles)


def write_file(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_data(discid, workdir, start):
    if not discid:
        print("Discid undefined.", file=sys.stderr)
        return 1

    response = ws_lookup(discid)
    if response.get("error"):
        print(f'MusicBrainz lookup returned an error "{response["error"]}"',
              file=sys.stderr)
        return 0

    releasenum = start
    seen = set()

    def add(entry, mbid=None, asin=None):
        nonlocal releasenum
        # Skip entries identical to one we've already written
        checksum = hashlib.sha1(entry.encode("utf-8")).hexdigest()
        if checksum in seen:
            return
        seen.add(checksum)
        releasenum += 1
        write_file(os.path.join(workdir, f"cddbread.{releasenum}"), entry)
        if mbid is not None:
            write_file(os.path.join(workdir, f"mbid.{releasenum}"), mbid)
            write_file(os.path.join(workdir, f"asin.{releasenum}"), asin or "")

    if "releases" in response:
        offsets = response.get("offsets") or []
        for release in response["releases"]:
            entry = release_entry(release, discid, offsets)
            if entry is not None:
                add(entry, release.get("id") or "", release.get("asin"))
    else:
        # No releases - looks like we have a CD stub. We can parse it, but
        # it's going to be very different.
        print("MusicBrainz lookup only returned a stub, trying to cope",
              file=sys.stderr)
        add(stub_entry(response, discid))
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="abcde-musicbrainz-tool",
        description="MusicBrainz query tool for abcde",
        allow_abbrev=False)
    parser.add_argument("--command", default="id", choices=["id", "data", "calcid"],
                        help="mode of operation (default: id)")
    parser.add_argument("--device", default="/dev/cdrom",
                        help="read from CD-ROM device DEV (default: /dev/cdrom)")
    parser.add_argument("--discid", help="disc ID to query with --command data")
    parser.add_argument("--discinfo", nargs="+", type=int, default=[],
                        metavar="N",
                        help="<first> <last> <lead-in> <lead-out> <trk1off> [...] "
                             "for --command calcid")
    parser.add_argument("--workdir", default="/tmp",
                        help="directory for cddbread.* output (default: /tmp)")
    parser.add_argument("--start", type=int, default=0,
                        help="number output files from START+1 (default: 0)")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()

    if args.command == "id":
        return cmd_id(args.device)
    if args.command == "calcid":
        return cmd_calcid(args.discinfo)
    return cmd_data(args.discid, args.workdir, args.start)


if __name__ == "__main__":
    sys.exit(main())
