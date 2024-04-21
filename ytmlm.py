import json
import click
import mutagen

from pathlib import Path
from tqdm import tqdm
from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic
from ytmusicapi.setup import setup_oauth

LYR_TAG = "©lyr"

def get_id_from_filename(file: str):
    return file.split("[")[-1].split("]")[0]


def get_id_from_filepath(file: Path):
    return get_id_from_filename(file.name)


@click.command()
@click.option(
    "--music-dir",
    envvar="YTMLM_MUSIC_DIR",
    required=True,
    type=Path,
    help="Music directory",
)
@click.option(
    "--limit",
    envvar="YTMLM_LIMIT",
    default=999999,
    type=int,
    help="Number of songs to fetch (999999)",
)
@click.option(
    "--oauth-file",
    envvar="YTMLM_OAUTH_FILE",
    default="./oauth.json",
    type=Path,
    help="OAuth file path",
)
@click.option(
    "--oauth-content",
    envvar="YTMLM_OAUTH_CONTENT",
    type=str,
    default=None,
    help="JSON contents of the oauth.json file",
)
@click.option(
    "--cookie-txt",
    envvar="YTMLM_COOKIE_TXT",
    default=None,
    type=Path,
    help="Netscape formatted cookie.txt for yt-dlp",
)
def ytmlm(
    music_dir: Path,
    limit: int,
    oauth_file: Path,
    oauth_content: str,
    cookie_txt: Path,
):
    """Download liked music from YTM"""

    # Create song directory if it does not exist
    music_dir.mkdir(parents=True, exist_ok=True)

    oauth_dict = None

    if oauth_content is not None:
        oauth_dict = json.loads(oauth_content)
    else:
        if not oauth_file.exists():
            setup_oauth(filepath=str(oauth_file))
        oauth_content = json.loads(oauth_file.read_text())

    ytm = YTMusic(oauth_content)

    print("Getting liked music")
    tracks = ytm.get_liked_songs(limit)["tracks"]

    existing_ids = set(map(get_id_from_filepath, music_dir.glob("**/*.m4a")))

    to_download = list(filter(lambda x: x["videoId"] not in existing_ids, tracks))

    print(f"Got {len(tracks)} tracks, {len(to_download)} new to download")
    print("")

    ytdl = YoutubeDL(
        {
            "format": "ba[ext=m4a]",
            "cookiefile": (
                str(cookie_txt.absolute())
                if cookie_txt and cookie_txt.is_file()
                else None
            ),
            "writethumbnail": True,
            "outtmpl": {
                "default": f"{music_dir.absolute()}/%(artist)s/%(album)s/%(title)s - %(artist)s [%(id)s].%(ext)s",
                "pl_thumbnail": "",
            },
            "postprocessors": [
                {
                    "key": "FFmpegMetadata",
                    "add_chapters": True,
                    "add_metadata": True,
                    "add_infojson": "if_exists",
                },
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ],
        }
    )

    print("Downloading songs")
    ytdlp_errors = []
    for track in (t := tqdm(to_download)):
        try:
            t.set_description(
                f"Downloading song {track.get('title', track.get('videoId', 'Unknwon'))}"
            )
            url = f"https://music.youtube.com/watch?v={track['videoId']}"
            ytdl.download(url)
        except Exception as e:
            ytdlp_errors.append((track, e))

    print("Downloading lyrics: ", end="")
    newIds = set(map(lambda x: x["videoId"], tracks))
    videoId_file_dict = dict(
        filter(
            lambda videoId_file: videoId_file[0] in newIds,
            map(
                lambda file: (get_id_from_filepath(file), file),
                music_dir.glob("**/*.m4a"),
            ),
        )
    )
    m4a_dict = dict(
        filter(
            lambda videoId_m4a: LYR_TAG not in videoId_m4a[1]
            or videoId_m4a[1][LYR_TAG] is None,
            map(
                lambda videoId_file: (
                    videoId_file[0],
                    mutagen.File(videoId_file[1].absolute()),
                ),
                videoId_file_dict.items(),
            ),
        )
    )
    print(f"{len(m4a_dict)} new to download")
    lyrics_errors = []
    for videoId, m4a in (t := tqdm(m4a_dict.items())):
        try:
            t.set_description(
                f"Downloading lyrics for {videoId_file_dict[videoId].name}"
            )
            if lyricId := ytm.get_watch_playlist(videoId).get("lyrics"):
                lyrics = ytm.get_lyrics(lyricId).get("lyrics")
                m4a[LYR_TAG] = [lyrics]
                m4a.save()
        except Exception as e:
            lyrics_errors.append((videoId_file_dict[videoId].name, e))

    print("\nDownload complete.\n\nyt-dlp failures:\n")
    for track, e in ytdlp_errors:
        print(f"{track.get('title', track.get('videoId', 'Unknown'))}: {e}")
    print("\nlyrics failures:\n")
    for file, e in lyrics_errors:
        print(f"{file}: {e}")


if __name__ == "__main__":
    ytmlm()
