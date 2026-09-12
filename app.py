import os
import re
import logging
from typing import Optional, Tuple

import httpx
from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("telegram-to-bale")

app = FastAPI(title="Telegram to Bale Bridge")


# =========================
# SETTINGS
# =========================

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

WEBHOOK_PATH_SECRET = os.getenv(
    "WEBHOOK_PATH_SECRET", ""
).strip()

TELEGRAM_WEBHOOK_SECRET = os.getenv(
    "TELEGRAM_WEBHOOK_SECRET", ""
).strip()


TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

BALE_API = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}"


# =========================
# SUPPORT ID REPLACEMENT
# =========================

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


def replace_support(text: Optional[str]) -> Optional[str]:

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

    lines = []

    for line in result.splitlines():

        if SUPPORT_RE.search(line):

            line = TME_RE.sub(
                BALE_SUPPORT_ID,
                line
            )

            line = USERNAME_RE.sub(
                BALE_SUPPORT_ID,
                line
            )

        lines.append(line)

    return "\n".join(lines)


# =========================
# TELEGRAM API
# =========================

async def telegram_request(method, data=None):

    async with httpx.AsyncClient(timeout=60) as client:

        response = await client.post(
            f"{TG_API}/{method}",
            json=data or {}
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):

            raise RuntimeError(
                f"Telegram error: {result}"
            )

        return result["result"]


# =========================
# BALE API
# =========================

async def bale_json(method, data):

    async with httpx.AsyncClient(timeout=120) as client:

        response = await client.post(
            f"{BALE_API}/{method}",
            json=data
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok", False):

            raise RuntimeError(
                f"Bale error: {result}"
            )

        return result


async def bale_file(
    method,
    field,
    content,
    filename,
    caption=None
):

    data = {
        "chat_id": BALE_TARGET_CHANNEL
    }

    if caption:
        data["caption"] = caption

    files = {
        field: (
            filename,
            content,
            "application/octet-stream"
        )
    }

    async with httpx.AsyncClient(timeout=180) as client:

        response = await client.post(
            f"{BALE_API}/{method}",
            data=data,
            files=files
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok", False):

            raise RuntimeError(
                f"Bale error: {result}"
            )

        return result


# =========================
# DOWNLOAD TELEGRAM FILE
# =========================

async def download_telegram_file(
    file_id: str
) -> Tuple[bytes, str]:

    info = await telegram_request(
        "getFile",
        {"file_id": file_id}
    )

    file_path = info["file_path"]

    filename = file_path.rsplit(
        "/", 1
    )[-1]

    async with httpx.AsyncClient(
        timeout=180
    ) as client:

        response = await client.get(
            f"{TG_FILE_API}/{file_path}"
        )

        response.raise_for_status()

        return response.content, filename


# =========================
# MEDIA DETECTION
# =========================

def get_media(message):

    if message.get("photo"):

        return (
            "sendPhoto",
            "photo",
            message["photo"][-1]["file_id"]
        )

    media_types = [

        ("video", "sendVideo", "video"),

        ("audio", "sendAudio", "audio"),

        ("voice", "sendVoice", "voice"),

        ("document", "sendDocument", "document"),

        ("animation", "sendAnimation", "animation"),

        ("sticker", "sendSticker", "sticker"),

        (
            "video_note",
            "sendVideoNote",
            "video_note"
        )
    ]

    for telegram_type, method, field in media_types:

        item = message.get(telegram_type)

        if item and item.get("file_id"):

            return (
                method,
                field,
                item["file_id"]
            )

    return None


# =========================
# SEND TO BALE
# =========================

async def send_to_bale(message):

    media = get_media(message)

    caption = replace_support(
        message.get("caption")
    )

    if media:

        method, field, file_id = media

        content, filename = (
            await download_telegram_file(
                file_id
            )
        )

        try:

            return await bale_file(
                method,
                field,
                content,
                filename,
                caption
            )

        except Exception:

            if method != "sendDocument":

                return await bale_file(
                    "sendDocument",
                    "document",
                    content,
                    filename,
                    caption
                )

            raise

    text = replace_support(
        message.get("text")
    )

    if text:

        return await bale_json(

            "sendMessage",

            {
                "chat_id": BALE_TARGET_CHANNEL,
                "text": text
            }
        )


# =========================
# CHECK SOURCE CHANNEL
# =========================

def correct_channel(message):

    chat = message.get(
        "chat",
        {}
    )

    username = (
        chat.get("username") or ""
    )

    return (
        username.lower().lstrip("@")
        ==
        TELEGRAM_SOURCE_CHANNEL
        .lower()
        .lstrip("@")
    )


# =========================
# HEALTH CHECK
# =========================

@app.get("/")
@app.get("/health")
async def health():

    return {
        "ok": True,
        "service": "Telegram to Bale"
    }


# =========================
# TELEGRAM WEBHOOK
# =========================

@app.post("/telegram/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request
):

    if (
        WEBHOOK_PATH_SECRET
        and secret != WEBHOOK_PATH_SECRET
    ):

        raise HTTPException(
            status_code=404
        )

    if TELEGRAM_WEBHOOK_SECRET:

        received_secret = (
            request.headers.get(
                "x-telegram-bot-api-secret-token",
                ""
            )
        )

        if (
            received_secret
            != TELEGRAM_WEBHOOK_SECRET
        ):

            raise HTTPException(
                status_code=403
            )

    update = await request.json()

    message = update.get(
        "channel_post"
    )

    if not message:

        return {
            "ok": True,
            "ignored": True
        }

    if not correct_channel(message):

        return {
            "ok": True,
            "ignored": True
        }

    await send_to_bale(message)

    return {
        "ok": True
    }


# =========================
# WEBHOOK SETUP
# =========================

@app.post("/setup-webhook")
async def setup_webhook(
    request: Request
):

    body = await request.json()

    setup_key = os.getenv(
        "SETUP_KEY",
        ""
    )

    if (
        setup_key
        and body.get("setup_key")
        != setup_key
    ):

        raise HTTPException(
            status_code=403
        )

    public_url = str(
        body.get("public_url", "")
    ).strip().rstrip("/")

    if not public_url.startswith(
        "https://"
    ):

        raise HTTPException(
            status_code=400,
            detail="public_url must use https"
        )

    payload = {

        "url":
        f"{public_url}/telegram/"
        f"{WEBHOOK_PATH_SECRET}",

        "allowed_updates": [
            "channel_post"
        ],

        "drop_pending_updates": True
    }

    if TELEGRAM_WEBHOOK_SECRET:

        payload["secret_token"] = (
            TELEGRAM_WEBHOOK_SECRET
        )

    result = await telegram_request(
        "setWebhook",
        payload
    )

    return {
        "ok": True,
        "telegram_result": result
    }
