import os
import time
from google import genai
from google.genai import types
from provider_timeouts import provider_timeout_seconds

MODEL = "gemini-3.8-flash"


def _temporary_server_failure(error):
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    if code in {500, 502, 503, 504}:
        return True
    message = str(error).upper()
    return any(token in message for token in ("500 ", "502 ", "503 ", "504 ", "UNAVAILABLE", "INTERNAL"))


def ask_gemini(message, image_path=None):

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        print("Gemini API key was not found; trying local fallback.")
        return None

    client = None
    try:
        timeout_ms = int(provider_timeout_seconds("MAANAV_GEMINI_TIMEOUT", 30) * 1000)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )

        contents = message
        if image_path:
            with open(image_path, "rb") as image_file:
                image_bytes = image_file.read()
            import mimetypes
            mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
            contents = [
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                message or "Describe this image.",
            ]
        for attempt in range(2):
            try:
                chat = client.chats.create(model=MODEL)
                response = chat.send_message(contents)
                return response.text
            except Exception as error:
                if attempt == 0 and _temporary_server_failure(error):
                    print("Gemini service is temporarily busy; retrying once.")
                    time.sleep(0.8)
                    continue
                print("Gemini error:", error)
                return None

    except Exception as error:
        print("Gemini error:", error)
        return None
    finally:
        if client is not None:
            try:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
            except Exception as error:
                print("Gemini client cleanup failed:", error)
