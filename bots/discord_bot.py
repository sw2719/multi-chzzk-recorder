import sys
import argparse
import asyncio
import disnake as ds
from disnake.ext import commands
import zmq

if sys.version_info[0] == 3 and sys.version_info[1] >= 8 and sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

def pprint(*args):
    print('[Discord]', *args, sep='')


class DiscordBot(commands.Bot):
    def __init__(self):
        intents = ds.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=',', intents=intents)
        self.bg_task = self.loop.create_task(self.check_loop())
        self.latest_file = {}
        self.target_user = None

        ds.Embed.set_default_color(0x73F8AA)

        self.context = zmq.Context()
        self.socket = None
        self.command_socket = None
        self.init = True

        self.command_busy = False

        self.add_commands()

    async def check_loop(self):
        no_data_counter = 0
        no_data_sent = False
        pprint('Waiting for bot to be ready...')
        while not self.is_ready():
            await asyncio.sleep(1)
        while True:
            try:
                data = self.socket.recv_json(flags=zmq.NOBLOCK)

                if data['type'] == 'alive':
                    no_data_counter = 0
                    no_data_sent = False
                    continue
                else:
                    if data['type'] == 'message':
                        title = data['title']
                        message = data['message']
                        embed = ds.Embed(title=title, description=message)
                        embed.set_author(name='치지직 레코더',
                                         icon_url='https://ssl.pstatic.net/static/nng/glive/icon/favicon.png')
                    elif data['type'] == 'embed':
                        if 'color' not in data['contents']:
                            data['contents']['color'] = 0x73F8AA
                        embed = ds.Embed.from_dict(data['contents'])
                        embed.set_author(name='치지직 레코더',
                                         icon_url='https://ssl.pstatic.net/static/nng/glive/icon/favicon.png')
                    else:
                        pprint('Invalid data type:', data['type'])
                        continue

                    while True:
                        try:
                            await self.send_message(embed)
                            break
                        except ds.HTTPException:
                            await asyncio.sleep(1)

                    no_data_counter = 0
                    no_data_sent = False

            except zmq.ZMQError:
                if no_data_counter > THRESHOLD and not no_data_sent:
                    await self.send_message(ds.Embed(
                        title='레코더가 응답하지 않음',
                        description=f'{THRESHOLD}초 이상 동안 레코더로부터 응답이 없습니다.\n'
                                    f'문제가 있는지 확인이 필요합니다.'))

                    no_data_sent = True

                no_data_counter += 1
            await asyncio.sleep(1)

    def add_commands(self):
        # Work in progress
        @self.command()
        async def add(ctx, user_input: str = ''):
            user_input = user_input.strip()

            if user_input.startswith('https://chzzk.naver.com/'):
                channel_id = user_input.split('/')[-1]
            else:
                channel_id = user_input

            if self.command_busy:
                await ctx.send('다른 명령이 이미 실행 중입니다.')
                return
            elif not user_input:
                await ctx.send('치지직 채널 링크 또는 채널 ID를 입력해야 합니다.')
                return

            self.command_socket.send_json({'type': 'add',
                                           'channel_id': channel_id})

            await self.send_result_after_command(ctx)

        @self.command()
        async def remove(ctx, channel_id: str = ''):
            channel_id = channel_id.strip()

            if self.command_busy:
                await ctx.send('다른 명령이 이미 실행 중입니다.')
                return
            elif not channel_id:
                await ctx.send('치지직 채널 ID를 입력해야 합니다.')
                return

            self.command_busy = True

            self.command_socket.send_json({'type': 'remove',
                                           'channel_id': channel_id})

            await self.send_result_after_command(ctx)

            self.command_busy = False

        @self.command(name='list')
        async def list_(ctx):
            if self.command_busy:
                await ctx.send('다른 명령이 이미 실행 중입니다.')
                return

            self.command_busy = True

            self.command_socket.send_json({'type': 'list'})
            await self.send_result_after_command(ctx)

            self.command_busy = False

    async def send_result_after_command(self, ctx):
        result = self.command_socket.recv_json()
        title = result['title']
        message = result['message']
        embed = ds.Embed(title=title, description=message)
        await ctx.send(embed=embed)

    async def send_message(self, embed):
        await self.target_user.send(embed=embed)

    async def on_ready(self):
        pprint('Logged on as ', self.user)
        self.target_user = await self.get_or_fetch_user(TARGET_USER_ID)
        pprint('Got target user: ', self.target_user.name, f'({self.target_user.id})')

        if self.init:
            self.socket = self.context.socket(zmq.PAIR)
            self.socket.bind(f"tcp://*:{PORT}")
            self.command_socket = self.context.socket(zmq.REQ)
            self.command_socket.connect(f"tcp://localhost:{PORT + 1}")
            self.init = False

            self.socket.send_string('ready')
            self.command_socket.send_string('ready')
            self.command_socket.recv_string()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--token", help="Discord bot token", required=True)
    parser.add_argument("-u", "--target", help="Target user ID", required=True)
    parser.add_argument("-p", "--port", help="ZMQ port", required=True)
    parser.add_argument("-i", "--interval", help="Twitch recorder interval", required=True)
    args = parser.parse_args()
    TOKEN = args.token
    TARGET_USER_ID = int(args.target)
    PORT = int(args.port)
    THRESHOLD = int(args.interval) + 30

    bot = DiscordBot()
    bot.run(TOKEN)
