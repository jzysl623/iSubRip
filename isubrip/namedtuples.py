from typing import NamedTuple, Union
from m3u8 import M3U8

from isubrip.enums import SubtitlesType


class MovieData(NamedTuple):
    """A named tuple containing a movie name and it's main M3U8 playlist."""
    name: str
    playlist: Union[M3U8, None]


class SubtitlesData(NamedTuple):
    """A named tuple containing language code, language name, type and playlist URL for subtitles."""
    language_code: str
    language_name: str
    subtitles_type: SubtitlesType
    playlist_url: str
