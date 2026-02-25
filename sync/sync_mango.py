import os
import ftplib
import json
import schedule
import time
from datetime import datetime
import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FTP_HOST = os.getenv("MANGO_FTP_HOST")
FTP_USER = os.getenv("MANGO_FTP_USER")
FTP_PASSWORD = os.getenv("MANGO_FTP_PASSWORD")
CALL_ANALYTICS_URL = os.getenv("CALL_ANALYTICS_URL")
SYNC_TIME = os.getenv("SYNC_TIME", "06:00")
SYNC_DIR = "/app/data/mango_sync"

def sync_mango_ftp():
    """Синхронизирует файлы с МАНГО FTP"""
    logger.info(f"Starting MANGO FTP sync at {datetime.now()}...")

    try:
        ftp = ftplib.FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASSWORD)
        ftp.cwd("/")

        files = ftp.nlst()
        logger.info(f"Found {len(files)} files on MANGO FTP")

        for filename in files:
            if not filename.lower().endswith(('.mp3', '.wav', '.ogg', '.m4a')):
                continue

            local_path = os.path.join(SYNC_DIR, filename)

            if os.path.exists(local_path):
                logger.info(f"✓ {filename} already processed, skipping")
                continue

            logger.info(f"⬇️ Downloading {filename}...")
            with open(local_path, 'wb') as f:
                ftp.retrbinary(f'RETR {filename}', f.write)

            # Парсим оператора из имени файла
            operator = parse_operator(filename)

            # Загружаем в call-analytics
            upload_file(local_path, operator)

        ftp.quit()
        logger.info("✅ MANGO FTP sync completed")

    except Exception as e:
        logger.error(f"❌ Sync error: {e}")

def parse_operator(filename):
    """Парсит имя оператора из имени файла"""
    # Примеры: call_ivan_2026-02-25.mp3 → Ivan
    parts = filename.replace('.mp3', '').replace('.wav', '').split('_')
    if len(parts) >= 2:
        return parts[1].title()
    return "Unknown"

def upload_file(filepath, operator):
    """Загружает файл в call-analytics"""
    try:
        with open(filepath, 'rb') as f:
            files = {'files': f}
            data = {'operator_name': operator}

            response = requests.post(CALL_ANALYTICS_URL, files=files, data=data, timeout=30)

            if response.status_code == 200:
                logger.info(f"✅ Uploaded {filepath} as {operator}")
            else:
                logger.error(f"❌ Upload failed: {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Upload error for {filepath}: {e}")

if __name__ == "__main__":
    logger.info("MANGO FTP Sync Service Started")
    logger.info(f"Scheduled sync time: {SYNC_TIME}")

    # Запускать в определённое время каждый день
    schedule.every().day.at(SYNC_TIME).do(sync_mango_ftp)

    # Также синхронизировать каждые 6 часов для надёжности
    schedule.every(6).hours.do(sync_mango_ftp)

    while True:
        schedule.run_pending()
        time.sleep(60)
