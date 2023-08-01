
FROM python:3.10.11-slim-bullseye

LABEL maintainer="brentonk@diversityarrays.com"

RUN apt-get update && \
    apt-get install -y \
        build-essential gcc locales wget build-essential zlib1g-dev \
        libssl-dev libncurses-dev libffi-dev libsqlite3-dev \
        libreadline-dev libbz2-dev libpq-dev

# Add the Python requirements.txt
ADD ./requirements.txt /tmp/requirements.txt

RUN python -m pip install -r /tmp/requirements.txt --no-cache-dir

RUN apt-get remove -y --purge libgdal-dev gcc build-essential && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN dpkg-reconfigure locales && \
    locale-gen C.UTF-8 && \
    /usr/sbin/update-locale LANG=C.UTF-8

# Add the application source code to the image
ADD ./src /src

WORKDIR /src

EXPOSE 5000

CMD [ "python", "-m", "flask", "run", "--host", "0.0.0.0" ]
