FROM python:3.11.13-slim
LABEL authors="sw2719"

ENV \
    NID_AUT="" \
    NID_SES="" \
    RECORDING_SAVE_ROOT_DIR="/recording" \
    QUALITY=best \
    RECORD_CHAT=1 \
    FILE_NAME_FORMAT="[{username}]{stream_started}_{escaped_title}.ts" \
    VOD_NAME_FORMAT="[{username}]{stream_started}_{escaped_title}.mp4" \
    TIME_FORMAT="%y-%m-%d %H_%M_%S" \
    MSG_TIME_FORMAT="%Y년 %m월 %d일 %H시 %M분 %S초" \
    INTERVAL=10 \
    USE_DISCORD_BOT=0 \
    ZMQ_PORT=5555 \
    DISCORD_BOT_TOKEN="" \
    TARGET_USER_ID="" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul \
    RUN_IN_CONTAINER=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p ${RECORDING_SAVE_ROOT_DIR} && \
    chmod -R 777 ${RECORDING_SAVE_ROOT_DIR}

# Run
CMD ["python", "multi_chzzk_recorder.py"]