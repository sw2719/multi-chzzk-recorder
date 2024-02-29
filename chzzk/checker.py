import requests
import json

from typing import Union, Dict


def get_channel_info(channel_id: str) -> Union[Dict, None]:
    """Get channel info from chzzk API.
    :param channel_id: Channel ID.
    :return: Channel info dict if channel exists, None otherwise."""
    with requests.get(f'https://api.chzzk.naver.com/service/v1/channels/{channel_id}') as r:
        try:
            r.raise_for_status()
            data = json.loads(r.text)['content']

            return data
        except requests.exceptions.HTTPError:
            return None


def check_live(channel_id: str) -> (bool, dict | None):
    with requests.get(f'https://api.chzzk.naver.com/service/v1/channels/{channel_id}/live-detail') as r:
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError:
            return False, None

        data = json.loads(r.text)
        return data['content'] == 'OPEN', data['content']
