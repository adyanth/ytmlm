import json
import click
import mutagen

from pathlib import Path
from tinytag import TinyTag
from tqdm import tqdm
from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic
from ytmusicapi.setup import setup_oauth
from ytmusicapi.auth.oauth import OAuthCredentials

LYR_TAG = "©lyr"
DAY_TAG = "©day"
NO_SYNCED_LYRICS = "[00:00.00] No synced lyrics found."
NO_UNSYNCED_LYRICS = "No unsynced lyrics found."


def get_id_from_filename(file: str):
    return file.split("[")[-1].split("]")[0]


def get_id_from_filepath(file: Path):
    return get_id_from_filename(file.name)


def get_synced_lyrics(file_path):
    import requests

    tags = TinyTag.get(file_path)

    url = "https://lrclib.net/api/get"
    params = {
        "artist_name": tags.artist,
        "track_name": tags.title,
        "album_name": tags.album,
        "duration": int(tags.duration),
    }

    response = requests.get(url, params=params)
    match response.status_code:
        case 200:
            if lyrics := response.json()["syncedLyrics"]:
                return lyrics
            else:
                return NO_SYNCED_LYRICS
        case 400 | 404:
            return NO_SYNCED_LYRICS
        case _:
            response.raise_for_status()
            raise Exception()


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
    "--oauth-client-secret-file",
    envvar="YTMLM_OAUTH_CLIENT_SECRET_FILE",
    type=Path,
    default="./client_secret.json",
    help="TV or limited device scoped OAuth2.0 Client Secret file path",
)
@click.option(
    "--oauth-client-secret-content",
    envvar="YTMLM_OAUTH_CLIENT_SECRET_CONTENT",
    type=str,
    default=None,
    help="TV or limited device scoped OAuth2.0 Client Secret Contents",
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
    oauth_client_secret_file: str,
    oauth_client_secret_content: str,
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

    oauth_client_dict = None
    if oauth_client_secret_content is not None:
        oauth_client_dict = oauth_client_secret_content
    else:
        if not oauth_client_secret_file.exists():
            raise Exception("Client secret file or content needed")
        oauth_client_dict = json.loads(oauth_client_secret_file.read_text())

    ytm = YTMusic(
        oauth_content,
        oauth_credentials=OAuthCredentials(
            client_id=oauth_client_dict["installed"]["client_id"],
            client_secret=oauth_client_dict["installed"]["client_secret"]
        )
    )

    print("Getting liked music")
    tracks = ytm.get_liked_songs(limit)["tracks"]

    # To debug
    # tracks = [{"videoId": "videoId", "title": "Test Song"}]

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
            or videoId_m4a[1][LYR_TAG] is None
            # Need both synced and unsynced lyrics to not try again
            or len(videoId_m4a[1][LYR_TAG]) != 2,
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
        file: Path = videoId_file_dict[videoId]
        # Clean up
        if len(m4a[DAY_TAG]) == 1:
            if len(m4a[DAY_TAG][0]) != 4:
                m4a[DAY_TAG][0] = m4a[DAY_TAG][0][:4]

        t.set_description(f"Downloading lyrics for {file.name}")

        lyrics = []
        # Unsynced lyrics from YT
        try:
            if lyricId := ytm.get_watch_playlist(videoId).get("lyrics"):
                unsynced = ytm.get_lyrics(lyricId).get("lyrics")
            else:
                unsynced = NO_UNSYNCED_LYRICS
            lyrics.append(unsynced)
        except Exception as e:
            lyrics_errors.append((file.name, e))

        # Synced lyrics from lrclib
        synced = None
        try:
            synced = get_synced_lyrics(file)
            lyrics.append(synced)
            # Also dump to lrc file
            if synced != NO_SYNCED_LYRICS:
                with open(file.with_suffix(".lrc"), "w") as f:
                    f.write(synced)
        except Exception as e:
            lyrics_errors.append((file.name, e))

        m4a[LYR_TAG] = lyrics
        # Save the lyrics
        m4a.save()

    print("\nDownload complete.\n\nyt-dlp failures:\n")
    for track, e in ytdlp_errors:
        print(f"{track.get('title', track.get('videoId', 'Unknown'))}: {e}")
    print("\nlyrics failures:\n")
    for file, e in lyrics_errors:
        print(f"{file}: {e}")


if __name__ == "__main__":
    ytmlm()
