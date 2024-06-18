import datetime
import json
import logging
import os
import re
import subprocess
import sys
import argparse
import time
import threading
import shlex
import atexit

import requests
import zmq

from typing import Dict, TypedDict, Union
from packaging import version

from api.chzzk import ChzzkAPI

STREAMLINK_MIN_VERSION = "6.7.4"

DEFAULT_CFG = {
    'nid_aut': '',
    'nid_ses': '',
    'recording_save_root_dir': '',
    'quality': 'best',
    'record_chat': False,
    'file_name_format': '[{username}]{stream_started}_{escaped_title}.ts',
    'vod_name_format': '[{username}]{stream_started}_{escaped_title}.ts',
    'time_format': '%y-%m-%d %H_%M_%S',
    'msg_time_format': '%Y년 %m월 %d일 %H시 %M분 %S초',
    'fallback_to_current_dir': True,
    'mount_command': '',
    'interval': 10,
    'use_discord_bot': False,
    'zmq_port': 5555,
    'discord_bot_token': '',
    'target_user_id': ''
}

logger = logging.getLogger()
logger.setLevel(logging.INFO)
fmt = logging.Formatter("{asctime} {levelname} {name} {message}", style="{")
stream_hdlr = logging.StreamHandler()
stream_hdlr.setFormatter(fmt)
logger.addHandler(hdlr=stream_hdlr)


def escape_filename(s: str) -> str:
    """Removes special characters that cannot be used for a filename"""
    return re.sub(r"[/\\?%*:|\"<>.\n{}]", "", s)


def truncate_long_name(s: str) -> str:
    return (s[:75] + '..') if len(s) > 77 else s


def check_streamlink() -> bool:
    """Check if streamlink is installed and is of the required version."""
    try:
        ret = subprocess.check_output(["streamlink", "--version"], universal_newlines=True)
        re_ver = re.search(r"streamlink (\d+)\.(\d+)\.(\d+)", ret, flags=re.IGNORECASE)

        if not re_ver:
            raise FileNotFoundError

        s_ver = version.parse('.'.join(re_ver.groups()))
        return s_ver >= version.parse(STREAMLINK_MIN_VERSION)
    except FileNotFoundError:
        logger.error("Streamlink not found. Install streamlink first then launch again.")
        sys.exit(1)


class RecorderProcess(TypedDict):
    recorder: Union[None, subprocess.Popen]
    path: Union[None, str]
    chat_recorder: Union[None, subprocess.Popen]


class MultiChzzkRecorder:
    def __init__(self, cfg: dict) -> None:
        logger.info("Initializing Multi Chzzk Recorder...")

        if not check_streamlink():
            logger.error("streamlink 6.7.4 or newer is required. Please install/update streamlink.")
            sys.exit(1)

        self.recording_count = 0
        self.record_dict = {}
        self.discord_process = None

        logger.debug("\n".join([f"{key}: {value}" for key, value in cfg.items()]))

        self.quality = cfg['quality']
        self.INTERVAL = cfg["interval"]
        self.ROOT_PATH = cfg['recording_save_root_dir']
        self.CHAT = cfg['record_chat']
        self.NID_AUT = cfg['nid_aut']
        self.NID_SES = cfg['nid_ses']
        self.MNT_CMD = cfg['mount_command']
        self.FALLBACK = cfg['fallback_to_current_dir']
        self.FILE_NAME_FORMAT = cfg['file_name_format']
        self.TIME_FORMAT = cfg['time_format']
        self.MSG_TIME_FORMAT = cfg['msg_time_format']

        try:
            with open('record_list.txt', 'r') as f:
                channel_ids = f.read().splitlines()

        except FileNotFoundError:
            open('record_list.txt', 'w').close()
            channel_ids = []

        if self.CHAT:
            logger.info('Chat recording enabled.')
            self.chat_path = f"{os.path.dirname(__file__)}/ChzzkChat/run.py"
            logger.debug(f"Chat launch command: {self.chat_path}")

        logger.info(f'Quality set to: {self.quality}')
        self.chzzk = ChzzkAPI(self.NID_AUT, self.NID_SES)

        for channel_id in channel_ids:
            while True:
                channel_data = self.chzzk.get_channel_info(channel_id)
                if channel_data is not None:
                    self.record_dict[channel_id] = channel_data
                    channel_name = channel_data['channelName']

                    file_dir = os.path.join(self.ROOT_PATH, channel_name)

                    if not os.path.isdir(file_dir):
                        logger.info(f'Creating directory for {channel_id} ({channel_name})')
                        os.makedirs(file_dir)
                    break
                else:
                    logger.error(f'Failed to get channel {channel_id}. Retrying in 5 seconds...')
                    time.sleep(5)

        self.recorder_processes: Dict[str, RecorderProcess] = {}
        for channel_id in self.record_dict:
            self.recorder_processes[channel_id]: RecorderProcess = {
                'recorder': None,
                'path': None,
                'chat_recorder': None
            }

        if self.INTERVAL < 5:
            logger.warning("Check interval should not be lower than 5 seconds.")
            self.INTERVAL = 5
            logger.warning("Check interval has been set to 5 seconds.")

        self.socket = None
        self.command_socket = None
        if cfg['use_discord_bot']:
            self.socket, self.command_socket = self.init_discord_bot(cfg['discord_bot_token'], cfg['target_user_id'],
                                                                     cfg['zmq_port'])
            logger.info('Got socket')

            self.poll_thread = threading.Thread(target=self.poll_command, daemon=True)
            self.poll_thread.start()

        streamers_list_str = '\n'.join(
            [f'`{channel_data["channelName"]} ({channel_id})`' for channel_id, channel_data in
             self.record_dict.items()])

        self.send_embed(
            title="치지직 레코더 시작됨",
            description=f"채널 {len(self.record_dict)}개를 녹화 중입니다:\n{streamers_list_str}",
            fields=[
                {"name": "녹화 품질", "value": f"{'최고 품질 (기본값)' if self.quality == 'best' else self.quality}",
                 "inline": False},
                {"name": "저장 디렉토리", "value": f"`{self.ROOT_PATH}`", "inline": False},
                {"name": "확인 주기", "value": f"{self.INTERVAL}초", "inline": False},
                {"name": "마운트 명령어", "value": self.MNT_CMD if self.MNT_CMD else '사용 안함', "inline": False},
                {"name": "fallback 디렉토리 사용", "value": '예' if self.FALLBACK else '아니오', "inline": False},
                {"name": "채팅 기록", "value": '예' if self.CHAT else '아니오', "inline": False}
            ]
        )

        self.loop_running = False

    def init_discord_bot(self, token, user_id, port):
        if not token:
            logger.error('Discord bot token is required but not specified. Exiting...')
            sys.exit(1)

        logger.info('Starting discord bot..')
        self.discord_process = subprocess.Popen(["python3", "bots/discord_bot.py",
                                                 "-t", token,
                                                 "-u", user_id,
                                                 "-p", str(port),
                                                 "-i", str(self.INTERVAL)])

        logger.info("Connecting to discord bot...")

        context = zmq.Context()
        socket = context.socket(zmq.PAIR)
        socket.linger = 250
        socket.connect(f"tcp://localhost:{port}")

        command_socket = context.socket(zmq.REP)
        command_socket.bind(f"tcp://*:{port + 1}")

        while True:
            try:
                exit_code = self.discord_process.poll()

                if exit_code is not None:
                    logger.error(f"Discord bot exited with code {exit_code}. Exiting...")
                    context.destroy()
                    sys.exit(1)

                message_cmd = command_socket.recv_string(flags=zmq.NOBLOCK)
                message = socket.recv_string(flags=zmq.NOBLOCK)
                if message == 'ready' and message_cmd == 'ready':
                    logger.info('Discord bot is now ready. Continuing...')
                    command_socket.send_string('ok')
                    return socket, command_socket
            except zmq.ZMQError:
                time.sleep(1)

    def send_message(self, title: str, message: str, socket=None) -> None:
        if socket is None and self.socket:
            self.socket.send_json({
                'type': 'message',
                'title': title,
                'message': message
            })
        elif socket:
            socket.send_json({
                'type': 'message',
                'title': title,
                'message': message
            })

    def send_embed(self, title: str, description: str, socket=None, **kwargs) -> None:
        """Send an embed message to the discord bot.
        :param title: Title of the embed message.
        :param description: Description of the embed message.
        :param socket: ZMQ socket to send the message to. If None, the default socket will be used if available.
        :param kwargs: Additional fields for the embed message.
        Available fields: url, timestamp, color, fields, thumbnail, image, footer, provider
        """

        if socket is None and self.socket:
            self.socket.send_json({
                'type': 'embed',
                'contents': {
                    "title": title,
                    "description": description,
                    **kwargs
                }
            })
        elif socket:
            socket.send_json({
                'type': 'embed',
                'contents': {
                    "title": title,
                    "description": description,
                    **kwargs
                }
            })

    def send_alive(self, socket=None) -> None:
        if socket is None and self.socket:
            self.socket.send_json({
                'type': 'alive'
            })
        elif socket:
            socket.send_json({
                'type': 'alive'
            })

    def poll_command(self):
        logger.info('Command poller started.')
        while True:
            try:
                command_data = self.command_socket.recv_json(flags=zmq.NOBLOCK)
                logger.info(f'Got command: {command_data}')

                if command_data['type'] == 'add':
                    self.add_streamer(command_data['channel'], command_data['add_by_name'])
                elif command_data['type'] == 'remove':
                    self.remove_streamer(command_data['channel_id'])
                elif command_data['type'] == 'list':
                    self.send_list()
                elif command_data['type'] == 'dl':
                    self.download_vod(command_data['url'], command_data['quality'])
                else:
                    logger.error(f'Unknown command type: {command_data["type"]}')

            except zmq.ZMQError:
                pass
            time.sleep(1)

    def send_list(self):
        if self.record_dict:
            streamers_list_str = '\n'.join(
                [f'[REC] `{channel_data["channelName"]} ({channel_id})`' if self.recorder_processes[channel_id][
                                                                                'recorder'] is not None
                 else f'`{channel_data["channelName"]} ({channel_id})`' for channel_id, channel_data in
                 self.record_dict.items()])
            self.send_message("녹화 채널 목록",
                              f"채널 {len(self.record_dict)}개를 녹화 중입니다:\n"
                              f"{streamers_list_str}", socket=self.command_socket)
        else:
            self.send_message("녹화 채널 목록", "녹화 중인 채널이 없습니다.", socket=self.command_socket)

    def save_record_dict(self):
        with open('record_list.txt', 'w') as f:
            f.write('\n'.join(self.record_dict.keys()))

    def add_streamer(self, channel: str, add_by_name: bool):
        if add_by_name:
            channel_id = self.chzzk.get_channel_id(channel)
        else:
            channel_id = channel

        if channel_id in self.record_dict:
            self.send_message('추가 실패', f"채널 ID `{channel_id}` 는 이미 추가되어 있습니다.", socket=self.command_socket)
            return
        elif not (channel_data := self.chzzk.get_channel_info(channel_id)):
            self.send_message('추가 실패', f"채널 ID `{channel_id}`는 올바른 치지직 채널이 아닙니다.", socket=self.command_socket)
            return

        while True:
            if not self.loop_running:
                self.record_dict[channel_id] = channel_data
                self.recorder_processes[channel_id]: RecorderProcess = {
                    'recorder': None,
                    'path': None
                }
                break

        username = channel_data['channelName']
        file_dir = os.path.join(self.ROOT_PATH, username)

        if not os.path.isdir(file_dir):
            logger.info(f'Creating directory for {channel_id} ({username})')
            os.makedirs(file_dir)

        self.save_record_dict()

        self.send_message("채널 추가됨", f"채널 `{username} ({channel_id})`을/를 녹화 목록에 추가했습니다.", socket=self.command_socket)

        logger.info(f'Added {channel_id}')

    def remove_streamer(self, channel_id: str):
        if channel_id not in self.record_dict:
            self.send_message('제거 실패', f"채널 ID `{channel_id}`는 추가된 채널이 아닙니다.\n"
                                       f"',list' 명령어로 추가된 채널 ID를 확인하세요.", socket=self.command_socket)
            return

        while True:
            if not self.loop_running:
                removed_channel_data = self.record_dict.pop(channel_id)

                if self.recorder_processes[channel_id]['recorder'] is not None:
                    self.recorder_processes[channel_id]['recorder'].terminate()
                    self.recorder_processes[channel_id]['recorder'].wait()
                    self.recording_count -= 1

                del self.recorder_processes[channel_id]
                break

        self.save_record_dict()

        self.send_message("제거 성공", f"채널 `{removed_channel_data['channelName']} ({channel_id})`을/를 녹화 목록에서 제거했습니다.",
                          socket=self.command_socket)

        logger.info(f'Removed {channel_id}')

    def get_file_path(self, username: str, file_name: str, is_vod=False):
        if is_vod:
            sub_dir = 'VOD'
        else:
            sub_dir = username

        if not os.path.isdir(self.ROOT_PATH):
            logger.error("Root path does not exist!")
            if self.MNT_CMD:
                logger.info("Attempting to mount...")
                try:
                    subprocess.run(shlex.split(self.MNT_CMD), check=True)

                    if not os.path.isdir(self.ROOT_PATH):
                        raise FileNotFoundError
                    else:
                        logger.info("Mounted successfully.")

                except (FileNotFoundError, subprocess.CalledProcessError):
                    logger.error("Mount command failed!")

            if not os.path.isdir(self.ROOT_PATH) and self.FALLBACK:
                logger.info("Saving to current directory as fallback...")

                if not os.path.isdir('fallback_recordings'):
                    os.mkdir('fallback_recordings')

                if not os.path.isdir(os.path.join('fallback_recordings', sub_dir)):
                    os.mkdir(os.path.join('fallback_recordings', sub_dir))

                file_dir = os.path.join(os.getcwd(), 'fallback_recordings', sub_dir)
                self.send_message('경고',
                                  f'`{username}`의 녹화를 fallback 디렉토리에 저장합니다..\n'
                                  '설정된 녹화 저장 디렉토리가 접근 가능한지 확인하세요.')
            else:
                self.send_message('오류',
                                  f"저장 디렉토리가 접근 불가능하므로 녹화를 시작할 수 없습니다.\n"
                                  '저장 디렉토리가 온라인이고 마운트됐는지 확인하세요.')
                return None
        else:
            file_dir = os.path.join(self.ROOT_PATH, sub_dir)

        rec_file_path = os.path.join(file_dir, file_name)

        uq_num = 0
        while os.path.exists(rec_file_path):
            logger.warning("File already exists, will add numbers: %s", rec_file_path)
            uq_num += 1
            file_path_no_ext, file_ext = os.path.splitext(rec_file_path)

            if uq_num > 1 and file_path_no_ext.endswith(f" ({uq_num - 1})"):
                file_path_no_ext = file_path_no_ext.removesuffix(f" ({uq_num - 1})")

            rec_file_path = f"{file_path_no_ext} ({uq_num}){file_ext}"

        return rec_file_path

    def download_vod(self, url: str, quality: str):
        now = datetime.datetime.now()
        video_data = self.chzzk.get_video(url)

        if video_data is None:
            self.send_message('다운로드 실패',
                              f'`{url}`의 정보를 가져오는 데 실패했습니다.\n올바른 URL인지 확인하세요.',
                              socket=self.command_socket)
            return

        if not quality:
            quality = self.quality

        username = video_data['channel']["channelName"]
        video_title = video_data["videoTitle"]
        stream_started_time = datetime.datetime.strptime(video_data["liveOpenDate"], '%Y-%m-%d %H:%M:%S')
        uploaded_time = datetime.datetime.strptime(video_data["publishDate"], '%Y-%m-%d %H:%M:%S')

        video_duration = datetime.timedelta(seconds=video_data["duration"])

        _data = {
            "username": username,
            "escaped_title": truncate_long_name(escape_filename(video_title)),
            "stream_started": stream_started_time.strftime(self.TIME_FORMAT),
            "uploaded": uploaded_time.strftime(self.TIME_FORMAT),
            "download_started": now.strftime(self.TIME_FORMAT)
        }
        file_name = str(self.FILE_NAME_FORMAT.format(**_data))
        rec_file_path = self.get_file_path(username, file_name, is_vod=True)

        def on_streamlink_exit(return_code):
            nonlocal video_data
            nonlocal rec_file_path
            nonlocal now

            if return_code == 0:
                completed_time = datetime.datetime.now()
                elapsed_time = completed_time - now

                self.send_embed('다운로드 성공',
                                f'`{video_data["videoTitle"]}`의 다운로드가 완료되었습니다.',
                                fields=[
                                    {"name": "파일 크기",
                                     "value": self.get_readable_file_size(os.path.getsize(rec_file_path)),
                                     "inline": False},
                                    {"name": "파일 경로", "value": f"`{rec_file_path}`", "inline": False},
                                    {"name": "소요 시간", "value": f"{str(elapsed_time)}", "inline": False}
                                ],
                                socket=self.command_socket)
            else:
                self.send_message('다운로드 실패', f'`{url}` 의 다운로드 중 오류가 발생했습니다.', socket=self.socket)

        def start_dl(on_exit, popen_args):
            proc = subprocess.Popen(popen_args)
            proc.wait()
            on_exit(proc.returncode)
            return

        command_string = 'streamlink ' \
                         f'{url} ' \
                         f'{quality} ' \
                         f'-o "{rec_file_path}" ' \
                         f'--http-cookie NID_AUT={self.NID_AUT} ' \
                         f'--http-cookie NID_SES={self.NID_SES}'

        command = shlex.split(command_string)

        logger.info(f"Downloading {url} at {rec_file_path}")
        thread = threading.Thread(target=start_dl, args=(on_streamlink_exit, command))
        thread.start()

        self.send_embed(
            title="다운로드 시작됨",
            description=f"VOD 다운로드 중...",
            thumbnail={"url": video_data['thumbnailImageUrl']},
            fields=[
                {"name": "제목", "value": f"`{video_title}`", "inline": False},
                {"name": "방송 시작", "value": f"`{stream_started_time.strftime(self.MSG_TIME_FORMAT)}`", "inline": False},
                {"name": "업로드", "value": f"`{uploaded_time.strftime(self.MSG_TIME_FORMAT)}`", "inline": False},
                {"name": "길이", "value": f"{str(video_duration)}`", "inline": False},
                {"name": "품질", "value": f"`{'최고 품질' if quality == 'best' else quality}`", "inline": False},
                {"name": "파일 경로", "value": f"`{rec_file_path}`", "inline": False}
            ],
            socket=self.command_socket
        )

        return

    def loop(self):
        """main loop function"""
        logger.info("Check/record loop starting...")
        while True:
            self.loop_running = True
            message_sent = False
            logger.info('Check cycle started.')
            for channel_id in self.record_dict:
                recorder = self.recorder_processes[channel_id]['recorder']
                if recorder is not None:  # if recording was in progress, check if it had been finished
                    if recorder.poll() is not None:  # Check if there is a return code
                        logger.info(f"Recording of {channel_id} stopped.")
                        process = self.recorder_processes[channel_id]['recorder']

                        try:
                            rec_file_path = self.recorder_processes[channel_id]['path']
                            readable_size = self.get_readable_file_size(os.path.getsize(rec_file_path))

                            self.send_embed(
                                title="녹화 종료됨",
                                description=f"채널 `{self.record_dict[channel_id]['channelName']}`의 녹화가 끝났습니다.",
                                thumbnail={"url": self.record_dict[channel_id]['channelImageUrl']},
                                fields=[
                                    {"name": "파일 경로", "value": f"`{self.recorder_processes[channel_id]['path']}`",
                                     "inline": False},
                                    {"name": "파일 크기", "value": readable_size, "inline": False}
                                ]
                            )

                        except FileNotFoundError:
                            logger.error(f"Recorded file of {channel_id} not found!")
                            stdout, stderr = process.communicate()

                            self.send_message("녹화 파일 찾을 수 없음",
                                              f"`{self.record_dict[channel_id]['channelName']} ({channel_id})`의 녹화를 시작할 수 없습니다.\n"
                                              f"```{stderr.decode()}```")


                        message_sent = True
                        self.recorder_processes[channel_id]['recorder'] = None
                        self.recorder_processes[channel_id]['path'] = None

                        if self.recorder_processes[channel_id]['chat_recorder'] is not None:
                            self.recorder_processes[channel_id]['chat_recorder'].terminate()
                            self.recorder_processes[channel_id]['chat_recorder'].wait()
                            self.recorder_processes[channel_id]['chat_recorder'] = None

                        self.recording_count -= 1

                else:
                    try:
                        username = self.record_dict[channel_id]["channelName"]
                        is_streaming, stream_data = self.chzzk.check_live(channel_id)
                        if is_streaming is None:
                            self.send_message('채널 확인 실패', f'채널 {username}의 방송 상태를 확인하던 중 오류가 발생했습니다.')
                            message_sent = True
                        elif is_streaming and self.recorder_processes[channel_id]['recorder'] is None:
                            logger.info(f"{channel_id} is online. Starting recording...")
                            now = datetime.datetime.now()
                            _data = {
                                "username": self.record_dict[channel_id]["channelName"],
                                "escaped_title": truncate_long_name(escape_filename(stream_data["liveTitle"])),
                                "stream_started": datetime.datetime.strptime(
                                    stream_data["openDate"], '%Y-%m-%d %H:%M:%S').strftime(self.TIME_FORMAT),
                                "record_started": now.strftime(self.TIME_FORMAT)
                            }
                            file_name = self.FILE_NAME_FORMAT.format(**_data)

                            rec_file_path = self.get_file_path(username, file_name)

                            if rec_file_path is None:
                                continue

                            # start streamlink process
                            logger.info("Recorded video will be saved at %s", rec_file_path)

                            command_string = 'streamlink ' \
                                             f'https://chzzk.naver.com/live/{channel_id} ' \
                                             f'{self.quality} ' \
                                             f'-o "{rec_file_path}" ' \
                                             f'--http-cookie NID_AUT={self.NID_AUT} ' \
                                             f'--http-cookie NID_SES={self.NID_SES}'

                            command = shlex.split(command_string)

                            logger.info("Recorded video will be saved at %s", rec_file_path)
                            self.recorder_processes[channel_id]['recorder'] = subprocess.Popen(command)
                            self.recorder_processes[channel_id]['path'] = rec_file_path

                            if self.CHAT:
                                chat_file_path = rec_file_path.removesuffix('.ts') + '.txt'
                                self.recorder_processes[channel_id]['chat_recorder'] = subprocess.Popen(
                                    ["python3", self.chat_path,
                                     "--nid_ses", self.NID_SES,
                                     "--nid_aut", self.NID_AUT,
                                     "--streamer_id", channel_id,
                                     "--file_path", chat_file_path,
                                     "--start_time", str(now.timestamp())],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE
                                )

                            self.recording_count += 1

                            record_started_time_str = datetime.datetime.strptime(
                                stream_data["openDate"], '%Y-%m-%d %H:%M:%S').strftime(self.MSG_TIME_FORMAT)
                            self.send_embed(
                                title="녹화 시작됨",
                                description=f"채널 `{username}`의 녹화를 시작합니다.",
                                thumbnail={"url": self.record_dict[channel_id]['channelImageUrl']},
                                fields=[
                                    {"name": "제목", "value": f"`{stream_data['liveTitle']}`", "inline": False},
                                    {"name": "방송 시작", "value": record_started_time_str, "inline": False},
                                    {"name": "녹화 시작", "value": now.strftime(self.MSG_TIME_FORMAT), "inline": False},
                                    {"name": "파일 경로", "value": f"`{rec_file_path}`", "inline": False}
                                ]
                            )
                            message_sent = True

                        elif not is_streaming:
                            logger.info(f"{channel_id} is offline.")
                    except requests.RequestException:
                        logger.error(f'Exception while checking {channel_id}')

            logger.info(f'Check cycle complete. Starting next cycle in {str(self.INTERVAL)} seconds.')

            if self.recording_count:
                logger.info(f'{self.recording_count} recording(s) in progress')

            if not message_sent:
                self.send_alive()

            self.loop_running = False
            time.sleep(self.INTERVAL)

    def cleanup(self):
        logger.info("Cleaning up...")

        if self.discord_process:
            self.discord_process.terminate()
            self.discord_process.wait()

        logger.info("Exiting...")

    @staticmethod
    def get_readable_file_size(size_in_bytes) -> str:
        # human-readable file size
        # initial size is in bytes
        if size_in_bytes > 1024 ** 3:  # Over 1GB
            readable_size = f"{size_in_bytes / (1024 ** 3):.1f} GB"
        elif size_in_bytes > 1024 ** 2:  # Over 1MB
            readable_size = f"{size_in_bytes / (1024 ** 2):.1f} MB"
        elif size_in_bytes > 1024:  # Over 1KB
            readable_size = f"{size_in_bytes / 1024:.1f} KB"
        else:  # Less than 1KB
            readable_size = f"{size_in_bytes} Bytes"

        return readable_size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    args = parser.parse_args()

    logger.setLevel(args.log_level)
    logger.info(args)

    if not os.path.isfile('config.json'):
        logger.warning('config.json not found!')
        with open('config.json', 'w') as f:
            json.dump(DEFAULT_CFG, f, indent=4)
            logger.info('Created default config file. Review and edit settings as required. Exiting...')
            sys.exit(0)
    else:
        with open('config.json', 'r') as f:
            temp_cfg = json.load(f)
            cfg = {}

        cfg_update_required = False

        for key in DEFAULT_CFG.keys():
            if key not in temp_cfg:
                cfg[key] = DEFAULT_CFG[key]
                logger.info(f'Adding missing config key: {key}')
                cfg_update_required = True
            else:
                cfg[key] = temp_cfg[key]

        if cfg_update_required:
            with open('config.json', 'w') as f:
                json.dump(cfg, f, indent=4)
                logger.info('Updated config file with new settings.')

    if os.path.isdir(cfg['recording_save_root_dir']):
        logger.info(f"Save directory set to: {cfg['recording_save_root_dir']}")
    else:
        logger.error(f"Save directory does not exist: {cfg['recording_save_root_dir']}")
        sys.exit(1)

    if cfg['fallback_to_current_dir']:
        logger.info("Fallback to current directory is enabled.")
        logger.info(
            "If save directory is offline or unreachable, recordings will be saved to current directory instead.")

    recorder = MultiChzzkRecorder(cfg)
    atexit.register(recorder.cleanup)
    recorder.loop()


if __name__ == "__main__":
    main()
