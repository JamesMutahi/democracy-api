# Recommendation System Settings
POST_RECOMMENDER_CONFIG = {
    "CACHE": {
        "KEY_PREFIX": "user_recs_",
        "TIMEOUT": 60 * 30,
        "TRENDING_TIMEOUT": 600,
    },

    "RECOMMENDATIONS": {
        "DEFAULT_LIMIT": 20,
        "SCORED_LIMIT": 50,
        "DIVERSITY_FACTOR": 0.08,
    },

    "TRENDING_POSTS": {
        "DEFAULT_LIMIT": 20,
        "WINDOW_DAYS": 7,
        "WEIGHTS": {
            "likes": 3.0,
            "bookmarks": 3.0,
            "clicks": 2.0,
            "views": 1.0,
            "reposts": 5.0,
        },
    },

    "TRENDING_HASHTAGS": {
        "DEFAULT_LIMIT": 10,
        "WINDOW_DAYS": 7,
        "CACHE_TIMEOUT": 600,
    },

    "TRENDING_WORDS": {
        "DEFAULT_LIMIT": 15,
        "WINDOW_DAYS": 7,
        "MIN_FREQUENCY": 3,
        "MIN_WORD_LENGTH": 4,
        "CACHE_TIMEOUT": 600,

        "STOP_WORDS": [
            "the", "and", "or", "but", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "up", "about", "into", "over",
            "after", "this", "that", "these", "those", "is", "are",
            "was", "were", "be", "been", "being", "have", "has", "had",
            "do", "does", "did", "will", "would", "shall", "should",
            "can", "could", "may", "might", "must", "a", "an", "i",
            "you", "he", "she", "it", "we", "they", "me", "him", "her",
            "us", "them", "my", "your", "his", "its", "our", "their",
            "not", "no", "yes", "if", "then", "else", "when", "where",
            "how", "what", "who", "which", "why", "all", "any", "both",
            "each", "few", "more", "most", "other", "some", "such",
            "than", "too", "very", "just", "now", "so", "as", "like",
            "get", "got", "make", "made", "one", "two", "three", "also",
            "because", "however", "although", "still", "even", "back",
            "well", "say",
        ],

        "EXCLUDED_WORDS": [
            "http",
            "https",
            "www",
            "com",
            "net",
            "org",
            "gov",
            "edu",
            "io",
            "co",
            "uk",
            "ca",
            "au",
            "de",
            "fr",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "pdf",
            "html",
            "php",
            "asp",
            "email",
            "mailto",
            "utm",
            "fbclid",
            "gclid",
        ],
    },

    "TRENDING_TOPICS": {
        "DEFAULT_LIMIT": 30,
        "WINDOW_DAYS": 7,
    },

    # Final recommendation score weights
    "SCORING_WEIGHTS": {
        "location": 0.22,
        "content_type": 0.16,
        "media": 0.10,
        "following": 0.14,
        "profile_visit": 0.09,
        "click": 0.07,
        "engagement": 0.06,
        "freshness": 0.09,
        "similarity": 0.05,
        "note_quality": 0.02,
    },

    # Engagement score used inside main recommendations
    "ENGAGEMENT_WEIGHTS": {
        "likes": 2.0,
        "bookmarks": 2.0,
        "views": 0.5,
        "reposts": 3.0,
    },

    "LOCATION_SCORES": {
        "WARD": 1.0,
        "CONSTITUENCY": 0.85,
        "COUNTY": 0.65,
        "DEFAULT": 0.45,
        "NO_LOCATION": 0.5,
    },

    "CONTENT_TYPE_SCORES": {
        "BALLOT": 0.95,
        "PETITION": 0.85,
        "BROADCAST": 0.80,
        "SURVEY": 0.75,
        "SECTION": 0.70,
        "DEFAULT": 0.40,
    },

    "MEDIA_SCORES": {
        "VIDEO": 0.95,
        "IMAGE": 0.85,
        "ANY_ASSET": 0.70,
        "DEFAULT": 0.35,
    },

    "FRESHNESS": {
        "RECENT_HOURS": 2,
        "DAY_HOURS": 24,
        "WEEK_HOURS": 168,
        "RECENT_SCORE": 1.0,
        "DAY_SCORE": 0.8,
        "WEEK_SCORE": 0.5,
        "OLD_SCORE": 0.2,
    },

    "SIMILARITY_SCORES": {
        "BALLOT": 0.75,
        "SURVEY": 0.75,
        "PETITION": 0.70,
        "BROADCAST": 0.80,
        "DEFAULT": 0.30,
    },

    "NOTE_QUALITY": {
        "MIN_HELPFUL_SCORE": 0.7,
    },
}

FOLLOW_RECOMMENDER_CONFIG = {
    "CACHE": {
        "KEY_PREFIX": "user_follow_recs_",
        "TIMEOUT": 60 * 60,  # 1 hour
    },

    "DEFAULT_LIMIT": 15,
    "SCORED_LIMIT": 80,
    "DIVERSITY_FACTOR": 0.10,

    # Final follow recommendation weights.
    # These should sum to 1.0.
    "WEIGHTS": {
        "location": 0.25,
        "mutual": 0.30,
        "profile_visit": 0.15,
        "engagement": 0.10,
        "recency": 0.15,
        "activity": 0.05,
    },

    "LOCATION_SCORES": {
        "WARD": 1.0,
        "CONSTITUENCY": 0.85,
        "COUNTY": 0.65,
        "DEFAULT": 0.45,
        "NO_LOCATION": 0.50,
    },

    "MUTUAL_SCORE_TIERS": [
        {"MIN_MUTUALS": 5, "SCORE": 1.0},
        {"MIN_MUTUALS": 3, "SCORE": 0.85},
        {"MIN_MUTUALS": 1, "SCORE": 0.65},
    ],

    "PROFILE_VISIT": {
        "SCORE": 0.85,
    },

    "RECENCY": {
        "ACTIVE_DAYS": 7,
        "ACTIVE_SCORE": 0.8,
        "SEMI_ACTIVE_DAYS": 30,
        "SEMI_ACTIVE_SCORE": 0.5,
        "DEFAULT_SCORE": 0.2,
    },

    # Engagement is normalized/capped so it cannot dominate the score.
    "ENGAGEMENT": {
        "LIKE_WEIGHT": 0.6,
        "CLICK_WEIGHT": 0.4,
        "CAP": 1.0,
    },

    # Activity is normalized/capped.
    # Example: 20 published posts => activity score of 1.0.
    "ACTIVITY": {
        "PUBLISHED_POST_CAP": 20,
    },
}