FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Seoul MOA_DATA_DIR=/data HOME=/tmp
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd -g 10001 moa && useradd -u 10001 -g moa -M moa
COPY moa ./moa
USER 10001:10001
ENTRYPOINT ["python", "-m", "moa"]
CMD ["schedule"]
