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
import traceback
import shlex
import atexit
from typing import Dict, Any

import requests
import zmq

from chzzk.checker import ChzzkChecker

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


class MultiChzzkRecorder:
    def __init__(self, quality: str, cfg: dict) -> None:
        logger.info("Initializing Multi Chzzk Recorder...")

        ret = subprocess.check_output(["streamlink", "--plugins"], universal_newlines=True)
        installed_plugins = ret.split(': ')[-1].split(', ')

        if 'chzzk' not in installed_plugins:
            logger.error("Streamlink plugin for chzzk is not installed. Exiting...")
            sys.exit(1)

        self.recording_count = 0
        self.record_dict = {}
        self.discord_process = None

        self.FFMPEG = "ffmpeg"
        self.INTERVAL = cfg["interval"]
        self.ROOT_PATH = cfg['recording_save_root_dir']
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

        self.quality = quality
        logger.info(f'Quality set to: {self.quality}')
        self.chzzk = ChzzkChecker(self.NID_AUT, self.NID_SES)

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

        self.recorder_processes = {}
        for channel_id in self.record_dict:
            self.recorder_processes[channel_id] = {
                'recorder': None,
                'path': None
            }

        if self.INTERVAL < 5:
            logger.warning("Check interval should not be lower than 5 seconds.")
            self.INTERVAL = 5
            logger.warning("Check interval has been set to 5 seconds.")

        self.socket = None
        self.command_socket = None
        if cfg['use_discord_bot']:
            self.socket, self.command_socket = self.init_discord_bot(cfg['discord_bot_token'], cfg['target_user_id'], cfg['zmq_port'])
            logger.info('Got socket')

            self.poll_thread = threading.Thread(target=self.poll_command, daemon=True)
            self.poll_thread.start()

        streamers_list_str = '\n'.join([f'`{channel_data["channelName"]} ({channel_id})`' for channel_id, channel_data in self.record_dict.items()])

        self.send_embed({
            "title": "치지직 레코더 시작됨",
            "description": f"채널 {len(self.record_dict)}개를 녹화 중입니다:\n{streamers_list_str}",
            "fields": [
                {"name": "녹화 품질", "value": f"{'최고 품질 (기본값)' if self.quality == 'best' else self.quality}", "inline": False},
                {"name": "저장 디렉토리", "value": f"`{self.ROOT_PATH}`", "inline": False},
                {"name": "확인 주기", "value": f"{self.INTERVAL}초", "inline": False},
                {"name": "마운트 명령어", "value": self.MNT_CMD if self.MNT_CMD else '사용 안함', "inline": False},
                {"name": "fallback 디렉토리 사용", "value": '예' if self.FALLBACK else '아니오', "inline": False}
            ]
        })

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
        command_socket.bind(f"tcp://*:{[port + 1]}")

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

    def send_embed(self, contents: Dict[str, Any], socket=None) -> None:
        """Send an embed message to the discord bot.
        :param contents: Embed contents.
        Supported keys: title, type, description, url, timestamp, color, fields, thumbnail, image, footer, provider

        :param socket: ZMQ socket to send the message to. If None, the default socket will be used if available."""
        if socket is None and self.socket:
            self.socket.send_json({
                'type': 'embed',
                'contents': contents
            })
        elif socket:
            socket.send_json({
                'type': 'embed',
                'contents': contents
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
                data = self.command_socket.recv_json(flags=zmq.NOBLOCK)
                logger.info(f'Got command: {data}')

                if data['type'] == 'add':
                    self.add_streamer(data['channel_id'])
                elif data['type'] == 'remove':
                    self.remove_streamer(data['channel_id'])
                elif data['type'] == 'list':
                    self.send_list()

            except zmq.ZMQError:
                pass
            time.sleep(1)

    def send_list(self):
        streamers_list_str = '\n'.join(
            [f'[REC] `{channel_data["channelName"]} ({channel_id})`' if self.recorder_processes[channel_id]['recorder'] is not None
             else f'`{channel_data["channelName"]} ({channel_id})`' for channel_id, channel_data in self.record_dict.items()])
        self.send_message("녹화 채널 목록",
                          f"채널 {len(self.record_dict)}개를 녹화 중입니다:\n"
                          f"{streamers_list_str}", socket=self.command_socket)

    def save_record_dict(self):
        with open('record_list.txt', 'w') as f:
            f.write('\n'.join(self.record_dict.keys()))

    def add_streamer(self, channel_id: str):
        if channel_id in self.record_dict:
            self.send_message('추가 실패', f"채널 ID `{channel_id}` 는 이미 추가되어 있습니다.", socket=self.command_socket)
            return
        elif not (channel_data := self.chzzk.get_channel_info(channel_id)):
            self.send_message('추가 실패', f"채널 ID `{channel_id}`는 올바른 치지직 채널이 아닙니다.", socket=self.command_socket)
            return

        while True:
            if not self.loop_running:
                self.record_dict[channel_id] = channel_data
                self.recorder_processes[channel_id] = {
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

        logger.info(f'Added {channel_id} to record dict')

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

                del self.recorder_processes[channel_id]
                break

        self.save_record_dict()

        self.send_message("제거 성공", f"채널 `{removed_channel_data['channelName']} ({channel_id})`을/를 녹화 목록에서 제거했습니다.", socket=self.command_socket)

        logger.info(f'Removed {channel_id} from record dict')

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

                        try:
                            rec_file_path = self.recorder_processes[channel_id]['path']
                            file_size = os.path.getsize(rec_file_path)

                            # human-readable file size
                            # initial size is in bytes
                            if file_size > 1024 ** 3:  # Over 1GB
                                readable_size = f"{file_size / (1024 ** 3):.1f} GB"
                            elif file_size > 1024 ** 2:  # Over 1MB
                                readable_size = f"{file_size / (1024 ** 2):.1f} MB"
                            elif file_size > 1024:  # Over 1KB
                                readable_size = f"{file_size / 1024:.1f} KB"
                            else:  # Less than 1KB
                                readable_size = f"{file_size} Bytes"

                            self.send_embed({
                                "title": "녹화 종료됨",
                                "description": f"채널 `{self.record_dict[channel_id]['channelName']}`의 녹화가 끝났습니다.",
                                "thumbnail": {
                                    "url": self.record_dict[channel_id]['channelImageUrl']
                                },
                                "fields": [
                                    {"name": "파일 경로", "value": f"`{self.recorder_processes[channel_id]['path']}`", "inline": False},
                                    {"name": "파일 크기", "value": readable_size, "inline": False}
                                ]

                            })

                        except FileNotFoundError:
                            logger.error(f"Recorded file of {channel_id} not found!")
                            self.send_message("녹화 파일 찾을 수 없음",
                                              f"`{self.record_dict[channel_id]['channelName']} ({channel_id})`의 녹화 파일을 찾을 수 없습니다.\n"
                                              f"streamlink의 문제일 수 있습니다. 로그를 확인하세요.")

                        message_sent = True
                        self.recorder_processes[channel_id]['recorder'] = None
                        self.recorder_processes[channel_id]['path'] = None

                        self.recording_count -= 1

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
                            "stream_started_msg": datetime.datetime.strptime(
                                stream_data["openDate"], '%Y-%m-%d %H:%M:%S').strftime(self.MSG_TIME_FORMAT),
                            "record_started": now.strftime(self.TIME_FORMAT)
                        }
                        file_name = self.FILE_NAME_FORMAT.format(**_data)

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

                                if not os.path.isdir(os.path.join('fallback_recordings', username)):
                                    os.mkdir(os.path.join('fallback_recordings', username))

                                file_dir = os.path.join(os.getcwd(), 'fallback_recordings', username)
                                self.send_message('경고',
                                                  f'`{username}`의 녹화를 fallback 디렉토리에 저장합니다..\n'
                                                  '설정된 녹화 저장 디렉토리가 접근 가능한지 확인하세요.')
                            else:
                                self.send_message('오류',
                                                  f"저장 디렉토리가 접근 불가능하므로 녹화를 시작할 수 없습니다.\n"
                                                  '저장 디렉토리가 온라인이고 마운트됐는지 확인하세요.')
                                continue
                        else:
                            file_dir = os.path.join(self.ROOT_PATH, username)

                        rec_file_path = os.path.join(file_dir, file_name)

                        uq_num = 0
                        while os.path.exists(rec_file_path):
                            logger.warning("File already exists, will add numbers: %s", rec_file_path)
                            uq_num += 1
                            file_path_no_ext, file_ext = os.path.splitext(rec_file_path)
                            if uq_num > 1 and file_path_no_ext.endswith(f" ({uq_num - 1})"):
                                file_path_no_ext = file_path_no_ext.removesuffix(f" ({uq_num - 1})")
                            rec_file_path = f"{file_path_no_ext} ({uq_num}){file_ext}"

                        # start streamlink process
                        logger.info("Recorded video will be saved at %s", rec_file_path)

                        command_string = 'streamlink ' \
                                      f'https://chzzk.naver.com/live/{channel_id} ' \
                                      f'{self.quality} ' \
                                      f'-o "{rec_file_path}"' \
                                      f"--http-header Cookie='NID_SES={self.NID_SES}; NID_AUT={self.NID_AUT}'"

                        command = shlex.split(command_string)

                        logger.info("Recorded video will be saved at %s", rec_file_path)
                        self.recorder_processes[channel_id]['recorder'] = subprocess.Popen(command)
                        self.recorder_processes[channel_id]['path'] = rec_file_path

                        self.recording_count += 1

                        self.send_embed({
                            "title": "녹화 시작됨",
                            "description": f"채널 `{username}`의 녹화를 시작합니다.",
                            "thumbnail": {
                                "url": self.record_dict[channel_id]['channelImageUrl']
                            },
                            "fields": [
                                {"name": "제목", "value": f"`{stream_data['liveTitle']}`", "inline": False},
                                {"name": "방송 시작", "value": f"`{_data['stream_started_msg']}`", "inline": False},
                                {"name": "녹화 시작", "value": f"`{now.strftime(self.MSG_TIME_FORMAT)}`", "inline": False},
                                {"name": "파일 경로", "value": f"`{rec_file_path}`", "inline": False}
                            ]
                        })
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--quality", default="best")
    parser.add_argument("-l", "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    args = parser.parse_args()

    logger.setLevel(args.log_level)
    logger.info(args)

    if not os.path.isfile('config.json'):
        logger.warning('config.json not found!')
        with open('config.json', 'w') as f:
            cfg = {
                'nid_aut': '',
                'nid_ses': '',
                'file_name_format': '[{username}]{stream_started}_{escaped_title}.ts',
                'time_format': '%y-%m-%d %H_%M_%S',
                'msg_time_format': '%y-%m-%d %H:%M:%S',
                'recording_save_root_dir': '',
                'fallback_to_current_dir': True,
                'mount_command': '',
                'interval': 10,
                'use_discord_bot': False,
                'zmq_port': 5555,
                'discord_bot_token': '',
                'target_user_id': ''
            }

            json.dump(cfg, f, indent=4)
            logger.info('Created default config file. Review and edit settings as required. Exiting...')
            sys.exit(0)
    else:
        with open('config.json', 'r') as f:
            cfg = json.load(f)

    if os.path.isdir(cfg['recording_save_root_dir']):
        logger.info(f"Save directory set to: {cfg['recording_save_root_dir']}")
    else:
        logger.error(f"Save directory does not exist: {cfg['recording_save_root_dir']}")
        sys.exit(1)

    if cfg['fallback_to_current_dir']:
        logger.info("Fallback to current directory is enabled.")
        logger.info("If save directory is offline or unreachable, recordings will be saved to current directory instead.")

    recorder = MultiChzzkRecorder(args.quality, cfg)
    atexit.register(recorder.cleanup)
    recorder.loop()


if __name__ == "__main__":
    main()
