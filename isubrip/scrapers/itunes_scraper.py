from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterator

import m3u8
from requests.exceptions import HTTPError

from isubrip.data_structures import SubtitlesData, SubtitlesFormatType
from isubrip.logger import logger
from isubrip.scrapers.scraper import HLSScraper, PlaylistLoadError, ScraperError, ScraperFactory, SubtitlesDownloadError
from isubrip.subtitle_formats.webvtt import WebVTTSubtitles
from isubrip.utils import merge_dict_values, raise_for_status

if TYPE_CHECKING:
    from isubrip.data_structures import Movie, ScrapedMediaResponse


class ItunesScraper(HLSScraper):
    """An iTunes movie data scraper."""
    id = "itunes"
    name = "iTunes"
    abbreviation = "iT"
    url_regex = re.compile(r"(?i)(?P<base_url>https?://itunes\.apple\.com/(?:(?P<country_code>[a-z]{2})/)?(?P<media_type>movie|tv-show|tv-season|show)/(?:(?P<media_name>[\w\-%]+)/)?(?P<media_id>id\d{9,10}))(?:\?(?P<url_params>.*))?")
    subtitles_class = WebVTTSubtitles
    is_movie_scraper = True
    uses_scrapers = ["appletv"]

    _subtitles_filters = {
        HLSScraper.M3U8Attribute.GROUP_ID.value: ["subtitles_ak", "subtitles_vod-ak-amt.tv.apple.com"],
        **HLSScraper._subtitles_filters,  # noqa: SLF001
    }

    def __init__(self,  user_agent: str | None = None, config_data: dict | None = None):
        super().__init__(user_agent=user_agent, config_data=config_data)
        self._appletv_scraper = ScraperFactory.get_scraper_instance(
            scraper_id="appletv",
            kwargs={"config_data": config_data},
            extract_scraper_config=True,
            raise_error=True,
        )

    def get_data(self, url: str) -> ScrapedMediaResponse[Movie]:
        """
        Scrape iTunes to find info about a movie, and it's M3U8 main_playlist.

        Args:
            url (str): An iTunes store movie URL.

        Raises:
            InvalidURL: `itunes_url` is not a valid iTunes store movie URL.
            PageLoadError: HTML page did not load properly.
            HTTPError: HTTP request failed.

        Returns:
            Movie: A Movie (NamedTuple) object with movie's name, and an M3U8 object of the main_playlist
            if the main_playlist is found. None otherwise.
        """
        regex_match = self.match_url(url, raise_error=True)
        url = regex_match.group(1)
        logger.debug(f"Scraping iTunes URL: {url}.")
        response = self._session.get(url=url, allow_redirects=False)

        try:
            raise_for_status(response=response)

        except HTTPError as e:
            if response.status_code == 404:
                raise ScraperError(
                    "Media not found. This could indicate that the provided URL is invalid.",
                ) from e

            raise

        redirect_location = response.headers.get("Location")

        if response.status_code != 301 or not redirect_location:
            logger.debug(f"iTunes URL: {url} did not redirect to an Apple TV URL.\n"
                         f"Response status code: {response.status_code}.\n"
                         f"Response headers:\n{response.headers}.\n"
                         f"Response data:\n{response.text}.")
            raise ScraperError("Apple TV redirect URL not found.")

        if not self._appletv_scraper.match_url(redirect_location):
            logger.debug(f"iTunes URL: {url} redirected to an invalid Apple TV URL: '{redirect_location}'.")
            raise ScraperError("Redirect URL is not a valid Apple TV URL.")

        return self._appletv_scraper.get_data(redirect_location)

    def get_subtitles(self, main_playlist: str | list[str], language_filter: list[str] | str | None = None,
                      subrip_conversion: bool = False) -> Iterator[SubtitlesData | SubtitlesDownloadError]:
        language_filters = {self.M3U8Attribute.LANGUAGE.value: language_filter} if language_filter else None
        main_playlist_m3u8 = self.load_m3u8(url=main_playlist)

        if main_playlist_m3u8 is None:
            raise PlaylistLoadError("Could not load M3U8 playlist.")

        playlist_filters = (merge_dict_values(self._subtitles_filters, language_filters)
                            if language_filters
                            else self._subtitles_filters)

        matched_media_items = self.get_media_playlists(main_playlist=main_playlist_m3u8,
                                                       playlist_filters=playlist_filters)

        for matched_media in matched_media_items:
            language_name = matched_media.name.replace(' (forced)', '').strip()
            language_code = matched_media.language
            special_type = self.detect_subtitles_type(subtitles_media=matched_media)

            try:
                m3u8_data = self._session.get(url=matched_media.absolute_uri)
                matched_media_playlist = m3u8.loads(content=m3u8_data.text, uri=matched_media.absolute_uri)

                subtitles_segments = self._download_segments(matched_media_playlist.segments)
                subtitles = self.subtitles_class(data=subtitles_segments[0], language_code=language_code)

                for segment in subtitles_segments[1:]:
                    segment_subtitles_obj = self.subtitles_class(data=segment, language_code=language_code)
                    segment_subtitles_obj.remove_head_blocks()
                    subtitles.append_subtitles(segment_subtitles_obj)

                subtitles.polish(
                    fix_rtl=self.subtitles_fix_rtl,
                    remove_duplicates=self.subtitles_remove_duplicates,
                )

                language_name = matched_media.name.replace(' (forced)', '').strip()

                if subrip_conversion:
                    subtitles_format = SubtitlesFormatType.SUBRIP
                    content = subtitles.to_srt().dump()

                else:
                    subtitles_format = SubtitlesFormatType.WEBVTT
                    content = subtitles.dump()

                yield SubtitlesData(
                    language_code=language_code,
                    language_name=language_name,
                    subtitles_format=subtitles_format,
                    content=content,
                    content_encoding=subtitles.encoding,
                    special_type=special_type,
                )

            except Exception as e:
                yield SubtitlesDownloadError(
                    language_code=language_code,
                    language_name=language_name,
                    special_type=special_type,
                    original_exc=e,
                )
