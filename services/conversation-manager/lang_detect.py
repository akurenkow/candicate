"""Language detection on normalized text. Returns ISO 639-1 code."""

import logging

from config import MODEL_WORKING_LANGUAGE

logger = logging.getLogger(__name__)

MIN_TEXT_LEN = 3


def detect(normalized_text: str) -> str:
    """
    Detect language of normalized text. Returns two-letter code (e.g. 'en', 'ru').
    Short or empty text returns MODEL_WORKING_LANGUAGE (no translation).
    """
    text = (normalized_text or "").strip()
    if len(text) < MIN_TEXT_LEN:
        return MODEL_WORKING_LANGUAGE
    try:
        import langdetect
        langdetect.DetectorFactory.seed = 0
        return langdetect.detect(text) or MODEL_WORKING_LANGUAGE
    except Exception as e:
        logger.warning("lang_detect failed: %s, assuming %s", e, MODEL_WORKING_LANGUAGE)
        return MODEL_WORKING_LANGUAGE
