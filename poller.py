import os
import re
import json
import time
from pathlib import Path

import requests


TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
BALE_BOT_TOKEN = os.environ["BALE_BOT_TOKEN"].strip()

TELEGRAM_SOURCE_CHANNEL = os.getenv(
    "TELEGRAM_SOURCE_CHANNEL",
    "@Nargesacademy8"
).strip()

BALE_TARGET_CHANNEL = os.getenv(
    "BALE_TARGET_CHANNEL",
    "@NargesAcademy"
).strip()

BALE_SUPPORT_ID = os.getenv(
    "BALE_SUPPORT_ID",
    "@poshtibani_ghasemi"
).strip()

OLD_SUPPORT_IDS = [
    x.strip()
    for x in os.getenv("OLD_SUPPORT_IDS", "").split(",")
    if x.strip()
]

RUN_SECONDS = int(os.getenv("RUN_SECONDS", "260"))

STATE_FILE = Path("telegram_offset.json")

TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

BALE_API = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}"


SUPPORT_RE = re.compile(
    r"(پشتیبان(?:ی)?|ادمین|support|admin)",
    re.IGNORECASE
)

USERNAME_RE = re.compile(
    r"(?<![\w@])@[A-Za-z0-9_]{4,}"
)

TME_RE = re.compile(
    r"https?://(?:www\.)?(?:t\.me|telegram\.me)/[A-Za-z0-9_]+/?",
    re.IGNORECASE
)


def replace_support(text):

    if not text:
        return text

    result = text

    for old in OLD_SUPPORT_IDS:

        username = old.lstrip("@")

        result = re.sub(
            re.escape(old),
            BALE_SUPPORT_ID,
            result,
            flags=re.IGNORECASE
        )

        result = re.sub(
            rf"https?://(?:www\.)?(?:t\.me|telegram\.me)/"
            rf"{re.escape(username)}/?",
            BALE_SUPPORT_ID,
            result,
            flags=re.IGNORECASE
        )

    lines = result.splitlines()

    new_lines = []

    for line in lines:

        if SUPPORT_RE.search(line):

            line = TME_RE.sub(
                BALE_SUPPORT_ID,
                line
            )

            line = USERNAME_RE.sub(
                BALE_SUPPORT_ID,
                line
            )

        new_lines.append(line)

    return "\n".join(new_lines)


def load_offset():

    if not STATE_FILE.exists():
        return 0

    try:

        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return int(
            data.get("offset", 0)
        )

    except Exception:

        return 0


def save_offset(offset):

    STATE_FILE.write_text(
        json.dumps(
            {"offset": offset},
            ensure_ascii=False
        ),
        encoding="utf-8"
    )


def telegram_api(method, payload=None, timeout=60):

    response = requests.post(
        f"{TG_API}/{method}",
        json=payload or {},
        timeout=timeout
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data["result"]


def bale_json(method, payload):

    response = requests.post(
        f"{BALE_API}/{method}",
        json=payload,
        timeout=120
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok", False):
        raise RuntimeError(
            f"Bale API error: {data}"
        )

    return data


def bale_upload(
    method,
    field_name,
    file_bytes,
    filename,
    caption=None
):

    data = {
        "chat_id": BALE_TARGET_CHANNEL
    }

    if caption:
        data["caption"] = caption

    files = {
        field_name: (
            filename,
            file_bytes,
            "application/octet-stream"
        )
    }

    response = requests.post(
        f"{BALE_API}/{method}",
        data=data,
        files=files,
        timeout=180
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok", False):
        raise RuntimeError(
            f"Bale API error: {result}"
        )

    return result


def download_telegram_file(file_id):

    info = telegram_api(
        "getFile",
        {"file_id": file_id}
    )

    file_path = info["file_path"]

    filename = (
        file_path.rsplit("/", 1)[-1]
        or "telegram_file"
    )

    response = requests.get(
        f"{TG_FILE_API}/{file_path}",
        timeout=180
    )

    response.raise_for_status()

    return response.content, filename


def extract_media(message):

    if message.get("photo"):

        return (
            "sendPhoto",
            "photo",
            message["photo"][-1]["file_id"]
        )

    mappings = [
        ("video", "sendVideo", "video"),
        ("audio", "sendAudio", "audio"),
        ("voice", "sendVoice", "voice"),
        ("document", "sendDocument", "document"),
        ("animation", "sendAnimation", "animation"),
        ("sticker", "sendSticker", "sticker"),
        ("video_note", "sendVideoNote", "video_note"),
    ]

    for tg_key, bale_method, bale_field in mappings:

        obj = message.get(tg_key)

        if obj and obj.get("file_id"):

            return (
                bale_method,
                bale_field,
                obj["file_id"]
            )

    return None


def correct_source_channel(message):

    chat = message.get(
        "chat",
        {}
    )

    username = (
        chat.get("username")
        or ""
    )

    return (
        username.lower().lstrip("@")
        ==
        TELEGRAM_SOURCE_CHANNEL
        .lower()
        .lstrip("@")
    )


def send_to_bale(message):

    text = replace_support(
        message.get("text")
    )

    caption = replace_support(
        message.get("caption")
    )

    media = extract_media(message)

    if media:

        method, field, file_id = media

        content, filename = (
            download_telegram_file(
                file_id
            )
        )

        try:

            return bale_upload(
                method,
                field,
                content,
                filename,
                caption
            )

        except Exception as error:

            print(
                f"{method} failed: {error}"
            )

            if method != "sendDocument":

                return bale_upload(
                    "sendDocument",
                    "document",
                    content,
                    filename,
                    caption
                )

            raise

    if text:

        return bale_json(
            "sendMessage",
            {
                "chat_id": BALE_TARGET_CHANNEL,
                "text": text
            }
        )


def delete_old_webhook():

    try:

        telegram_api(
            "deleteWebhook",
            {
                "drop_pending_updates": False
            }
        )

    except Exception as error:

        print(
            f"deleteWebhook warning: {error}"
        )


def main():

    print(
        "Telegram -> Bale poller started"
    )

    delete_old_webhook()

    offset = load_offset()

    started_at = time.time()

    while (
        time.time() - started_at
        < RUN_SECONDS
    ):

        try:

            updates = telegram_api(
                "getUpdates",
                {
                    "offset": offset,
                    "limit": 100,
                    "timeout": 45,
                    "allowed_updates": [
                        "channel_post"
                    ]
                },
                timeout=55
            )

            for update in updates:

                update_id = update[
                    "update_id"
                ]

                next_offset = (
                    update_id + 1
                )

                message = update.get(
                    "channel_post"
                )

                if message:

                    if correct_source_channel(
                        message
                    ):

                        try:

                            send_to_bale(
                                message
                            )

                            print(
                                "Forwarded Telegram "
                                f"message {message.get('message_id')}"
                            )

                        except Exception as error:

                            print(
                                "Forward error:",
                                error
                            )

                            raise

                offset = next_offset

                save_offset(
                    offset
                )

        except requests.exceptions.Timeout:

            pass

        except Exception as error:

            print(
                "Polling error:",
                error
            )

            time.sleep(5)

    print(
        "Poller finished normally"
    )


if __name__ == "__main__":
    main()
