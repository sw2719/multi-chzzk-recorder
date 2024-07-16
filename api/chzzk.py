import requests
import json
import logging
import re

from typing import Union, Dict, TypedDict, List
from fake_useragent import UserAgent

REQUEST_TIMEOUT = 15
ua = UserAgent()
request_header = {"User-Agent": ua.chrome}
logger = logging.getLogger(__name__)


class ChzzkChannel(TypedDict):
    channelId: str
    channelName: str
    channelImageUrl: str
    openLive: bool


class ChzzkStream(TypedDict):
    liveTitle: str
    liveImageUrl: str
    openDate: str
    adult: bool


class ChzzkVideo(TypedDict):
    videoTitle: str
    publishDate: str
    thumbnailImageUrl: str
    duration: int
    channel: Dict[str, Union[str, bool]]
    liveOpenDate: str


class ChzzkAPI:
    def __init__(self, nid_aut: str, nid_ses: str):
        self._cookies = {'NID_AUT': nid_aut, 'NID_SES': nid_ses}

    def get_channel_info(self, channel_id: str) -> Union[ChzzkChannel, None]:
        """Get channel info from chzzk API.
        :param channel_id: Channel ID.
        :return: Channel info dict if channel exists, None otherwise."""
        with requests.get(f'https://api.chzzk.naver.com/service/v1/channels/{channel_id}',
                          headers=request_header, cookies=self._cookies, timeout=REQUEST_TIMEOUT) as r:
            try:
                r.raise_for_status()
                data = json.loads(r.text)['content']

                return data
            except requests.exceptions.HTTPError:
                logger.error(f'HTTP Error while getting channel {channel_id}')
                logger.error(f'HTTP Status code {r.status_code}')
                return None
            except requests.exceptions.Timeout:
                logger.error(f'Timeout while getting channel {channel_id}')
                return None

    def check_live(self, channel_id: str) -> (bool, Union[ChzzkStream, None]):
        with requests.get(f'https://api.chzzk.naver.com/service/v1/channels/{channel_id}/live-detail',
                          headers=request_header, cookies=self._cookies, timeout=REQUEST_TIMEOUT) as r:
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError:
                logger.error(f'HTTP Error while checking channel {channel_id}')
                logger.error(f'HTTP Status code {r.status_code}')
                return False, None
            except requests.exceptions.Timeout:
                logger.error(f'Timeout while checking channel {channel_id}')
                return False, None

            data = json.loads(r.text)
            return data['content']['status'] == 'OPEN', data['content']

    def get_video(self, video_url: str) -> Union[ChzzkVideo, None]:
        match = re.match(r'https://chzzk.naver.com/video/(\d+)', video_url)

        if not match:
            return None

        video_id = match.group(1)

        with requests.get(f'https://api.chzzk.naver.com/service/v1/videos/{video_id}',
                          headers=request_header, cookies=self._cookies, timeout=REQUEST_TIMEOUT) as r:
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError:
                logger.error(f'HTTP Error while getting video {video_id}')
                logger.error(f'HTTP Status code {r.status_code}')
                return None
            except requests.exceptions.Timeout:
                logger.error(f'Timeout while getting video {video_id}')
                return None

            return json.loads(r.text)['content']

    def _search_channel(self, channel_name, offset=0, size=5):
        with requests.get(f'https://api.chzzk.naver.com/service/v1/search/channels?keyword={channel_name}&offset={offset}&size={size}',
                          headers=request_header, cookies=self._cookies, timeout=REQUEST_TIMEOUT) as r:
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError:
                logger.error(f'HTTP Error while searching channel {channel_name}')
                logger.error(f'HTTP Status code {r.status_code}')
                return None
            except requests.exceptions.Timeout:
                logger.error(f'Timeout while searching channel {channel_name}')
                return None

            return json.loads(r.text)['content']['data']

    def _get_channel_by_name(self, channel_name, size=1):
        channels = self._search_channel(channel_name, size=size)

        if not channels:
            return None

        for channel in channels:
            channel = channel['channel']
            if channel['channelName'] == channel_name:
                return channel
        else:
            return None

    def get_channel_id(self, channel_name) -> str:
        channel = self._get_channel_by_name(channel_name)
        return channel['channelId'] if channel else None
