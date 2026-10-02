"""Provider fallback policy, kept independent from the desktop UI."""


def route_message(
    message,
    *,
    image_path=None,
    history=None,
    memory_text="",
    offline=False,
    openai_provider=None,
    gemini_provider=None,
    local_provider,
    local_model="local model",
):
    """Try enabled providers in order and return a user-safe fallback message.

    Provider callbacks return a non-empty string on success and ``None`` when
    unavailable. Exceptions are isolated so one provider cannot prevent fallback.
    """
    user_message = message or "Please describe the attached image."
    history = list(history or [])

    if not offline:
        if openai_provider is not None:
            try:
                answer = openai_provider(
                    user_message,
                    history=history,
                    image_path=image_path,
                    memory_text=memory_text,
                )
                if isinstance(answer, str) and answer.strip():
                    return answer
            except Exception as error:
                print("OpenAI provider failed; trying Gemini:", error)

        if gemini_provider is not None:
            try:
                answer = gemini_provider(user_message, image_path=image_path)
                if isinstance(answer, str) and answer.strip():
                    return answer
            except Exception as error:
                print("Gemini provider failed; trying local AI:", error)

    if image_path:
        if offline:
            return (
                "Image analysis needs an online image-capable AI. Offline mode kept "
                "the image on this computer and did not send it anywhere."
            )
        return "I couldn't reach an image-capable AI service. Check your internet/API key, then try again."

    try:
        answer = local_provider(user_message, history=history, memory_text=memory_text)
        if isinstance(answer, str) and answer.strip():
            return answer
    except Exception as error:
        print("Local Ollama unavailable:", error)

    return (
        "I couldn't reach the cloud providers or the local Ollama model. "
        "Check your internet/API key, or start Ollama and make sure the "
        f"'{local_model}' model is installed. Your message is still in the chat; please retry."
    )
