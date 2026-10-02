import base64
import mimetypes
from personality import build_system_prompt
from provider_timeouts import provider_timeout_seconds
from credentials import get_openai_api_key

MODEL = "gpt-6-astra"


def ask_openai(message, history=None, image_path=None, memory_text=""):
    api_key = get_openai_api_key()
    if not api_key:
        return None
    client = None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=provider_timeout_seconds("MAANAV_OPENAI_TIMEOUT", 45))
        prior = list(history or [])
        if image_path:
            mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
            with open(image_path, "rb") as image_file:
                encoded = base64.b64encode(image_file.read()).decode("ascii")
            current_content = [
                {"type": "input_text", "text": message or "Describe this image."},
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": "auto",
                },
            ]
        else:
            current_content = message
        prior.append({"role": "user", "content": current_content})
        response = client.responses.create(
            model=MODEL,
            instructions=build_system_prompt(memory_text),
            input=prior,
        )
        return response.output_text
    except Exception as error:
        print("GPT-6 Astra unavailable; trying Gemini:", error)
        return None
    finally:
        if client is not None:
            try:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
            except Exception as error:
                print("OpenAI client cleanup failed:", error)
