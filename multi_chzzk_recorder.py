#!/usr/bin/env python3

# Only works for Streamlink version >= 4.2.0

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
from typing import List, TypedDict, Union

import requests
import zmq

from chzzk.checker import check_live, get_channel_info

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

        self.cfg = cfg

        self.ffmpeg_path = "ffmpeg"
        self.refresh = self.cfg["interval"]
        self.root_path = self.cfg['recording_save_root_dir']
        self.recording_count = 0
        self.record_dict = {}

        try:
            with open('record_list.txt', 'r') as f:
                channel_ids = f.read().splitlines()

        except FileNotFoundError:
            open('record_list.txt', 'w').close()
            channel_ids = []

        self.quality = quality
        logger.info(f'Quality set to: {self.quality}')

        for channel_id in channel_ids:
            if channel_data := get_channel_info(channel_id):
                self.record_dict[channel_id] = channel_data
                channel_name = channel_data['channelName']

                file_dir = os.path.join(self.root_path, channel_name)

                if not os.path.isdir(file_dir):
                    logger.info(f'Creating directory for {channel_id} ({channel_name})')
                    os.makedirs(file_dir)

        self.recorder_processes = {}
        for channel_id in self.record_dict:
            self.recorder_processes[channel_id] = {
                'recorder': None,
                'path': None
            }

        if self.refresh < 5:
            logger.warning("Check interval should not be lower than 5 seconds.")
            self.refresh = 5
            logger.warning("Check interval has been set to 5 seconds.")

        self.socket = None
        self.command_socket = None
        if self.cfg['use_discord_bot']:
            self.socket, self.command_socket = self.init_discord_bot()
            logger.info('Got socket')

        streamers_list_str = '`\n`'.join([f'`{channel_data["channelName"]} ({channel_id})`' for channel_id, channel_data in self.record_dict.items()])

        self.send_message("Chzzk Recorder initialized",
                          f"Checking/recording {len(self.record_dict)} streamer(s):\n"
                          f"{streamers_list_str}\n\n"
                          f"Recording quality: ```{self.quality}```\n"
                          f"Recording save directory: ```{self.root_path}```\n"
                          f"Check interval: ```{self.refresh} seconds```\n"
                          f"Use fallback dir: ```{self.cfg['fallback_to_current_dir']}```\n")

        self.poll_thread = threading.Thread(target=self.poll_command)
        self.poll_thread.start()

        self.loop_running = False
        self.loop()

    def init_discord_bot(self):
        if not self.cfg['discord_bot_token']:
            logger.error('Discord bot token is required but not specified. Exiting...')
            sys.exit(1)

        token = self.cfg['discord_bot_token']
        target_user_id = self.cfg['target_user_id']
        zmq_port = self.cfg['zmq_port']

        logger.info('Starting discord bot..')
        discord_process = subprocess.Popen(["python3", "bots/discord_bot.py",
                                            "-t", token,
                                            "-u", target_user_id,
                                            "-p", str(zmq_port),
                                            "-i", str(self.cfg['interval'])])

        logger.info("Connecting to discord bot...")

        context = zmq.Context()
        socket = context.socket(zmq.PAIR)
        socket.linger = 250
        socket.connect(f"tcp://localhost:{self.cfg['zmq_port']}")

        command_socket = context.socket(zmq.REP)
        command_socket.bind(f"tcp://*:{self.cfg['zmq_port'] + 1}")

        while True:
            try:
                exit_code = discord_process.poll()

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
                    self.add_streamer(data['username'])
                elif data['type'] == 'remove':
                    self.remove_streamer(data['username'])
                elif data['type'] == 'list':
                    self.send_list()

            except zmq.ZMQError:
                pass
            time.sleep(1)

    def send_list(self):
        streamers_list_str = '`\n`'.join(
            [f'[REC] `{channel_data["channelName"]} ({channel_id})`' if self.recorder_processes[channel_id]['recorder'] is not None
             else f'`{channel_data["channelName"]} ({channel_id})`' for channel_id, channel_data in self.record_dict.items()])
        self.send_message("Record list",
                          f"Checking/recording {len(self.record_list)} streamer(s):\n"
                          f"{streamers_list_str}", socket=self.command_socket)

    def save_record_dict(self):
        with open('record_list.txt', 'w') as f:
            f.write('\n'.join(self.record_dict.keys()))

    def add_streamer(self, channel_id: str):
        if channel_id in self.record_dict:
            self.send_message('Add failed', f"Channel ID `{channel_id}` is already added.", socket=self.command_socket)
            return
        elif not (channel_data := get_channel_info(channel_id)):
            self.send_message('Add failed', f"Channel ID `{channel_id}` is not a valid chzzk channel.", socket=self.command_socket)
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
        file_dir = os.path.join(self.root_path, username)

        if not os.path.isdir(file_dir):
            logger.info(f'Creating directory for {channel_id} ({username})')
            os.makedirs(file_dir)

        self.save_record_dict()

        self.send_message("Add success", f"Added `{username} ({channel_id})` to record list.", socket=self.command_socket)

        logger.info(f'Added {channel_id} to record dict')

    def remove_streamer(self, channel_id: str):
        if channel_id not in self.record_dict:
            self.send_message('Remove failed', f"Channel ID `{channel_id}` is not added.\n"
                              f"Use ',list' command to see added channel IDs.", socket=self.command_socket)
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

        self.send_message("Remove success", f"Removed `{removed_channel_data['channelName']} ({channel_id})` from record list.", socket=self.command_socket)

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

                            self.send_message('Recording done',
                                              f'Recording of `{self.record_dict[channel_id]["channelName"]}` has been stopped.\n\n'
                                              f"File path: ```{self.recorder_processes[channel_id]['path']}```\n"
                                              f"File size: {readable_size}")

                        except FileNotFoundError:
                            logger.error(f"File not found!")
                            self.send_message('Critical Error', f"Recorded file of `{self.record_dict[channel_id]['channelName']} ({channel_id})` was not found!\n"
                                                                f"Possible issue of streamlink. Check log for details.")

                        message_sent = True
                        self.recorder_processes[channel_id]['recorder'] = None
                        self.recorder_processes[channel_id]['path'] = None

                        self.recording_count -= 1

                is_streaming, stream_data = check_live(channel_id)
                if is_streaming:
                    logger.info(f"{channel_id} is online. Starting recording...")
                    username = self.record_dict[channel_id]["channelName"]
                    now = datetime.datetime.now()
                    _data = {
                        "username": self.record_dict[channel_id]["channelName"],
                        "escaped_title": truncate_long_name(escape_filename(stream_data["liveTitle"])),
                        "stream_started": datetime.datetime.strptime(
                            stream_data["openDate"], '%y-%m-%d %H:%M:%S').strftime(self.cfg['time_format']),
                        "stream_started_msg": datetime.datetime.strptime(
                            stream_data["openDate"], '%y-%m-%d %H:%M:%S').strftime(self.cfg['msg_time_format']),
                        "record_started": now.strftime(self.cfg['time_format'])
                    }
                    file_name = self.cfg['file_name_format'].format(**_data)

                    if not os.path.isdir(self.root_path):
                        logger.error("Root path does not exist!")

                        if self.cfg['fallback_to_current_dir']:
                            logger.info("Saving to current directory as fallback...")

                            if not os.path.isdir('fallback_recordings'):
                                os.mkdir('fallback_recordings')

                            if not os.path.isdir(os.path.join('fallback_recordings', username)):
                                os.mkdir(os.path.join('fallback_recordings', username))

                            file_dir = os.path.join(os.getcwd(), 'fallback_recordings', username)
                            self.send_message('Warning',
                                              f'Recording stream to fallback directory for `{username}`.\n'
                                              'Please check if default save directory is online or mounted correctly.')
                        else:
                            self.send_message('Error',
                                              f"Recording couldn't start because save directory is inaccessible!\n"
                                              'Please check if default save directory is online or mounted correctly.')
                            continue
                    else:
                        file_dir = os.path.join(self.root_path, username)

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
                                  f'https://chzzk.naver.com/{channel_id} ' \
                                  f'{self.quality} ' \
                                  f'-o "{rec_file_path}"'

                    command = shlex.split(command_string)

                    logger.info("Recorded video will be saved at %s", rec_file_path)
                    self.recorder_processes[username]['recorder'] = subprocess.Popen(command)
                    self.recorder_processes[username]['path'] = rec_file_path

                    self.recording_count += 1
                    self.send_message('Recording started',
                                      f'Started recording `{username}`.\n\n'
                                      f'Title```{stream_data["liveTitle"]}```\n'
                                      f'Stream started at```{_data["stream_started_msg"]}```\n'
                                      f'Recording started at```{now.strftime(self.cfg["msg_time_format"])}```\n'
                                      f'File path```{rec_file_path}```')
                    message_sent = True

                else:
                    logger.info(f"{channel_id} is offline.")

            logger.info(f'Check cycle complete. Starting next cycle in {str(self.refresh)} seconds.')

            if self.recording_count:
                logger.info(f'{self.recording_count} recording(s) in progress')

            if not message_sent:
                self.send_alive()

            self.loop_running = False
            time.sleep(self.refresh)


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
                'file_name_format': '[{username}]{stream_started}_{escaped_title}.ts',
                'time_format': '%y-%m-%d %H_%M_%S',
                'msg_time_format': '%y-%m-%d %H:%M:%S',
                'recording_save_root_dir': '',
                'fallback_to_current_dir': True,
                'request_retry_time': 15,
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

    MultiChzzkRecorder(args.quality, cfg)


if __name__ == "__main__":
    main()
