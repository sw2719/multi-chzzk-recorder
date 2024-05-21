# Multi Chzzk Recorder
여러 치지직 채널을 녹화할 수 있는 Python 프로그램입니다.

# 주요 기능
* 여러 치지직 채널 동시 녹화
* 디스코드 봇 기능
  - 녹화 채널 추가 및 제거
  - 녹화 시작 및 종료 알림
  - VOD 다운로드

# 사용 전 읽어주세요
* 개인적 사용 용도로 대충 쪄낸거라 작동이 불안정할 수 있습니다.
* Raspberry Pi나 NAS 등에서 24시간 작동을 목적으로 제작되었으며, 설치 및 사용에 기초적인 프로그래밍 지식이 필요합니다. 
  - PC에서 GUI를 통한 녹화를 원하시는 경우 다른 프로그램을 사용해주세요.

# 사용 방법
* streamlink 6.7.4 이상이 필요합니다.
* 최초 실행시 설정 파일이 생성되고 프로그램이 종료됩니다. 저장 디렉토리를 설정 후 다시 프로그램을 실행하세요. 디스코드 봇을 사용할 경우 봇과 관련된 항목들도 설정해야 합니다. 

# 디스코드 봇 사용 방법
* 봇을 생성하고 Server Members Intent를 활성화하세요.
* 봇을 자신의 아무 서버에 초대하고 봇의 토큰과 자신의 ID를 설정 파일에 입력하세요.
* 지원 명령어 (명령어 prefix: ,)
  - add [채널명] - 녹화 채널 추가
  - remove [채널명] - 녹화 채널 제거
  - list - 녹화 채널 목록 출력
  - dl [URL] [품질 (optional)] - VOD 다운로드 

# 설정 파일
```
"file_name_format": 파일명 포맷 (기본값: "[{username}]{stream_started}_{escaped_title}.ts")
"time_format": 파일명에 사용할 시간 포맷 (기본값: "%y-%m-%d %H_%M_%S")
"msg_time_format": 디스코드 녹화 알림에 사용할 시간 포맷 (기본값: "%y-%m-%d %H:%M:%S")
"recording_save_root_dir": 녹화 파일을 저장할 디렉토리
"fallback_to_current_dir": 저장 디렉토리를 사용할 수 없을 때 프로그램 디렉토리에 녹화 파일 저장 (기본값: true)
"interval": 생방송 확인 주기 (기본값: 10)
"use_discord_bot": 디스코드 봇 사용 여부 (기본값: false)
"zmq_port": 메인 프로세스와 디스코드 봇 간 통신을 위한 시작포트 (기본값: 5555)
"discord_bot_token": 디스코드 봇 토큰 (디스코드 봇 사용시 필요)
"target_user_id": 자신의 디스코드 ID (디스코드 봇 사용시 필요)
```
