FROM python:3-alpine

WORKDIR /ytmlm
COPY requirements.txt .
RUN apk add ffmpeg
RUN pip install -r requirements.txt
COPY ytmlm.py .
ENTRYPOINT [ "python3", "ytmlm.py" ]
