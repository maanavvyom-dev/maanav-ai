# =========================================================
# MAANAV AI - INTENT ROUTER
# =========================================================

def classify(message):
    """
    Fast local intent classification.
    Returns the subsystem that should handle the request.
    """

    text = message.lower().strip()

    # -----------------------------
    # SYSTEM
    # -----------------------------
    system_words = [
        "shutdown",
        "shut down",
        "restart",
        "reboot",
        "volume",
        "mute",
        "unmute",
        "battery",
        "lock my pc",
        "lock pc",
        "lock laptop",
        "lock my laptop",
        "lock computer",
        "cancel shutdown",
        "cancel restart",
        "wifi",
        "wi-fi",
        "wireless",
    ]

    if any(word in text for word in system_words):
        return "system"

    # -----------------------------
    # TIME / DATE (local, no AI required)
    # -----------------------------
    if text in {
        "time", "what time is it", "what time is it now", "what's the time",
        "tell me the time", "what is today's date", "what's today's date",
        "what is the date", "what's the date", "today's date", "date today",
        "what is the current date", "what's the current date", "current date",
        "what day is it", "what day is it today", "what is today", "what day is today",
    }:
        return "time"

    # Treat common folder destinations as file tools before the generic
    # open/launch verb so they never fall through to an app or cloud model.
    if any(text == phrase for phrase in {
        "open desktop", "open the desktop", "open downloads", "open download",
        "open the downloads", "open the download", "open downloads folder",
        "open the downloads folder", "open documents", "open document",
        "open the documents", "open the documents folder", "open pictures",
        "open picture", "open the pictures", "open music", "open the music",
        "open videos", "open video", "open the videos",
    }):
        return "files"

    # -----------------------------
    # APP / PROGRAM
    # -----------------------------
    app_words = [
        "open ",
        "launch ",
        "start ",
        "run ",
    ]

    if any(text.startswith(word) for word in app_words):
        return "app"

    # -----------------------------
    # WEBSITE
    # -----------------------------
    website_words = [
        "youtube",
        "google",
        "gmail",
        "chatgpt",
        "website",
        "web site",
        "open browser",
    ]

    if any(word in text for word in website_words):
        return "web"

    # -----------------------------
    # FILES
    # -----------------------------
    file_words = [
        "file",
        "files",
        "folder",
        "folders",
        "download",
        "downloads",
        "desktop",
        "pdf",
        "docx",
        "txt",
        "jpg",
        "png",
    ]

    if any(word in text for word in file_words):
        return "files"

    # -----------------------------
    # CODE
    # -----------------------------
    code_words = [
        "code",
        "coding",
        "python",
        "program",
        "programming",
        "debug",
        "error in my code",
        "fix my code",
    ]

    if any(word in text for word in code_words):
        return "code"

    # -----------------------------
    # DEFAULT
    # -----------------------------
    return "chat"


if __name__ == "__main__":
    tests = [
        "open chrome",
        "turn up volume",
        "find my pdf",
        "fix my python code",
        "what is artificial intelligence?",
    ]

    for test in tests:
        print(f"{test} -> {classify(test)}")
