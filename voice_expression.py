"""Small, offline-safe delivery cues shared by Maanav's speech backends."""
import re


_EMPATHY = re.compile(
    r"\b(sorry|tough|hard time|i understand|that sounds|i'm here|you've got this|"
    r"you have got this|take your time|no pressure|it's okay|it is okay|i hear you|"
    r"that must be|i'm with you|we can work through)\b", re.I
)
_CELEBRATION = re.compile(
    r"\b(congratulations|congrats|well done|awesome|amazing|fantastic|you did it|"
    r"great job|that's exciting|that is exciting|brilliant|proud of you|nailed it|"
    r"incredible|what a win)\b", re.I
)
_PLAYFUL = re.compile(
    r"\b(fun|playful|silly|little adventure|let's play|lets play|ha ha|haha|"
    r"nice one|plot twist|cheeky)\b", re.I
)
_CONCERN = re.compile(
    r"\b(be careful|urgent|emergency|danger|unsafe|worried|warning|critical|"
    r"important to act|please stop)\b", re.I
)
_CONFIDENT = re.compile(
    r"\b(i can do that|i'll handle|i will handle|here's the plan|here is the plan|"
    r"step by step|first,|the answer is|you can count on)\b", re.I
)


def delivery_for(text):
    """Pick a human-sounding emotional intention without changing the words."""
    value = str(text or "").strip()
    if _EMPATHY.search(value):
        return "reassuring"
    if _CONCERN.search(value):
        return "serious"
    if _PLAYFUL.search(value):
        return "playful"
    if _CELEBRATION.search(value) or ("!" in value and "?" not in value):
        return "upbeat"
    if _CONFIDENT.search(value):
        return "confident"
    if value.endswith("?"):
        return "curious"
    return "warm"


PIPER_PROFILES = {
    # Piper has no direct emotion controls; profiles add restrained rhythm changes.
    "warm":       {"noise_scale": 0.72, "noise_w_scale": 0.88, "length_scale": 0.96},
    "reassuring": {"noise_scale": 0.66, "noise_w_scale": 0.82, "length_scale": 1.04},
    "upbeat":     {"noise_scale": 0.76, "noise_w_scale": 0.94, "length_scale": 0.91},
    "curious":    {"noise_scale": 0.74, "noise_w_scale": 0.91, "length_scale": 0.97},
    "playful":    {"noise_scale": 0.78, "noise_w_scale": 0.96, "length_scale": 0.92},
    "serious":    {"noise_scale": 0.64, "noise_w_scale": 0.82, "length_scale": 1.03},
    "confident":  {"noise_scale": 0.72, "noise_w_scale": 0.88, "length_scale": 0.94},
}

PYTTSX3_RATES = {
    "warm": 152, "reassuring": 143, "upbeat": 164, "curious": 150,
    "playful": 160, "serious": 140, "confident": 154,
}
