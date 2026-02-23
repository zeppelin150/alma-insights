"""
Alma Insights — Trending Engine
Sentiment analysis, TF-IDF rising terms, and topic clustering for the Trending Topics page.
"""

import re
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict


# ═══ Domain-specific stopwords ═══
# Comprehensive set tuned to surface RCM, clinical, platform/bug, and service
# terms while filtering generic sentiment, conversational, and temporal noise.
# Domain-relevant terms deliberately EXCLUDED: claim, billing, authorization,
# credentialing, eligibility, denial, remittance, modifier, coding, charge,
# payment, error, timeout, crash, login, portal, dashboard, sync, downtime,
# outage, bug, SLA, escalation, backlog, queue, priority, deductible, copay,
# coinsurance, appeal, patient, provider, coverage, procedure, diagnosis,
# referral, formulary, prescription.
DOMAIN_STOPWORDS = {
    # ── Generic conversational filler ──
    "ticket", "zendesk", "please", "thank", "thanks", "hi", "hello",
    "would", "could", "also", "like", "know", "need", "get", "one",
    "us", "see", "let", "want", "going", "sure", "okay", "yes", "no",
    "got", "make", "made", "right", "well", "just", "really", "much",
    "still", "back", "even", "way", "thing", "things", "said", "say",
    "actually", "basically", "someone", "something", "anything",
    "everything", "everyone", "another", "nothing", "nobody", "anybody",
    "whoever", "whatever", "however", "already", "always", "sometimes",
    "often", "usually", "probably", "maybe", "perhaps", "obviously",
    "apparently", "definitely", "certainly", "absolutely", "completely",
    "totally", "entire", "whole", "several", "many", "most",
    # ── Sentiment filler (not domain-specific) ──
    "life", "never", "experienced", "poor", "bad", "terrible", "horrible",
    "worst", "awful", "great", "love", "hate", "angry", "frustrated",
    "upset", "disappointed", "happy", "satisfied", "dissatisfied",
    "unacceptable", "ridiculous", "annoying", "inconvenient", "confusing",
    "wonderful", "excellent", "fantastic", "amazing", "perfect",
    "disgusted", "outraged", "pleased", "grateful", "thankful",
    "unbelievable",
    # ── Generic verbs (not domain-specific) ──
    "understand", "explain", "wait", "waited", "waiting",
    "pay", "paid", "paying", "resolve", "resolved",
    "open", "opened", "close", "closed", "start", "started",
    "end", "ended", "happen", "happened", "receive", "received",
    "expect", "expected", "continue", "continued",
    "follow", "following", "respond", "response", "responded",
    "spoke", "speak", "speaking", "mention", "mentioned",
    "notice", "noticed", "advise", "advised",
    # ── Temporal / connective noise ──
    "today", "yesterday", "tomorrow", "week", "month", "year",
    "ago", "since", "until", "before", "after", "first",
    "last", "next", "previous", "recent", "recently",
    "issue", "problem", "concern", "situation", "matter",
    "company", "business", "account", "service", "customer",
    "member", "person", "people",
    # ── Bot / system noise ──
    "status", "changed", "updated", "assigned",
    "automated", "reminder", "survey", "sent", "requester",
    # ── Greetings / closings ──
    "good", "morning", "afternoon", "evening", "regards", "best",
    "sincerely", "team", "support", "help", "assist", "assistance",
    "reach", "contact",
    # ── Generic domain noise ──
    "alma", "email", "send", "dear", "call", "called",
    "day", "time", "able", "told", "take", "give", "come", "look",
    "tell", "work", "use", "try", "ask", "show", "think", "feel",
    "seem", "keep", "put", "mean", "become", "leave", "information",
    "provide", "process", "request", "number", "date", "name",
    "phone", "case", "system", "update",
}

# ═══ Compound domain terms ═══
# Multi-word terms are joined with underscores BEFORE tokenization
# so TF-IDF treats them as single features.
COMPOUND_TERMS = {
    # Billing / payments
    "auto pay": "auto_pay",
    "auto-pay": "auto_pay",
    "autopay": "auto_pay",
    "recurring charge": "recurring_charge",
    "duplicate charge": "duplicate_charge",
    "double charge": "duplicate_charge",
    "incorrect charge": "incorrect_charge",
    "wrong charge": "incorrect_charge",
    "late fee": "late_fee",
    "late payment": "late_payment",
    "minimum payment": "minimum_payment",
    "payment plan": "payment_plan",
    "payment processing": "payment_processing",
    "balance bill": "balance_bill",
    "balance billing": "balance_billing",
    "surprise bill": "surprise_bill",
    "surprise billing": "surprise_billing",
    "write off": "write_off",
    "write-off": "write_off",
    # RCM / clinical
    "timely filing": "timely_filing",
    "coordination of benefits": "coordination_of_benefits",
    "cob": "coordination_of_benefits",
    "prior authorization": "prior_authorization",
    "prior auth": "prior_authorization",
    "pre auth": "prior_authorization",
    "pre-auth": "prior_authorization",
    "preauth": "prior_authorization",
    "date of service": "date_of_service",
    "dos": "date_of_service",
    "explanation of benefits": "explanation_of_benefits",
    "eob": "explanation_of_benefits",
    "denied claim": "denied_claim",
    "claim denial": "claim_denial",
    "out of network": "out_of_network",
    "oon": "out_of_network",
    "in network": "in_network",
    "revenue cycle": "revenue_cycle",
    "rcm": "revenue_cycle",
    "remittance advice": "remittance_advice",
    "era": "remittance_advice",
    "payment posting": "payment_posting",
    "provider enrollment": "provider_enrollment",
    "eligibility verification": "eligibility_verification",
    "elig check": "eligibility_verification",
    "cpt code": "cpt_code",
    "modifier 25": "modifier_25",
    "modifier 59": "modifier_59",
    # Churn / retention
    "client churn": "client_churn",
    "member churn": "client_churn",
    "patient churn": "client_churn",
    "no show": "no_show",
    "no-show": "no_show",
    "noshow": "no_show",
    # Financial
    "accounts receivable": "accounts_receivable",
    "credentialing": "credentialing",
}

# Pre-sort compound terms by descending key length for replacement
_SORTED_COMPOUNDS = sorted(COMPOUND_TERMS.items(), key=lambda x: -len(x[0]))

# Regex to strip timestamp lines and role labels
_ROLE_HEADER_RE = re.compile(r'\[.*?\]\s*(CUSTOMER|AGENT|BOT).*?:\n', re.IGNORECASE)


def _ensure_nltk_data():
    """Download NLTK data files on first use. Returns True if successful."""
    import nltk
    resources = {
        'vader_lexicon': 'sentiment/vader_lexicon',
        'punkt': 'tokenizers/punkt',
        'punkt_tab': 'tokenizers/punkt_tab',
        'stopwords': 'corpora/stopwords',
    }
    for name, path in resources.items():
        try:
            nltk.data.find(path)
        except LookupError:
            try:
                nltk.download(name, quiet=True)
            except Exception:
                pass
    return True


def _apply_compound_terms(text, compounds=None):
    """Replace known multi-word domain terms with underscore-joined tokens.

    Args:
        text: Input text (already lowercased).
        compounds: Optional dict {phrase: replacement}. If None, uses built-in
                   COMPOUND_TERMS. When provided (e.g. from compound_discovery),
                   the dict is sorted by key length on each call.
    """
    if compounds is not None:
        sorted_pairs = sorted(compounds.items(), key=lambda x: -len(x[0]))
    else:
        sorted_pairs = _SORTED_COMPOUNDS
    for phrase, replacement in sorted_pairs:
        text = text.replace(phrase, replacement)
    return text


def _display_term(term):
    """Convert internal underscore-joined terms back to space-separated display form."""
    return term.replace("_", " ")


def _clean_thread_text(text, compounds=None):
    """Clean a full_thread for NLP analysis.

    Args:
        text: Raw conversation thread text.
        compounds: Optional dict of compound terms to apply. Passed through to
                   _apply_compound_terms(). None = use built-in COMPOUND_TERMS.
    """
    if not text:
        return ""
    # Strip role headers
    text = _ROLE_HEADER_RE.sub(' ', text)
    # Strip separator lines
    text = text.replace('---', ' ')
    # Lowercase
    text = text.lower()
    # Apply compound term substitutions
    text = _apply_compound_terms(text, compounds=compounds)
    return text


def _tokenize_and_clean(text):
    """Tokenize and remove stopwords, then apply concept normalization."""
    import nltk
    from nltk.corpus import stopwords

    tokens = nltk.word_tokenize(text)
    english_stops = set(stopwords.words('english'))
    all_stops = english_stops | DOMAIN_STOPWORDS

    # Keep tokens with alpha/underscore/hyphen, length >= 3, not stopwords
    cleaned = [t for t in tokens
               if len(t) >= 3
               and t not in all_stops
               and re.match(r'^[a-z][a-z_-]+$', t)]

    # P2.1a: normalize synonyms to canonical concept IDs
    cleaned = _apply_concept_normalization(cleaned)

    return cleaned


# ── P2.1a: Concept normalization (built once at module level) ──
from src.data.concept_map import build_concept_index, apply_concept_normalization as _concept_normalize
_concept_index = build_concept_index()


def _apply_concept_normalization(tokens: list) -> list:
    """Replace synonym tokens with canonical concept IDs."""
    return _concept_normalize(tokens, _concept_index)


def _bucket_conversations(conversations, window_size):
    """Group conversations into time buckets. Returns {window_label: [conv_dicts]}."""
    buckets = defaultdict(list)

    for conv in conversations:
        created = conv.get("created_at", "")
        if not created:
            continue
        try:
            dt = datetime.strptime(created[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if window_size == "Hourly":
            # Truncate to hour — parse from ISO timestamp
            try:
                dt_full = datetime.strptime(created[:13], "%Y-%m-%d %H")
            except (ValueError, TypeError):
                try:
                    dt_full = datetime.strptime(created[:13], "%Y-%m-%dT%H")
                except (ValueError, TypeError):
                    dt_full = dt  # fallback to midnight
            label = dt_full.strftime("%Y-%m-%d %H:00")
        elif window_size == "Daily":
            label = dt.strftime("%Y-%m-%d")
        elif window_size == "Weekly":
            # ISO week
            iso = dt.isocalendar()
            label = f"{iso[0]}-W{iso[1]:02d}"
        elif window_size == "Biweekly":
            # Two-week periods
            iso = dt.isocalendar()
            biweek = (iso[1] - 1) // 2 + 1
            label = f"{iso[0]}-BW{biweek:02d}"
        else:  # Monthly
            label = dt.strftime("%Y-%m")

        buckets[label].append(conv)

    return dict(sorted(buckets.items()))


# ═══════════════════════════════════════════
#  SENTIMENT TRENDS
# ═══════════════════════════════════════════

def compute_sentiment_trends(conn, date_start, date_end, trc_filter, window_size):
    """
    Returns {trc: [(window_label, avg_compound), ...]}
    If trc_filter is None/empty, groups by top 5 TRCs by volume.
    """
    _ensure_nltk_data()
    from nltk.sentiment.vader import SentimentIntensityAnalyzer

    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
    if not conversations:
        return {}

    sia = SentimentIntensityAnalyzer()

    # Score each conversation
    for conv in conversations:
        text = conv.get("full_thread", "")
        cleaned = _clean_thread_text(text)
        if cleaned.strip():
            scores = sia.polarity_scores(cleaned)
            conv["_sentiment"] = scores["compound"]
        else:
            conv["_sentiment"] = 0.0

    # Determine TRC grouping
    if trc_filter:
        trc_groups = {trc_filter: [c for c in conversations if c.get("trc_code") == trc_filter]}
    else:
        # Top 5 TRCs by volume
        trc_counts = defaultdict(int)
        for c in conversations:
            if c.get("trc_code"):
                trc_counts[c["trc_code"]] += 1
        top_trcs = sorted(trc_counts, key=trc_counts.get, reverse=True)[:5]
        trc_groups = {}
        for trc in top_trcs:
            trc_groups[trc] = [c for c in conversations if c.get("trc_code") == trc]

    # Bucket and average
    result = {}
    for trc, convs in trc_groups.items():
        buckets = _bucket_conversations(convs, window_size)
        series = []
        for label, bucket_convs in buckets.items():
            sentiments = [c["_sentiment"] for c in bucket_convs]
            avg = np.mean(sentiments) if sentiments else 0.0
            series.append((label, float(avg)))
        if series:
            result[trc] = series

    return result


# ═══════════════════════════════════════════
#  TEMPORAL VELOCITY (Pass 1.5)
# ═══════════════════════════════════════════

def _compute_temporal_velocity(term_scores, half_life_windows=3):
    """
    Compute velocity with exponential recency weighting.

    Instead of flat linear regression, recent windows count more.
    half_life_windows: after this many windows, weight drops to 50%.

    Returns dict with velocity, raw_velocity, recency_score, peak_recency,
    and is_temporally_promoted flag.
    """
    n = len(term_scores)
    if n < 2:
        return {
            "velocity": 0.0, "raw_velocity": 0.0, "recency_score": 0.0,
            "peak_window": 0, "peak_recency": "historical",
            "is_temporally_promoted": False,
        }

    y = np.array(term_scores, dtype=float)
    x = np.arange(n, dtype=float)

    # Raw velocity (original flat linear regression)
    raw_slope = float(np.polyfit(x, y, 1)[0])

    # Exponential recency weights: newest window = highest weight
    decay_rate = np.log(2) / half_life_windows
    weights = np.exp(-decay_rate * (n - 1 - x))
    weights /= weights.sum()

    # Weighted linear regression
    x_mean = np.average(x, weights=weights)
    y_mean = np.average(y, weights=weights)
    numerator = np.sum(weights * (x - x_mean) * (y - y_mean))
    denominator = np.sum(weights * (x - x_mean) ** 2)
    weighted_slope = float(numerator / denominator) if denominator != 0 else 0.0

    # Recency score: signal strength in last 2 windows vs overall
    if n >= 3 and y.max() > 0:
        recent_signal = float(np.mean(y[-2:]))
        total_signal = float(np.mean(y))
        recency_score = min(1.0, recent_signal / total_signal) if total_signal > 0 else 0.0
    else:
        recency_score = 0.5

    # Peak window analysis
    peak_idx = int(np.argmax(y))
    windows_from_end = n - 1 - peak_idx
    if windows_from_end <= 1:
        peak_recency = "current"
    elif windows_from_end <= 3:
        peak_recency = "recent"
    else:
        peak_recency = "historical"

    is_promoted = recency_score > 0.6 and weighted_slope > 0

    return {
        "velocity": weighted_slope,
        "raw_velocity": raw_slope,
        "recency_score": round(recency_score, 3),
        "peak_window": peak_idx,
        "peak_recency": peak_recency,
        "is_temporally_promoted": is_promoted,
    }


# ═══════════════════════════════════════════
#  TF-IDF FEEDBACK LOOP (Pass 1.5)
# ═══════════════════════════════════════════

def _build_weight_modifiers(conn):
    """
    Build a term → weight multiplier dict from user feedback.

    Aggregation rules:
    - "important" signals: each adds 0.2 to the multiplier
    - "relevant" signals: each adds 0.1
    - "noise" signals: each subtracts 0.15
    - Floor at 0.1, ceiling at 3.0
    - Explicit user_terms.weight_modifier overrides everything

    Returns: {term: float_multiplier}
    """
    modifiers = {}

    # Check if feedback tables exist (graceful degradation)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    except Exception:
        return modifiers

    # Aggregate from feedback log
    if "tfidf_feedback" in tables:
        try:
            rows = conn.execute("""
                SELECT term, feedback_type, COUNT(*) as cnt
                FROM tfidf_feedback
                GROUP BY term, feedback_type
            """).fetchall()
            for row in rows:
                term = row["term"] if hasattr(row, "keys") else row[0]
                ftype = row["feedback_type"] if hasattr(row, "keys") else row[1]
                cnt = row["cnt"] if hasattr(row, "keys") else row[2]

                if term not in modifiers:
                    modifiers[term] = 1.0

                if ftype == "important":
                    modifiers[term] += 0.2 * cnt
                elif ftype == "relevant":
                    modifiers[term] += 0.1 * cnt
                elif ftype == "noise":
                    modifiers[term] -= 0.15 * cnt
        except Exception:
            pass

    # Explicit overrides from user_terms
    if "user_terms" in tables:
        try:
            rows = conn.execute(
                "SELECT canonical_form, weight_modifier FROM user_terms "
                "WHERE action IN ('promote', 'demote')"
            ).fetchall()
            for row in rows:
                term = row["canonical_form"] if hasattr(row, "keys") else row[0]
                weight = row["weight_modifier"] if hasattr(row, "keys") else row[1]
                modifiers[term] = weight
        except Exception:
            pass

    # Clamp
    for term in modifiers:
        modifiers[term] = max(0.1, min(3.0, modifiers[term]))

    return modifiers


def _get_user_term_actions(conn):
    """Get user action overrides for terms (for UI annotation)."""
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "user_terms" not in tables:
            return {}
        rows = conn.execute(
            "SELECT term, action FROM user_terms ORDER BY updated_at DESC"
        ).fetchall()
        # Return first action per term (most recent)
        result = {}
        for row in rows:
            term = row["term"] if hasattr(row, "keys") else row[0]
            action = row["action"] if hasattr(row, "keys") else row[1]
            if term not in result:
                result[term] = action
        return result
    except Exception:
        return {}


# ═══════════════════════════════════════════
#  RISING TERMS (TF-IDF VELOCITY)
# ═══════════════════════════════════════════

def compute_rising_terms(conn, date_start, date_end, trc_filter, window_size,
                         conn_for_feedback=None):
    """
    Returns {
        rising: [{term, velocity, current_score, sparkline_data,
                  temporal_promoted, recency_score, peak_recency, user_action}, ...],
        cooling: [{...same...}, ...]
    }
    """
    _ensure_nltk_data()

    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
    if not conversations:
        return {"rising": [], "cooling": []}

    # Bucket conversations
    buckets = _bucket_conversations(conversations, window_size)
    if len(buckets) < 2:
        return {"rising": [], "cooling": []}

    window_labels = list(buckets.keys())

    # Build one "document" per window: concatenated cleaned text
    docs = []
    for label in window_labels:
        window_texts = []
        for conv in buckets[label]:
            text = _clean_thread_text(conv.get("full_thread", ""))
            tokens = _tokenize_and_clean(text)
            window_texts.append(" ".join(tokens))
        docs.append(" ".join(window_texts))

    # Fit TF-IDF across all windows
    from sklearn.feature_extraction.text import TfidfVectorizer

    # Adaptive min_df: with few windows (docs), use min_df=1 to avoid
    # filtering out all terms; with more windows use 2.
    adaptive_min_df = 1 if len(docs) <= 3 else 2

    vectorizer = TfidfVectorizer(
        max_features=5000,
        min_df=adaptive_min_df,
        max_df=0.85,
        ngram_range=(1, 3),
        token_pattern=r'(?u)\b\w[\w-]+\b',
    )

    try:
        tfidf_matrix = vectorizer.fit_transform(docs)
    except ValueError:
        return {"rising": [], "cooling": []}

    feature_names = vectorizer.get_feature_names_out()
    tfidf_array = tfidf_matrix.toarray()  # shape: (n_windows, n_features)

    # Apply feedback weight modifiers (Pass 1.5)
    feedback_conn = conn_for_feedback or conn
    weight_mods = _build_weight_modifiers(feedback_conn)
    if weight_mods:
        for fi, fname in enumerate(feature_names):
            display_name = _display_term(fname)
            if display_name in weight_mods:
                tfidf_array[:, fi] *= weight_mods[display_name]

    # Get user action annotations (for UI badges)
    user_actions = _get_user_term_actions(feedback_conn)

    # Compute velocity (recency-weighted) for each term
    velocities = []

    for fi in range(len(feature_names)):
        scores = tfidf_array[:, fi]
        if scores.max() < 0.001:  # skip near-zero terms
            continue

        tv = _compute_temporal_velocity(scores.tolist())
        display_name = _display_term(feature_names[fi])

        velocities.append({
            "term": display_name,
            "velocity": tv["velocity"],
            "raw_velocity": tv["raw_velocity"],
            "current_score": float(scores[-1]),
            "sparkline_data": scores.tolist(),
            "temporal_promoted": tv["is_temporally_promoted"],
            "recency_score": tv["recency_score"],
            "peak_recency": tv["peak_recency"],
            "user_action": user_actions.get(display_name),
        })

    # Sort: velocity * (1 + 0.5 * recency_score) — promoted terms rank higher
    velocities.sort(
        key=lambda v: v["velocity"] * (1 + 0.5 * v.get("recency_score", 0)),
        reverse=True,
    )

    rising = [v for v in velocities if v["velocity"] > 0.001][:15]
    cooling = [v for v in velocities if v["velocity"] < -0.001]
    cooling.sort(key=lambda v: v["velocity"])
    cooling = cooling[:10]

    return {"rising": rising, "cooling": cooling}


# ═══════════════════════════════════════════
#  TOPIC CLUSTERS (K-Means)
# ═══════════════════════════════════════════

def compute_topic_clusters(conn, date_start, date_end, trc_filter):
    """
    Returns {clusters: [{label, terms, ticket_ids, count, avg_csat, avg_sentiment}, ...]}
    """
    _ensure_nltk_data()
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans

    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
    if len(conversations) < 20:
        return {"clusters": [], "message": "Need at least 20 conversations for clustering"}

    sia = SentimentIntensityAnalyzer()

    # Prepare texts and metadata
    texts = []
    meta = []
    for conv in conversations:
        cleaned = _clean_thread_text(conv.get("full_thread", ""))
        tokens = _tokenize_and_clean(cleaned)
        text = " ".join(tokens)
        if text.strip():
            texts.append(text)

            sentiment = sia.polarity_scores(cleaned)["compound"] if cleaned.strip() else 0.0
            meta.append({
                "ticket_id": conv["ticket_id"],
                "csat_score": conv.get("csat_score"),
                "sentiment": sentiment,
            })

    if len(texts) < 20:
        return {"clusters": [], "message": "Not enough text content for clustering"}

    # TF-IDF
    vectorizer = TfidfVectorizer(
        max_features=5000,
        min_df=2,
        max_df=0.85,
        ngram_range=(1, 3),
        token_pattern=r'(?u)\b\w[\w-]+\b',
    )

    try:
        tfidf_matrix = vectorizer.fit_transform(texts)
    except ValueError:
        return {"clusters": [], "message": "Could not vectorize conversations"}

    feature_names = vectorizer.get_feature_names_out()

    # Auto-select K
    k = min(8, max(2, len(texts) // 20))

    # K-Means
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10, max_iter=300)
    labels = kmeans.fit_predict(tfidf_matrix)

    # Build cluster info
    clusters = []
    for ci in range(k):
        mask = labels == ci
        cluster_indices = np.where(mask)[0]

        if len(cluster_indices) == 0:
            continue

        # Top terms from centroid
        centroid = kmeans.cluster_centers_[ci]
        top_indices = centroid.argsort()[-5:][::-1]
        top_terms = [_display_term(feature_names[ti]) for ti in top_indices]
        label = ", ".join(top_terms)

        # Gather ticket IDs and stats
        ticket_ids = [meta[idx]["ticket_id"] for idx in cluster_indices]

        csat_scores = [meta[idx]["csat_score"] for idx in cluster_indices
                       if meta[idx]["csat_score"] is not None]
        avg_csat = float(np.mean(csat_scores)) if csat_scores else None

        sentiments = [meta[idx]["sentiment"] for idx in cluster_indices]
        avg_sentiment = float(np.mean(sentiments)) if sentiments else 0.0

        clusters.append({
            "label": label,
            "terms": top_terms,
            "ticket_ids": ticket_ids,
            "count": len(cluster_indices),
            "avg_csat": avg_csat,
            "avg_sentiment": avg_sentiment,
        })

    # Sort by count descending
    clusters.sort(key=lambda c: c["count"], reverse=True)

    return {"clusters": clusters, "method": "kmeans"}


# ═══════════════════════════════════════════
#  NMF TOPIC MODELING
# ═══════════════════════════════════════════

def compute_topic_model(conn, date_start, date_end, trc_filter, window_size,
                        tfidf_matrix=None, feature_names=None, texts_meta=None):
    """
    Decompose conversations into latent topics using NMF (overlapping topics).

    When called from run_full_analysis(), tfidf_matrix/feature_names/texts_meta are
    pre-computed and passed in. When called standalone, they are computed internally.

    Returns {
        topics: [{label, top_terms, ticket_ids, count, avg_csat, avg_sentiment,
                  trend_direction, trend_slope, trend_sparkline}, ...],
        multi_topic_tickets: [{ticket_id, topics, subject}, ...],
        method: "nmf",
    }
    """
    from sklearn.decomposition import NMF
    from sklearn.feature_extraction.text import TfidfVectorizer

    # If not pre-computed, build TF-IDF and metadata internally
    if tfidf_matrix is None or feature_names is None or texts_meta is None:
        _ensure_nltk_data()
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
        sia = SentimentIntensityAnalyzer()

        conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
        if len(conversations) < 20:
            return {"topics": [], "multi_topic_tickets": [], "method": "nmf",
                    "message": "Need at least 20 conversations for topic modeling"}

        texts = []
        texts_meta = []
        for conv in conversations:
            cleaned = _clean_thread_text(conv.get("full_thread", ""))
            tokens = _tokenize_and_clean(cleaned)
            text = " ".join(tokens)
            if text.strip():
                sentiment = sia.polarity_scores(cleaned)["compound"] if cleaned.strip() else 0.0
                texts.append(text)
                texts_meta.append({
                    "ticket_id": conv["ticket_id"],
                    "subject": conv.get("subject", ""),
                    "csat_score": conv.get("csat_score"),
                    "sentiment": sentiment,
                    "created_at": conv.get("created_at", ""),
                    "trc_code": conv.get("trc_code", ""),
                })

        if len(texts) < 20:
            return {"topics": [], "multi_topic_tickets": [], "method": "nmf",
                    "message": "Not enough text content for topic modeling"}

        vectorizer = TfidfVectorizer(
            max_features=5000, min_df=2, max_df=0.85,
            ngram_range=(1, 3), token_pattern=r'(?u)\b\w[\w-]+\b',
        )
        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()
        except ValueError:
            return {"topics": [], "multi_topic_tickets": [], "method": "nmf",
                    "message": "Could not vectorize conversations"}

    n_docs = tfidf_matrix.shape[0]
    if n_docs < 20:
        return {"topics": [], "multi_topic_tickets": [], "method": "nmf",
                "message": "Not enough documents for topic modeling"}

    # Auto-select K
    k = min(12, max(3, n_docs // 15))

    # Fit NMF
    nmf = NMF(n_components=k, random_state=42, max_iter=300, init='nndsvda')
    W = nmf.fit_transform(tfidf_matrix)   # (n_docs, k) — doc-topic scores
    H = nmf.components_                    # (k, n_features) — topic-term weights

    # Build topic info
    topics = []
    MULTI_TOPIC_THRESHOLD = 0.15

    for ti in range(k):
        # Top terms from this topic's component vector
        top_term_indices = H[ti].argsort()[-8:][::-1]
        top_terms = [
            {"term": _display_term(feature_names[idx]), "weight": float(H[ti][idx])}
            for idx in top_term_indices
        ]
        label = ", ".join(t["term"] for t in top_terms[:5])

        # Find documents where this topic scores highest
        primary_mask = W[:, ti] == W.max(axis=1)
        primary_indices = np.where(primary_mask)[0]

        # All docs with any score > 0.01 for this topic
        all_mask = W[:, ti] > 0.01
        all_indices = np.where(all_mask)[0]

        ticket_ids = [texts_meta[idx]["ticket_id"] for idx in primary_indices]

        csat_scores = [texts_meta[idx]["csat_score"] for idx in all_indices
                       if texts_meta[idx].get("csat_score") is not None]
        avg_csat = float(np.mean(csat_scores)) if csat_scores else None

        sentiments = [texts_meta[idx].get("sentiment", 0) for idx in all_indices]
        avg_sentiment = float(np.mean(sentiments)) if sentiments else 0.0

        topics.append({
            "label": label,
            "top_terms": top_terms,
            "ticket_ids": ticket_ids,
            "count": len(primary_indices),
            "avg_csat": avg_csat,
            "avg_sentiment": avg_sentiment,
            "trend_direction": "stable",
            "trend_slope": 0.0,
            "trend_sparkline": [],
        })

    # ── Compute topic trends over time ──
    convs_for_buckets = [
        {"created_at": texts_meta[i].get("created_at", "")}
        for i in range(n_docs)
    ]
    buckets = _bucket_conversations(convs_for_buckets, window_size)
    if len(buckets) >= 2:
        window_labels = list(buckets.keys())
        # Build doc_index → window_index mapping
        doc_window = {}
        for wi, (wlabel, wconvs) in enumerate(buckets.items()):
            for _ in wconvs:
                pass  # We need actual indices

        # Alternative: map each doc to its window by date
        for di in range(n_docs):
            created = texts_meta[di].get("created_at", "")
            if not created:
                continue
            try:
                from datetime import datetime
                dt = datetime.strptime(created[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

            if window_size == "Hourly":
                hour = created[11:13] if len(created) >= 13 else "00"
                wlabel = f"{created[:10]} {hour}:00"
            elif window_size == "Daily":
                wlabel = dt.strftime("%Y-%m-%d")
            elif window_size == "Weekly":
                iso = dt.isocalendar()
                wlabel = f"{iso[0]}-W{iso[1]:02d}"
            elif window_size == "Biweekly":
                iso = dt.isocalendar()
                biweek = (iso[1] - 1) // 2 + 1
                wlabel = f"{iso[0]}-BW{biweek:02d}"
            else:
                wlabel = dt.strftime("%Y-%m")

            doc_window[di] = wlabel

        for ti_idx, topic in enumerate(topics):
            scores_per_window = []
            for wlabel in window_labels:
                doc_indices = [di for di, wl in doc_window.items() if wl == wlabel]
                if doc_indices:
                    avg_score = float(np.mean([W[di, ti_idx] for di in doc_indices]))
                else:
                    avg_score = 0.0
                scores_per_window.append(avg_score)

            topic["trend_sparkline"] = scores_per_window
            if len(scores_per_window) >= 2:
                x = np.arange(len(scores_per_window), dtype=float)
                slope = float(np.polyfit(x, scores_per_window, 1)[0])
                topic["trend_slope"] = slope
                if slope > 0.005:
                    topic["trend_direction"] = "rising"
                elif slope < -0.005:
                    topic["trend_direction"] = "declining"
                else:
                    topic["trend_direction"] = "stable"

    # Sort by count descending
    topics.sort(key=lambda t: t["count"], reverse=True)

    # ── Identify multi-topic tickets ──
    multi_topic_tickets = []
    for di in range(n_docs):
        scores = W[di]
        significant = [(ti, float(scores[ti])) for ti in range(k)
                       if scores[ti] > MULTI_TOPIC_THRESHOLD]
        if len(significant) >= 2:
            significant.sort(key=lambda x: -x[1])
            multi_topic_tickets.append({
                "ticket_id": texts_meta[di]["ticket_id"],
                "subject": texts_meta[di].get("subject", ""),
                "topics": [
                    {"index": ti, "label": topics[ti]["label"], "score": sc}
                    for ti, sc in significant
                ],
            })

    return {
        "topics": topics,
        "multi_topic_tickets": multi_topic_tickets,
        "method": "nmf",
    }


# ═══════════════════════════════════════════
#  CROSS-TRC CORRELATION ANALYSIS
# ═══════════════════════════════════════════

def _build_per_trc_series(conversations, window_size):
    """Build per-TRC time series from pre-fetched conversations.

    Returns {trc: {volume: [...], sentiment: [...], windows: [...]}}
    Expects conversations to already have '_sentiment' key if available.
    """
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    sia = SentimentIntensityAnalyzer()

    # Group by TRC
    trc_groups = defaultdict(list)
    for conv in conversations:
        trc = conv.get("trc_code")
        if trc:
            trc_groups[trc].append(conv)

    result = {}
    for trc, convs in trc_groups.items():
        if len(convs) < 5:
            continue  # Too few for meaningful series
        buckets = _bucket_conversations(convs, window_size)
        windows = sorted(buckets.keys())
        if len(windows) < 4:
            continue  # Need at least 4 windows for correlation

        volumes = []
        sentiments = []
        for w in windows:
            volumes.append(len(buckets[w]))
            sents = []
            for c in buckets[w]:
                if "_sentiment" in c:
                    sents.append(c["_sentiment"])
                else:
                    text = _clean_thread_text(c.get("full_thread", ""))
                    if text.strip():
                        sents.append(sia.polarity_scores(text)["compound"])
            sentiments.append(float(np.mean(sents)) if sents else 0.0)

        result[trc] = {
            "volume": volumes,
            "sentiment": sentiments,
            "windows": windows,
        }

    return result


def compute_cross_trc_correlations(conn, date_start, date_end, window_size,
                                   min_correlation=0.5, per_trc_series=None):
    """
    Detect correlated patterns across different TRC codes.

    Uses Pearson correlation between per-TRC time series (volume, sentiment).
    When called from run_full_analysis(), per_trc_series is pre-computed.

    Returns {
        correlations: [{trc_a, trc_b, metric_a, metric_b, r, p_value,
                        strength, direction, interpretation,
                        series_a, series_b, window_labels}, ...],
        per_trc_series: {...}
    }
    """
    from scipy.stats import pearsonr

    # Build per-TRC series if not provided
    if per_trc_series is None:
        conversations = _fetch_conversations(conn, date_start, date_end, None)
        per_trc_series = _build_per_trc_series(conversations, window_size)

    trc_list = sorted(per_trc_series.keys())
    if len(trc_list) < 2:
        return {"correlations": [], "per_trc_series": per_trc_series}

    def _flatten_metrics(trc_data):
        """Yield (metric_name, values_list) for each metric in a TRC."""
        yield ("volume", trc_data["volume"])
        yield ("sentiment", trc_data["sentiment"])

    correlations = []

    for i, trc_a in enumerate(trc_list):
        data_a = per_trc_series[trc_a]
        windows_a = data_a["windows"]

        for trc_b in trc_list[i + 1:]:
            data_b = per_trc_series[trc_b]
            windows_b = data_b["windows"]

            # Find overlapping windows
            common_windows = sorted(set(windows_a) & set(windows_b))
            if len(common_windows) < 4:
                continue

            # Build index maps for common windows
            idx_a = {w: j for j, w in enumerate(windows_a)}
            idx_b = {w: j for j, w in enumerate(windows_b)}

            for metric_a_name, series_a_full in _flatten_metrics(data_a):
                for metric_b_name, series_b_full in _flatten_metrics(data_b):
                    # Extract values for common windows
                    vals_a = [series_a_full[idx_a[w]] for w in common_windows]
                    vals_b = [series_b_full[idx_b[w]] for w in common_windows]

                    # Skip if either series is constant (pearsonr would fail)
                    if len(set(vals_a)) < 2 or len(set(vals_b)) < 2:
                        continue

                    try:
                        r, p = pearsonr(vals_a, vals_b)
                    except Exception:
                        continue

                    if abs(r) >= min_correlation and p < 0.05:
                        strength = "strong" if abs(r) >= 0.7 else "moderate"
                        direction = "positive" if r > 0 else "negative"

                        # Human-readable interpretation
                        verb_a = "rises" if direction == "positive" else "rises"
                        verb_b = "rise" if direction == "positive" else "fall"
                        interpretation = (
                            f"When {metric_a_name} {verb_a} in {trc_a}, "
                            f"{metric_b_name} tends to {verb_b} in {trc_b} "
                            f"(r={r:.2f}, p={p:.3f})"
                        )

                        correlations.append({
                            "trc_a": trc_a,
                            "trc_b": trc_b,
                            "metric_a": metric_a_name,
                            "metric_b": metric_b_name,
                            "r": round(float(r), 3),
                            "p_value": round(float(p), 4),
                            "strength": strength,
                            "direction": direction,
                            "interpretation": interpretation,
                            "series_a": vals_a,
                            "series_b": vals_b,
                            "window_labels": common_windows,
                        })

    # Sort by |r| descending, cap at 20
    correlations.sort(key=lambda c: abs(c["r"]), reverse=True)
    correlations = correlations[:20]

    return {"correlations": correlations, "per_trc_series": per_trc_series}


# ═══════════════════════════════════════════
#  TEMPORAL LEAD-LAG DETECTION
# ═══════════════════════════════════════════

def _find_lead_lag(series_a, series_b, max_lag):
    """
    Compute cross-correlation at multiple lags.
    Returns (best_lag, best_correlation).
    Positive lag = series_a leads series_b.
    """
    n = len(series_a)
    if n < max_lag + 2:
        return 0, 0.0

    a = np.array(series_a, dtype=float)
    b = np.array(series_b, dtype=float)
    a = a - np.mean(a)
    b = b - np.mean(b)

    a_std = np.std(a)
    b_std = np.std(b)
    if a_std == 0 or b_std == 0:
        return 0, 0.0

    best_lag = 0
    best_r = 0.0

    for lag in range(-max_lag, max_lag + 1):
        if lag == 0:
            overlap_a = a
            overlap_b = b
        elif lag > 0:
            # a leads b: compare a[:-lag] with b[lag:]
            overlap_a = a[:n - lag]
            overlap_b = b[lag:]
        else:
            # b leads a: compare a[-lag:] with b[:n + lag]
            overlap_a = a[-lag:]
            overlap_b = b[:n + lag]

        if len(overlap_a) < 3:
            continue

        denom = np.sqrt(np.sum(overlap_a ** 2) * np.sum(overlap_b ** 2))
        if denom == 0:
            continue
        r = float(np.sum(overlap_a * overlap_b) / denom)

        if abs(r) > abs(best_r):
            best_r = r
            best_lag = lag

    return best_lag, best_r


def _lag_to_label(lag_windows, window_size):
    """Convert lag count to human-readable label."""
    n = abs(lag_windows)

    if window_size == "Hourly":
        if n <= 1:
            return "~1 hour"
        elif n <= 24:
            return f"~{n} hours"
        else:
            return f"~{n // 24} days"

    if window_size == "Daily":
        if n <= 1:
            return "~1 day"
        elif n <= 7:
            return f"~{n} days"
        else:
            return f"~{n // 7} weeks"

    if window_size == "Weekly":
        days = n * 7
    elif window_size == "Biweekly":
        days = n * 14
    else:  # Monthly
        days = n * 30

    if days <= 7:
        return "~1 week"
    elif days <= 14:
        return "~2 weeks"
    elif days <= 21:
        return "~3 weeks"
    elif days <= 35:
        return "~1 month"
    else:
        return f"~{days} days"


def compute_temporal_leads(correlations_result, window_size, max_lag=4):
    """
    Detect which TRC metrics lead (precede) others in time.

    For each significant correlation, computes cross-correlation at multiple lags.
    Augments each correlation dict in-place with lead_lag information.

    Returns the augmented correlations_result dict.
    """
    correlations = correlations_result.get("correlations", [])

    for corr in correlations:
        series_a = corr.get("series_a", [])
        series_b = corr.get("series_b", [])

        if len(series_a) < max_lag + 2:
            corr["lead_lag"] = None
            continue

        best_lag, best_r = _find_lead_lag(series_a, series_b, max_lag)

        # Check zero-lag correlation for comparison
        _, zero_r = _find_lead_lag(series_a, series_b, 0)

        # Only report lead-lag if peak significantly exceeds zero-lag
        if best_lag != 0 and abs(best_r) > abs(zero_r) + 0.1:
            if best_lag > 0:
                leader = corr["trc_a"]
                follower = corr["trc_b"]
                leader_metric = corr["metric_a"]
                follower_metric = corr["metric_b"]
            else:
                leader = corr["trc_b"]
                follower = corr["trc_a"]
                leader_metric = corr["metric_b"]
                follower_metric = corr["metric_a"]

            lag_label = _lag_to_label(best_lag, window_size)

            corr["lead_lag"] = {
                "best_lag": best_lag,
                "best_correlation": round(best_r, 3),
                "leader": leader,
                "follower": follower,
                "leader_metric": leader_metric,
                "follower_metric": follower_metric,
                "lag_windows": abs(best_lag),
                "lag_label": lag_label,
                "interpretation": (
                    f"{leader} {leader_metric} leads {follower} "
                    f"{follower_metric} by {lag_label} (r={best_r:.2f})"
                ),
            }
        else:
            corr["lead_lag"] = None  # Simultaneous or no meaningful lag

    return correlations_result


# ═══════════════════════════════════════════
#  DRILL-DOWN HELPERS
# ═══════════════════════════════════════════

def get_tickets_for_term(db, term, date_start, date_end, trc_filter, limit=50):
    """Return conversations containing a given term.

    For multi-word terms (e.g. compound terms displayed with spaces), searches
    for ALL constituent words appearing in the thread text. For single words,
    uses direct substring matching.
    """
    conditions = ["c.created_at >= ?", "c.created_at <= ?"]
    params = [date_start, date_end]

    if trc_filter:
        conditions.append("c.trc_code = ?")
        params.append(trc_filter)

    where = " AND ".join(conditions)

    query = f"""
        SELECT c.ticket_id, c.subject, c.trc_code, c.status,
               c.csat_score, c.created_at, c.thread_preview, c.full_thread
        FROM conversations c
        WHERE {where}
        ORDER BY c.created_at DESC
    """

    rows = db.conn.execute(query, params).fetchall()

    # For multi-word terms, check that ALL words appear in the thread
    term_lower = term.lower().strip()
    words = term_lower.split()
    matches = []
    for r in rows:
        thread = (r["full_thread"] or "").lower()
        if len(words) <= 1:
            # Single word: direct substring match
            if term_lower and term_lower in thread:
                matches.append(dict(r))
        else:
            # Multi-word: all words must appear in the thread
            if all(w in thread for w in words):
                matches.append(dict(r))
        if len(matches) >= limit:
            break

    return matches


def get_tickets_by_ids(db, ticket_ids, limit=50):
    """Return conversations by ticket IDs."""
    if not ticket_ids:
        return []

    ids = ticket_ids[:limit]
    placeholders = ",".join("?" * len(ids))
    query = f"""
        SELECT ticket_id, subject, trc_code, status,
               csat_score, created_at, thread_preview, full_thread
        FROM conversations
        WHERE ticket_id IN ({placeholders})
        ORDER BY created_at DESC
    """
    rows = db.conn.execute(query, ids).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════
#  SHARED HELPERS
# ═══════════════════════════════════════════

def _fetch_conversations(conn, date_start, date_end, trc_filter):
    """Fetch conversations for the given date range and optional TRC filter."""
    conditions = ["created_at >= ?", "created_at <= ?"]
    params = [date_start, date_end]

    if trc_filter:
        conditions.append("trc_code = ?")
        params.append(trc_filter)

    where = " AND ".join(conditions)
    query = f"""
        SELECT ticket_id, subject, trc_code, status, csat_score, created_at, full_thread
        FROM conversations
        WHERE {where}
        ORDER BY created_at
    """
    return [dict(r) for r in conn.execute(query, params).fetchall()]


# ═══════════════════════════════════════════
#  UNIFIED ANALYSIS PIPELINE
# ═══════════════════════════════════════════

def _empty_full_result():
    """Skeleton result dict when no conversations found."""
    return {
        "sentiment": {},
        "terms": {"rising": [], "cooling": []},
        "topics": {"topics": [], "multi_topic_tickets": [], "method": "nmf"},
        "correlations": {"correlations": [], "per_trc_series": {}},
    }


def run_full_analysis(conn, date_start, date_end, trc_filter, window_size,
                      topic_method="nmf", progress_callback=None, db=None):
    """
    Run the complete analysis pipeline in one pass.

    Steps share intermediate data (conversations, TF-IDF matrix, per-TRC series)
    to avoid redundant computation.

    Args:
        conn: SQLite connection (created in worker thread)
        date_start, date_end: Date range strings
        trc_filter: TRC code or None
        window_size: "Hourly" | "Daily" | "Weekly" | "Biweekly" | "Monthly"
        topic_method: "nmf" (default) or "kmeans"
        progress_callback: callable(step, total_steps, message)
        db: Optional DatabaseManager instance (enables Pass 1.5 features:
            dynamic compounds, feedback weights, compound discovery)

    Returns dict with keys: sentiment, terms, topics, correlations, discovery
    """
    total_steps = 12

    def _progress(step, msg):
        if progress_callback:
            progress_callback(step, total_steps, msg)

    # ── Step 1: NLTK data ──
    _progress(1, "Preparing language data...")
    _ensure_nltk_data()

    # ── Step 2: Load active compounds (Pass 1.5) ──
    _progress(2, "Loading compound terms...")
    active_compounds = None  # None = use built-in COMPOUND_TERMS
    if db is not None:
        try:
            from src.data.compound_discovery import get_active_compounds
            active_compounds = get_active_compounds(db)
        except Exception:
            pass  # Graceful fallback to built-in compounds

    # ── Step 3: Fetch conversations (single query, shared) ──
    _progress(3, "Fetching conversations...")
    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
    if not conversations:
        return _empty_full_result()

    # ── Step 4: Preprocess + sentiment ──
    _progress(4, "Preprocessing text and scoring sentiment...")
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    sia = SentimentIntensityAnalyzer()

    texts = []
    texts_meta = []
    for conv in conversations:
        cleaned = _clean_thread_text(conv.get("full_thread", ""), compounds=active_compounds)
        tokens = _tokenize_and_clean(cleaned)
        text = " ".join(tokens)

        # Compute sentiment for every conversation (used by sentiment_trends)
        sentiment = sia.polarity_scores(cleaned)["compound"] if cleaned.strip() else 0.0
        conv["_sentiment"] = sentiment

        if text.strip():
            texts.append(text)
            texts_meta.append({
                "ticket_id": conv["ticket_id"],
                "subject": conv.get("subject", ""),
                "csat_score": conv.get("csat_score"),
                "sentiment": sentiment,
                "created_at": conv.get("created_at", ""),
                "trc_code": conv.get("trc_code", ""),
            })

    # ── Step 5: Build shared TF-IDF matrix (per-document, for topics) ──
    _progress(5, "Building TF-IDF matrix...")
    from sklearn.feature_extraction.text import TfidfVectorizer
    tfidf_matrix = None
    feature_names = None

    if len(texts) >= 20:
        vectorizer = TfidfVectorizer(
            max_features=5000, min_df=2, max_df=0.85,
            ngram_range=(1, 3), token_pattern=r'(?u)\b\w[\w-]+\b',
        )
        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()
        except ValueError:
            pass

    # ── Step 5b: Embedding clusters (P2.1b, optional) ──
    embedding_clusters = None
    try:
        from src.data.embedding_engine import is_available as _emb_available
        if _emb_available() and texts:
            from src.data.embedding_engine import embed_texts, compute_embedding_clusters
            _progress(5, "Computing semantic embeddings...")
            embeddings = embed_texts(texts)
            embedding_clusters = compute_embedding_clusters(embeddings)
    except Exception:
        pass  # Graceful degradation — embeddings are optional

    # ── Step 6: Sentiment trends ──
    _progress(6, "Analyzing sentiment trends...")
    sentiment_result = compute_sentiment_trends(conn, date_start, date_end, trc_filter, window_size)

    # ── Step 7: Rising/cooling terms (uses own per-window TF-IDF + feedback weights) ──
    _progress(7, "Detecting rising & cooling terms...")
    terms = compute_rising_terms(
        conn, date_start, date_end, trc_filter, window_size,
        conn_for_feedback=conn if db is not None else None,
    )

    # ── Step 8: Topic modeling (NMF or K-Means) ──
    _progress(8, "Discovering topic clusters...")
    if topic_method == "nmf" and tfidf_matrix is not None:
        topics = compute_topic_model(
            conn, date_start, date_end, trc_filter, window_size,
            tfidf_matrix=tfidf_matrix, feature_names=feature_names,
            texts_meta=texts_meta,
        )
    else:
        topics = compute_topic_clusters(conn, date_start, date_end, trc_filter)

    # ── Step 9: Build per-TRC time series ──
    _progress(9, "Building cross-TRC time series...")
    # For cross-TRC, we need ALL conversations (no TRC filter)
    if trc_filter:
        all_conversations = _fetch_conversations(conn, date_start, date_end, None)
        # Score sentiment for newly fetched conversations
        for conv in all_conversations:
            if "_sentiment" not in conv:
                cleaned = _clean_thread_text(conv.get("full_thread", ""), compounds=active_compounds)
                conv["_sentiment"] = sia.polarity_scores(cleaned)["compound"] if cleaned.strip() else 0.0
    else:
        all_conversations = conversations  # Already have all TRCs

    per_trc_series = _build_per_trc_series(all_conversations, window_size)

    # ── Step 10: Cross-TRC correlations ──
    _progress(10, "Computing cross-TRC correlations...")
    correlations = compute_cross_trc_correlations(
        conn, date_start, date_end, window_size,
        per_trc_series=per_trc_series,
    )

    # ── Step 11: Temporal lead-lag ──
    _progress(11, "Detecting temporal lead-lag patterns...")
    correlations = compute_temporal_leads(correlations, window_size)

    # ── Step 12: Discover new compound candidates (Pass 1.5) ──
    discovery_result = None
    if db is not None:
        _progress(12, "Discovering new compound terms...")
        try:
            from src.data.compound_discovery import discover_compounds, persist_discoveries
            existing = set((active_compounds or COMPOUND_TERMS).keys())
            candidates = discover_compounds(conversations, existing_compounds=existing)
            if candidates:
                persist_discoveries(db, candidates)
            discovery_result = {
                "candidates_found": len(candidates),
                "top_candidates": candidates[:10],
            }
        except Exception:
            discovery_result = {"candidates_found": 0, "top_candidates": []}
    else:
        _progress(12, "Finalizing analysis...")

    return {
        "sentiment": sentiment_result,
        "terms": terms,
        "topics": topics,
        "correlations": correlations,
        "discovery": discovery_result,
        "embedding_clusters": embedding_clusters,
    }


# ═══════════════════════════════════════════════════════════════════
#  P2.0 — HYPOTHESIS TESTING
# ═══════════════════════════════════════════════════════════════════

def _extract_concepts(hypothesis: str) -> list:
    """Extract concept tokens from free-text hypothesis."""
    import nltk
    text = hypothesis.lower()
    text = _apply_compound_terms(text)
    tokens = nltk.word_tokenize(text)
    english_stops = set()
    try:
        from nltk.corpus import stopwords
        english_stops = set(stopwords.words('english'))
    except Exception:
        pass
    all_stops = english_stops | DOMAIN_STOPWORDS

    concepts = []
    seen = set()
    for token in tokens:
        if len(token) < 3 or token in all_stops:
            continue
        concept = _concept_index.get(token, token)
        if concept not in seen:
            concepts.append(concept)
            seen.add(concept)
    return concepts


def _find_matching_tickets(conn, concepts, date_start, date_end) -> set:
    """Find ticket IDs matching the hypothesis concepts via keyword (+ semantic if available)."""
    from src.data.concept_map import DOMAIN_CONCEPTS

    keyword_matches = set()
    for concept in concepts:
        synonyms = DOMAIN_CONCEPTS.get(concept, {concept})
        for syn in synonyms:
            query = syn.replace("_", " ")
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT ticket_id FROM conversations
                    WHERE (full_thread LIKE ? OR subject LIKE ?)
                      AND created_at >= ? AND created_at <= ?
                """, (f"%{query}%", f"%{query}%", date_start, date_end))
                for row in cursor.fetchall():
                    keyword_matches.add(row[0])
            except Exception:
                pass

    try:
        from src.data.embedding_engine import is_available as _emb_available
        if _emb_available():
            from src.data.embedding_engine import embed_texts, semantic_search
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ticket_id, full_thread FROM conversations
                WHERE created_at >= ? AND created_at <= ?
                  AND full_thread IS NOT NULL AND full_thread != ''
                LIMIT 5000
            """, (date_start, date_end))
            rows = cursor.fetchall()
            if rows:
                ids = [r[0] for r in rows]
                texts = [r[1] for r in rows]
                embeddings = embed_texts(texts)
                query_text = " ".join(c.replace("_", " ") for c in concepts)
                sem_results = semantic_search(query_text, embeddings, ids, top_k=50)
                keyword_matches.update(tid for tid, score in sem_results if score > 0.3)
    except Exception:
        pass

    return keyword_matches


def _get_related_incidents(conn, matching_trcs: set, days: int = 90) -> dict:
    """Find incident flags for TRCs involved in the hypothesis."""
    try:
        from src.data.incident_engine import get_flag_history
        related = []
        for trc in matching_trcs:
            flags = get_flag_history(conn, trc_code=trc, days=days)
            related.extend(flags)
        if not related:
            return {"related_flags": [], "description": "No incident flags found for related TRCs"}
        return {
            "related_flags": related[:10],
            "description": (
                f"{len(related)} incident flags found across "
                f"{len(matching_trcs)} related TRCs in the last {days} days"
            ),
        }
    except Exception:
        return {"related_flags": [], "description": "Incident engine unavailable"}


def _score_evidence(concept_groups: dict, temporal: dict, correlation: dict, incidents: dict) -> str:
    score = 0
    total_matches = sum(len(g.get("ticket_ids", [])) for g in concept_groups.values())
    if total_matches >= 30:
        score += 2
    elif total_matches >= 10:
        score += 1

    if temporal and temporal.get("lead_lag"):
        r = abs(temporal["lead_lag"].get("peak_correlation", 0))
        if r > 0.7:
            score += 3
        elif r > 0.5:
            score += 2

    if correlation:
        r = abs(correlation.get("pearson_r", 0))
        if r > 0.7:
            score += 2
        elif r > 0.5:
            score += 1

    # Incident corroboration: independent statistical confirmation
    if incidents and incidents.get("related_flags"):
        theta_2_flags = [f for f in incidents["related_flags"] if f.get("theta_level") == 2]
        if theta_2_flags:
            score += 2
        elif incidents["related_flags"]:
            score += 1

    for g in concept_groups.values():
        if g.get("sentiment_avg", 0) < -0.2:
            score += 1

    if score >= 7:
        return "strong"
    if score >= 4:
        return "moderate"
    if score >= 2:
        return "weak"
    return "insufficient"


def test_hypothesis(
    conn,
    hypothesis: str,
    date_start: str,
    date_end: str,
    window_size: str = "weekly",
    progress_callback=None,
    gemini_client=None,
) -> dict:
    """
    Test a user hypothesis against ticket data.

    Args:
        conn: SQLite connection (created in worker thread)
        hypothesis: Free-text hypothesis string
        date_start, date_end: Date range strings (YYYY-MM-DD)
        window_size: Bucketing size for temporal analysis
        progress_callback: callable(step, total, message)
        gemini_client: Optional GeminiClient instance for AI synthesis

    Returns a rich evidence dict with gemini_synthesis if Gemini is available.
    """
    _ensure_nltk_data()

    def _prog(step, msg):
        if progress_callback:
            progress_callback(step, 8, msg)

    # 1. Extract concepts
    _prog(1, "Extracting concepts from hypothesis...")
    concepts = _extract_concepts(hypothesis)

    # 2. Find matching tickets
    _prog(2, "Searching for matching tickets...")
    matching_ids = _find_matching_tickets(conn, concepts, date_start, date_end)

    if not matching_ids:
        return {
            "hypothesis": hypothesis,
            "concepts_extracted": concepts,
            "matching_tickets": [],
            "concept_groups": {},
            "temporal_pattern": {},
            "correlation": {},
            "incident_signals": {},
            "sentiment_data": {},
            "evidence_strength": "insufficient",
            "gemini_synthesis": None,
        }

    # 3. Fetch matching conversations
    _prog(3, "Analyzing matching conversations...")
    placeholders = ",".join("?" * len(matching_ids))
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT ticket_id, subject, trc_code, full_thread, csat_score, created_at
        FROM conversations
        WHERE ticket_id IN ({placeholders})
    """, list(matching_ids))
    matching_tickets = [
        {
            "ticket_id": r[0], "subject": r[1] or "", "trc_code": r[2] or "",
            "full_thread": r[3] or "", "csat_score": r[4], "created_at": r[5] or "",
        }
        for r in cursor.fetchall()
    ]

    # 4. Build concept groups
    _prog(4, "Building concept groups...")
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    sia = SentimentIntensityAnalyzer()
    from src.data.concept_map import DOMAIN_CONCEPTS

    concept_groups = {}
    for concept in concepts:
        synonyms = DOMAIN_CONCEPTS.get(concept, {concept})
        group_tickets = []
        for t in matching_tickets:
            text = (t.get("full_thread", "") + " " + t.get("subject", "")).lower()
            if any(syn.replace("_", " ") in text for syn in synonyms):
                group_tickets.append(t)

        if group_tickets:
            sentiments = [sia.polarity_scores(t["full_thread"])["compound"]
                          for t in group_tickets if t.get("full_thread")]
            trc_counts = defaultdict(int)
            volume_by_window = defaultdict(int)
            for t in group_tickets:
                trc_counts[t["trc_code"]] += 1
                bucket = _bucket_single(t.get("created_at", ""), window_size)
                if bucket:
                    volume_by_window[bucket] += 1

            concept_groups[concept] = {
                "ticket_ids": [t["ticket_id"] for t in group_tickets],
                "trcs": dict(trc_counts),
                "sentiment_avg": float(np.mean(sentiments)) if sentiments else 0.0,
                "volume_by_window": sorted(volume_by_window.items()),
            }

    # 5. Incident corroboration
    _prog(5, "Checking incident flags...")
    matching_trcs = {t["trc_code"] for t in matching_tickets if t.get("trc_code")}
    incident_signals = _get_related_incidents(conn, matching_trcs)

    # 6. Temporal / correlation (simple inline computation)
    _prog(6, "Analyzing temporal patterns...")
    temporal_pattern = {}
    correlation = {}
    if len(concept_groups) >= 2:
        group_keys = list(concept_groups.keys())[:2]
        s1 = dict(concept_groups[group_keys[0]]["volume_by_window"])
        s2 = dict(concept_groups[group_keys[1]]["volume_by_window"])
        common_keys = sorted(set(s1) & set(s2))
        if len(common_keys) >= 4:
            v1 = [s1[k] for k in common_keys]
            v2 = [s2[k] for k in common_keys]
            try:
                from scipy.stats import pearsonr
                r, p = pearsonr(v1, v2)
                correlation = {
                    "pearson_r": float(r),
                    "p_value": float(p),
                    "interpretation": (
                        f"When '{group_keys[0].replace('_', ' ')}' rises, "
                        f"'{group_keys[1].replace('_', ' ')}' "
                        f"{'also rises' if r > 0 else 'falls'} "
                        f"(r={r:.2f}, p={p:.3f})"
                    ),
                }
            except Exception:
                pass

    # 7. Sentiment summary
    _prog(7, "Summarizing sentiment...")
    all_sentiments = []
    for t in matching_tickets:
        if t.get("full_thread"):
            s = sia.polarity_scores(t["full_thread"])["compound"]
            all_sentiments.append(s)
    sentiment_data = {}
    if all_sentiments:
        avg = float(np.mean(all_sentiments))
        sentiment_data = {
            "avg_compound": avg,
            "trend": "negative" if avg < -0.1 else "positive" if avg > 0.1 else "neutral",
        }

    # 8. Evidence strength + optional Gemini synthesis
    _prog(8, "Scoring evidence...")
    evidence_strength = _score_evidence(concept_groups, temporal_pattern, correlation, incident_signals)

    gemini_synthesis = None
    if gemini_client is not None:
        try:
            from src.gemini.prompts import build_hypothesis_prompt, SYSTEM_PROMPT
            evidence_for_prompt = {
                "matching_tickets": matching_tickets[:15],
                "temporal_pattern": temporal_pattern,
                "correlation": correlation,
                "incident_signals": incident_signals,
            }
            prompt = build_hypothesis_prompt(hypothesis, evidence_for_prompt)
            gemini_synthesis = gemini_client.generate(prompt, system_prompt=SYSTEM_PROMPT)
        except Exception as e:
            gemini_synthesis = f"[AI synthesis unavailable: {e}]"

    return {
        "hypothesis": hypothesis,
        "concepts_extracted": concepts,
        "matching_tickets": matching_tickets,
        "concept_groups": concept_groups,
        "temporal_pattern": temporal_pattern,
        "correlation": correlation,
        "incident_signals": incident_signals,
        "sentiment_data": sentiment_data,
        "evidence_strength": evidence_strength,
        "gemini_synthesis": gemini_synthesis,
    }


def _bucket_single(created_at: str, window_size: str) -> str:
    """Return the window bucket label for a single created_at timestamp."""
    if not created_at:
        return ""
    try:
        dt = datetime.strptime(created_at[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    ws = window_size.lower()
    if ws == "hourly":
        hour = created_at[11:13] if len(created_at) >= 13 else "00"
        return f"{created_at[:10]} {hour}:00"
    if ws == "daily":
        return created_at[:10]
    if ws == "weekly":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if ws == "biweekly":
        week_num = dt.isocalendar()[1]
        biweek = (week_num - 1) // 2 * 2 + 1
        return f"{dt.year}-BW{biweek:02d}"
    if ws == "monthly":
        return dt.strftime("%Y-%m")
    return created_at[:10]


# ═══════════════════════════════════════════
#  P3.0 — AI CLUSTER SMOOTHING
# ═══════════════════════════════════════════

def smooth_clusters_with_ai(topics, gemini_client):
    """Use Gemini to refine NMF topic cluster labels.

    For each cluster, sends the top terms and asks Gemini to evaluate:
    - coherent: bool (do the terms form a meaningful topic?)
    - label: str (better human-readable label)
    - merge_with: int | None (cluster index to merge with)
    - split_into: list | None (proposed sub-clusters)
    - confidence: float (0-1)

    Returns dict: {cluster_index: {coherent, label, merge_with, split_into, confidence}}
    """
    if not topics or not gemini_client:
        return {}

    # Build the prompt
    cluster_descriptions = []
    for i, topic in enumerate(topics):
        terms = topic.get("top_terms", [])
        term_str = ", ".join(
            t["term"] if isinstance(t, dict) else str(t)
            for t in terms[:8]
        )
        count = topic.get("count", 0)
        sentiment = topic.get("avg_sentiment", 0)
        cluster_descriptions.append(
            f"Cluster {i}: [{term_str}] ({count} tickets, sentiment={sentiment:+.2f})"
        )

    prompt = (
        "You are an RCM support analytics expert.\n\n"
        "Review these NMF topic clusters from healthcare support ticket analysis. "
        "For each cluster, evaluate coherence and suggest improvements.\n\n"
        "CLUSTERS:\n" + "\n".join(cluster_descriptions) + "\n\n"
        "Return ONLY valid JSON (no markdown fences):\n"
        "{\n"
        '  "0": {"coherent": true, "label": "Billing Denials", "merge_with": null, '
        '"split_into": null, "confidence": 0.85},\n'
        '  "1": ...\n'
        "}\n\n"
        "Rules:\n"
        "- Labels should be 2-4 word RCM-relevant phrases\n"
        "- Set coherent=false for clusters mixing unrelated topics\n"
        "- Only suggest merge_with if two clusters clearly overlap\n"
        "- Only suggest split_into if a cluster has distinct sub-themes\n"
        "- confidence 0-1 reflecting your certainty about the label"
    )

    try:
        import json
        response = gemini_client.generate(
            prompt, system_prompt="You are an RCM analytics expert. Return only JSON."
        )
        # Clean potential markdown fences
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        result = json.loads(text.strip())
        # Normalize keys to ints
        return {int(k): v for k, v in result.items()}
    except Exception:
        return {}


# ═══════════════════════════════════════════
#  P3.0 — AI KEYWORD SUGGESTIONS
# ═══════════════════════════════════════════

def suggest_keyword_improvements(terms, gemini_client):
    """Use Gemini to suggest keyword suppressions and concept map additions.

    Args:
        terms: dict with "all_terms" and "rising_terms" from run_full_analysis()
        gemini_client: GeminiClient instance

    Returns dict:
        {
            "suppress": [{"term": "...", "reason": "..."}, ...],
            "add_to_map": [{"term": "...", "concept_group": "...", "reason": "..."}, ...]
        }
    """
    if not terms or not gemini_client:
        return {"suppress": [], "add_to_map": []}

    all_terms = terms.get("all_terms", [])
    rising = terms.get("rising_terms", [])

    # Format terms for the prompt
    all_lines = []
    for t in all_terms[:40]:
        if isinstance(t, dict):
            all_lines.append(f"  {t.get('term', t.get('word', ''))}: "
                           f"score={t.get('score', t.get('tfidf', ''))}")
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            all_lines.append(f"  {t[0]}: score={t[1]:.4f}")

    rising_lines = []
    for t in rising[:20]:
        if isinstance(t, dict):
            rising_lines.append(f"  {t.get('term', '')}: velocity={t.get('velocity', '')}")
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            rising_lines.append(f"  {t[0]}: velocity={t[1]:.4f}")

    prompt = (
        "You are an RCM support analytics expert reviewing TF-IDF keyword results.\n\n"
        "TOP TERMS (TF-IDF):\n" + "\n".join(all_lines) + "\n\n"
        "RISING TERMS (velocity):\n" + "\n".join(rising_lines) + "\n\n"
        "Analyze these terms and return ONLY valid JSON (no markdown fences):\n"
        "{\n"
        '  "suppress": [\n'
        '    {"term": "...", "reason": "agent boilerplate / not actionable"},\n'
        "    ...\n"
        "  ],\n"
        '  "add_to_map": [\n'
        '    {"term": "...", "concept_group": "billing_denials", "reason": "domain variant"},\n'
        "    ...\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Suppress: agent scripted phrases (\"happy to help\", \"appreciate your patience\"), "
        "generic filler, non-RCM terms that add noise\n"
        "- Add to map: RCM domain terms that should be grouped with existing concepts "
        "(billing, claims, eligibility, credentialing, authorization, etc.)\n"
        "- Be conservative — only flag terms you're confident about\n"
        "- Max 10 suppress, max 5 add_to_map"
    )

    try:
        import json
        response = gemini_client.generate(
            prompt, system_prompt="You are an RCM analytics expert. Return only JSON."
        )
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        result = json.loads(text.strip())
        return {
            "suppress": result.get("suppress", [])[:10],
            "add_to_map": result.get("add_to_map", [])[:5],
        }
    except Exception:
        return {"suppress": [], "add_to_map": []}
