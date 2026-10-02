"""Choose an installed Windows speech voice that matches the reply language."""
import unicodedata


_SCRIPT_LANGUAGES = (
    ("ARABIC", ("ar", "fa", "ur")),
    ("ARMENIAN", ("hy",)),
    ("BENGALI", ("bn", "as")),
    ("CYRILLIC", ("ru", "uk", "bg", "sr", "mk", "be", "kk")),
    ("DEVANAGARI", ("hi", "mr", "ne", "sa")),
    ("GEORGIAN", ("ka",)),
    ("GREEK", ("el",)),
    ("GUJARATI", ("gu",)),
    ("GURMUKHI", ("pa",)),
    ("HANGUL", ("ko",)),
    ("HEBREW", ("he",)),
    ("HIRAGANA", ("ja",)),
    ("KATAKANA", ("ja",)),
    ("CJK UNIFIED IDEOGRAPH", ("zh", "ja")),
    ("MALAYALAM", ("ml",)),
    ("ORIYA", ("or",)),
    ("SINHALA", ("si",)),
    ("TAMIL", ("ta",)),
    ("TELUGU", ("te",)),
    ("THAI", ("th",)),
)


def language_preferences(text):
    """Return likely language codes, prioritizing the most-used non-Latin script."""
    scores = {}
    for character in str(text or ""):
        if not unicodedata.category(character).startswith("L"):
            continue
        name = unicodedata.name(character, "")
        if name.startswith("LATIN "):
            continue
        languages = next(
            (codes for script, codes in _SCRIPT_LANGUAGES if name.startswith(script)),
            (),
        )
        for language in languages:
            scores[language] = scores.get(language, 0) + 1
    return [language for language, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def _voice_languages(voice):
    languages = getattr(voice, "languages", ()) or ()
    if isinstance(languages, (str, bytes)):
        languages = (languages,)
    result = set()
    for language in languages:
        if isinstance(language, bytes):
            language = language.decode("utf-8", "ignore")
        normalized = str(language).lower().replace("_", "-").strip()
        code = normalized.split("-")[0]
        if code:
            result.add(code)
    return result


def choose_voice(voices, text, preferred_names=()):
    """Choose a script-matched voice first, then a named/default fallback."""
    voices = list(voices or ())
    for language in language_preferences(text):
        voice = next((item for item in voices if language in _voice_languages(item)), None)
        if voice is not None:
            return voice
    for preferred_name in preferred_names:
        preferred_name = str(preferred_name).lower()
        voice = next((item for item in voices if preferred_name in str(item.name).lower()), None)
        if voice is not None:
            return voice
    return voices[1] if len(voices) > 1 else (voices[0] if voices else None)
