# Multi Chzzk Recorder
여러 치지직 채널을 녹화할 수 있는 Python 스크립트입니다.

# 주요 기능
* 여러 치지직 채널 동시 녹화
* 디스코드 봇 기능
  - 녹화 채널 추가 및 제거
  - 녹화 시작 및 종료 알림
  - 다시보기 다운로드

# 사용 전 읽어주세요
* 개인적 사용 용도로 대충 급하게 쪄낸거라 작동이 불안정할 수 있습니다.
* Raspberry Pi나 NAS 등에서 24시간 작동을 목적으로 제작되었으며, 설치 및 사용에 기초적인 프로그래밍 지식이 필요합니다.

# 사용 방법
* 최초 clone시 `git submodule update --init --recursive` 명령을 실행하세요.
* requirements.txt로 의존성을 설치하세요.
* 최초 실행시 설정 파일이 생성되고 프로그램이 종료됩니다. NID_SES, NID_AUT, 저장 디렉토리를 설정 후 다시 프로그램을 실행하세요.
* 모든 기능 (채널 추가/제거, 알림, 다시보기 다운로드 등)을 사용하려면 디스코드 봇 설정이 필요합니다.

# 디스코드 봇 사용 방법
* 봇을 생성하고 Server Members Intent를 활성화하세요.
* 봇을 자신의 (또는 봇 사용자가 있는) 아무 서버에 초대하고 봇 토큰을 설정 파일에 입력하세요. 소유자와 실 사용자가 다른 경우 유저 ID를 입력해야 합니다.
* 지원 명령어 (명령어 prefix: ,)
  - ,add [채널명] - 채널명을 통한 녹화 채널 추가
  - ,add_id [체널 ID 또는 URL] - 채널 ID를 통한 녹화 채널 추가
  - ,remove [채널명 또는 채널 ID] - 녹화 채널 제거
    - 제거한 채널이 녹화중일 경우 녹화가 중단됩니다.
  - ,list - 녹화 채널 목록 출력
  - ,list_id - 채널 ID를 포함한 녹화 채널 목록 출력
  - ,dl [다시보기 URL] [품질 (optional)] - 다시보기 다운로드

# 설정 파일
필수로 설정이 필요한 항목: nid_ses, nid_aut, recording_save_root_dir

* `nid_ses`: 네이버 쿠키값
* `nid_aut`: 네이버 쿠키값
* `recording_save_root_dir`: 녹화 파일을 저장할 디렉토리
* `quality`: 녹화 품질
  - 기본값: best
* `record_chat`: 채팅 기록 여부
  - 기본값: `false`
* `file_name_format`: 실시간 녹화 파일명 포맷 
  - 기본값: `[{username}]{stream_started}_{escaped_title}.ts`
  - 사용 가능 변수 (syntax: `{변수명}`): 
    - `username`: 채널 사용자명
    - `stream_started`: 방송 시작 시각
    - `record_started`: 녹화 시작 시각
    - `escaped_title`: 녹화 시작 시점의 방송 제목
* `vod_name_format`: VOD 다운로드 파일명 포맷 
  - 기본값: `[{username}]{stream_started}_{escaped_title}.ts`
  - 사용 가능 변수 (syntax: `{변수명}`): 
    - `username`: 채널 사용자명
    - `stream_started`: 방송 시작 시각
    - `download_started`: 다운로드 시작 시각
    - `uploaded`: 업로드 시각
    - `escaped_title`: 다시보기 제목
* `time_format`: 파일명에 사용할 시간 포맷 
  - 기본값: `%y-%m-%d %H_%M_%S`
* `msg_time_format`: 디스코드 알림에 사용할 시간 포맷 
  - 기본값: `%Y년 %m월 %d일 %H시 %M분 %S초`
* `fallback_to_current_dir`: 저장 디렉토리를 사용할 수 없을 때 프로그램 디렉토리에 녹화 파일 저장 
  - 기본값: `true`
* `mount_command`: 저장 디렉토리를 사용할 수 없을 때 실행할 명령어 
  - 기본값: 없음
* `interval`: 방송 중 채널 확인 주기 (초 단위)
  - 기본값: 10
* `use_discord_bot`: 디스코드 봇 사용 여부 
  - 기본값: `false`
* `zmq_port`: 메인 프로세스와 디스코드 봇 간 통신을 위한 시작포트 
  - 기본값: `5555`
* `discord_bot_token`: 디스코드 봇 토큰 
  - 기본값: 없음 (디스코드 봇 사용시 필수)
* `target_user_id`: 봇 명령어를 사용할 디스코드 유저 ID
  - 기본값: 없음 (빈 칸인 경우 봇의 소유자 ID로 자동 설정)

