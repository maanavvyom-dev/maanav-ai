"""Route microphone transcription through local speech first, with clear offline states."""


def recognize_audio(audio, *, offline_transcriber, online_transcriber, offline_only,
                    local_model_ready, set_status, request_error, unknown_value_error):
    """Prefer local transcription and only call online recognition when permitted."""
    offline_error = None
    try:
        text = offline_transcriber(audio)
        if text:
            return str(text).lower().strip()
    except Exception as error:
        offline_error = error

    if offline_only:
        if not local_model_ready():
            set_status("OFFLINE SPEECH MODEL NOT READY")
        elif offline_error is not None:
            print("Offline speech recognition failed:", offline_error)
            set_status("OFFLINE RECOGNITION ERROR · RETRYING")
        else:
            set_status("NO SPEECH DETECTED · TRY AGAIN")
        return None

    if offline_error is not None:
        print("Offline speech recognition unavailable; trying online speech:", offline_error)
    try:
        text = online_transcriber(audio)
        return str(text).lower().strip() if text else None
    except unknown_value_error:
        raise
    except request_error as error:
        print("Online speech recognition unavailable:", error)
        set_status("SPEECH · INTERNET OR LOCAL MODEL NEEDED")
        return None
