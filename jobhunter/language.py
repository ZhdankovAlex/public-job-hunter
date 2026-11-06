"""Языковой фильтр: нужен ли местный язык (свои языки по умолчанию — английский и русский)."""
import re
from collections import Counter

MUST_MARK = "🚫 нужен местный язык (must-have)"
NICE_MARK = "🗣️ местный язык желателен"
RUSSIAN_MARK = "🇷🇺 русский — плюс/требование"

# Частые служебные слова — простой детектор языка текста
_STOPWORDS = {
    "en": "the and of to with for you we our is are will on as in",
    "de": "und der die das mit für wir ist nicht eine oder auch bei sowie deine unser werden sind",
    "fr": "les des vous nous pour dans une est avec votre notre sur et qui",
    "es": "los las para con una que por del nuestro nuestra tu experiencia el",
    "nl": "het een van voor met je wij onze bij zijn naar ook aan wordt",
    "pt": "os para com uma você nosso nossa que por não são em experiência do da",
    "it": "di il per con una che del della nostro sei gli nel sono",
}
_STOPWORDS = {lang: set(words.split()) for lang, words in _STOPWORDS.items()}
_GERMAN_TITLE_RX = re.compile(r"\((?:m/w/d|w/m/d|m/f/d|f/m/d|m/w/x|all genders)\)", re.I)

_LANG = (
    r"(?:\bGerman\b|\bDeutsch\w*|\bSpanish\b|\bEspañol\b|\bCastellano\b|\bDutch\b|\bNederlands\b|"
    r"\bFrench\b|\bFrançais\b|\bFranzösisch\w*|\bPortuguese\b|\bPortuguês\b|\bItalian\b|\bItaliano\b|"
    r"\bGeorgian\b|\bGreek\b)"
)
_MUST = (
    r"(?:fluen\w+|business[\s-]level|proficien\w+|native|\bC1\b|\bC2\b|required|requirement|mandatory|"
    r"is a must|essential|excellent|very good|strong|verhandlungssicher|fließend\w*|sehr gute\w*|"
    r"muttersprach\w*|imprescindible|courant)"
)
_MUST_RX = re.compile(rf"{_MUST}\W+(?:[\w/]+\W+){{0,5}}?{_LANG}|{_LANG}\W+(?:[\w/]+\W+){{0,5}}?{_MUST}", re.I)
_NICE_RX = re.compile(
    r"\bplus\b|nice[\s-]to[\s-]have|advantage|beneficial|bonus|desirable|preferred|optional|"
    r"von vorteil|wünschenswert|willing(?:ness)? to learn|beginner|basic|not (?:a )?require",
    re.I,
)
_LANG_RX = re.compile(_LANG, re.I)
# "fluent in English or German" — достаточно английского
_EITHER_RX = re.compile(rf"English\W+(?:\w+\W+){{0,2}}or\W+(?:\w+\W+){{0,2}}{_LANG}|{_LANG}\W+(?:\w+\W+){{0,2}}or\W+(?:\w+\W+){{0,2}}English", re.I)
_RUSSIAN_RX = re.compile(
    r"Russian[\s-]speak\w*|\bRussian\b\W+(?:\w+\W+){0,4}?(?:required|is a plus|a plus|native|fluen\w+|language)|"
    r"(?:fluen\w+|native|speak\w*|knowledge of)\W+(?:\w+\W+){0,3}?\bRussian\b|"
    r"русскоязычн|знание русского|русский язык",
    re.I,
)
_SENTENCE_SPLIT_RX = re.compile(r"[\n.!?;•]+")


def detect_language(text):
    """Код языка текста ('en', 'de', ...) по частым словам; '' если текста мало."""
    counts = Counter()
    for word in re.findall(r"[a-zà-ÿ]+", text.lower()):
        for lang, words in _STOPWORDS.items():
            if word in words:
                counts[lang] += 1
    if not counts:
        return ""
    lang, hits = counts.most_common(1)[0]
    if lang != "en" and hits >= 10 and hits > counts["en"]:
        return lang
    return "en" if counts["en"] >= 10 else ""


def analyze(title, text):
    """-> (пометки, нужен_ли_местный_язык)."""
    marks, must, nice = [], False, False
    lang = detect_language(text)
    if lang not in ("", "en") or (not lang and _GERMAN_TITLE_RX.search(title)):
        marks.append(f"🗣️ вакансия не на английском ({lang or 'de'})")
    for sentence in _SENTENCE_SPLIT_RX.split(text):
        if not _LANG_RX.search(sentence):
            continue
        if _NICE_RX.search(sentence) or _EITHER_RX.search(sentence):
            nice = True
        elif _MUST_RX.search(sentence):
            must = True
    if must:
        marks.append(MUST_MARK)
    elif nice:
        marks.append(NICE_MARK)
    if _RUSSIAN_RX.search(f"{title}\n{text}"):
        marks.append(RUSSIAN_MARK)
    return marks, must
