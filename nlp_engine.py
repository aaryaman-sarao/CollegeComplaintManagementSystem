"""
nlp_engine.py
-------------
NLP Automated Triage Engine for the Smart College Complaint
Management System.

Pipeline (slide 26)
-------------------
1. Preprocessing  : lowercase, punctuation removal, tokenisation,
                    stopword removal, stemming
2. Feature Eng.   : TF-IDF vectorisation (unigrams + bigrams)
3. Classification : Multinomial Naive Bayes for category,
                    rule-augmented label for priority
4. Persistence    : model saved to / loaded from models/triage_model.pkl
                    so the web process never re-trains on startup

Public API
----------
    engine = NLPEngine()
    engine.load()                        # load trained model from disk
    category, priority = engine.predict("water leakage in hostel room")
    # → ("Hostel", "high")

    # To (re-)train:
    engine.train(samples)               # list of (text, category) tuples
    engine.save()
"""

import os
import re
import pickle
import logging
from pathlib import Path
from typing import Optional

import nltk
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NLTK data — download once, silently skip if already present
# ---------------------------------------------------------------------------
for _pkg in ("stopwords", "punkt", "punkt_tab"):
    try:
        nltk.download(_pkg, quiet=True)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL_DIR  = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "triage_model.pkl"

# Categories must match department names seeded in schema.sql
CATEGORIES = ["Hostel", "Electrical", "Academic", "Maintenance", "IT Support", "Transport"]

# Priority keyword rules applied AFTER category classification.
# If any keyword matches the complaint text the priority is escalated.
PRIORITY_RULES: dict[str, list[str]] = {
    "urgent": [
        "fire", "flood", "emergency", "accident", "injury",
        "gas leak", "electrocution", "unconscious", "collapse",
        "broken glass", "blood", "sparking", "spark", "electric shock",
        "short circuit", "burst", "stranded",
    ],
    "high": [
        "no water", "power cut", "no electricity", "water leakage",
        "sewage", "overflow", "roof leak", "not working since days",
        "exam", "result", "harassment", "ragging", "theft",
        "pest", "rats",
    ],
    "low": [
        "suggestion", "feedback", "minor", "small", "request",
        "improve", "would like", "please consider",
    ],
}


# ---------------------------------------------------------------------------
# Text preprocessor
# ---------------------------------------------------------------------------
class TextPreprocessor:
    """
    Tokenise, clean, remove stopwords, and stem complaint text.
    Operates as a standalone callable so it can be embedded inside
    the sklearn Pipeline as a custom transformer step.
    """

    def __init__(self) -> None:
        self._stemmer   = PorterStemmer()
        self._stopwords = set(stopwords.words("english"))
        # Common college-domain stopwords that add noise
        self._stopwords.update({"dear", "sir", "madam", "please", "kindly",
                                 "college", "department", "student", "regarding"})

    def clean(self, text: str) -> str:
        """Return a preprocessed string ready for TF-IDF."""
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)   # remove punctuation
        text = re.sub(r"\s+", " ", text).strip()
        tokens = text.split()
        tokens = [t for t in tokens if t not in self._stopwords and len(t) > 2]
        tokens = [self._stemmer.stem(t) for t in tokens]
        return " ".join(tokens)

    def transform(self, texts: list[str]) -> list[str]:
        return [self.clean(t) for t in texts]


# ---------------------------------------------------------------------------
# NLP Engine
# ---------------------------------------------------------------------------
class NLPEngine:
    """
    Wraps the full TF-IDF + Naive Bayes triage pipeline.

    Attributes
    ----------
    is_loaded : bool
        True once a trained model has been loaded or trained in this
        session.
    """

    def __init__(self) -> None:
        self._preprocessor = TextPreprocessor()
        self._pipeline: Optional[Pipeline] = None
        self._label_encoder: Optional[LabelEncoder] = None
        self.is_loaded: bool = False

    # ------------------------------------------------------------------ #
    # Training                                                            #
    # ------------------------------------------------------------------ #
    def train(self, samples: list[tuple[str, str]]) -> None:
        """
        Fit the model on labelled training samples.

        Parameters
        ----------
        samples : list of (text, category) tuples
            e.g. [("water not coming", "Hostel"), ("wifi down", "IT Support")]
        """
        if not samples:
            raise ValueError("Training samples list is empty.")

        texts      = [s[0] for s in samples]
        raw_labels = [s[1] for s in samples]

        # Preprocess texts
        cleaned = self._preprocessor.transform(texts)

        # Encode string labels → integers
        self._label_encoder = LabelEncoder()
        encoded_labels = self._label_encoder.fit_transform(raw_labels)

        # Build sklearn Pipeline:
        #   TfidfVectorizer  → sparse matrix of TF-IDF features
        #   MultinomialNB    → probabilistic classifier
        self._pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(
                ngram_range=(1, 2),     # unigrams + bigrams
                max_features=5000,
                sublinear_tf=True,      # apply log(1+tf) scaling
                min_df=1,
            )),
            ("clf", MultinomialNB(alpha=0.5)),
        ])

        self._pipeline.fit(cleaned, encoded_labels)
        self.is_loaded = True
        logger.info("NLP model trained on %d samples | categories: %s",
                    len(samples), list(self._label_encoder.classes_))

    # ------------------------------------------------------------------ #
    # Persistence                                                         #
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        """Serialise the trained pipeline and label encoder to disk."""
        if not self.is_loaded:
            raise RuntimeError("No trained model to save.")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "pipeline":      self._pipeline,
            "label_encoder": self._label_encoder,
        }
        with open(MODEL_PATH, "wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info("Model saved → %s", MODEL_PATH)

    def load(self) -> None:
        """
        Load the trained model from disk.
        Raises FileNotFoundError if the model file does not exist
        (run nlp_trainer.py first).
        """
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Trained model not found at {MODEL_PATH}. "
                "Run `python nlp_trainer.py` to train and save the model."
            )
        with open(MODEL_PATH, "rb") as fh:
            payload = pickle.load(fh)
        self._pipeline      = payload["pipeline"]
        self._label_encoder = payload["label_encoder"]
        self.is_loaded = True
        logger.info("Model loaded ← %s", MODEL_PATH)

    # ------------------------------------------------------------------ #
    # Inference                                                           #
    # ------------------------------------------------------------------ #
    def predict(self, text: str) -> tuple[str, str]:
        """
        Predict the category and priority for a complaint description.

        Parameters
        ----------
        text : str
            Raw complaint text submitted by the student.

        Returns
        -------
        (category, priority) : tuple[str, str]
            category : one of CATEGORIES
            priority : "urgent" | "high" | "medium" | "low"
        """
        if not self.is_loaded:
            raise RuntimeError("Model is not loaded. Call load() first.")
        if not text or not text.strip():
            return ("Academic", "medium")   # safe fallback

        cleaned   = self._preprocessor.clean(text)
        encoded   = self._pipeline.predict([cleaned])[0]
        category  = self._label_encoder.inverse_transform([encoded])[0]
        priority  = self._rule_based_priority(text.lower())

        logger.debug("NLP predict | category=%s priority=%s | text=%.60s",
                     category, priority, text)
        return category, priority

    def predict_proba(self, text: str) -> dict[str, float]:
        """
        Return per-category confidence scores (useful for admin UI).

        Returns
        -------
        dict mapping category name → probability (0.0–1.0)
        """
        if not self.is_loaded:
            raise RuntimeError("Model is not loaded.")
        cleaned = self._preprocessor.clean(text)
        probs   = self._pipeline.predict_proba([cleaned])[0]
        classes = self._label_encoder.classes_
        return {cls: float(round(p, 4)) for cls, p in zip(classes, probs)}

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rule_based_priority(text_lower: str) -> str:
        """
        Scan the raw (lowercased) text for keyword triggers and return
        the highest matching priority tier.

        Order of precedence: urgent > high > medium > low
        """
        for level in ("urgent", "high", "low"):
            for keyword in PRIORITY_RULES[level]:
                if keyword in text_lower:
                    return level
        return "medium"   # default


# ---------------------------------------------------------------------------
# Module-level singleton — imported by app.py and routes
# ---------------------------------------------------------------------------
engine = NLPEngine()
