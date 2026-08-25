ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION}

# Без этого Flow иногда падает без сообщения об ошибке (проблема с локалью)
ENV LC_ALL=C
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        gnupg \
    && add-apt-repository -y ppa:opm/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        mpi-default-bin \
        libopm-simulators-bin \
        python3-opm-common \
        python3 \
        python3-venv \
        time \
    && rm -rf /var/lib/apt/lists/*

# Виртуальное окружение: pip не трогает apt-овые пакеты.
# --system-site-packages нужен, чтобы был виден python3-opm-common.
# numpy<2 обязателен: биндинги OPM собраны под ABI numpy 1.x,
# со свежим numpy 2.x их импорт упадёт
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir "numpy<2" resdata pydantic

WORKDIR /work

# Много процессов параллельно, а не один на все ядра
ENV OMP_NUM_THREADS=2

CMD ["flow", "--version"]
