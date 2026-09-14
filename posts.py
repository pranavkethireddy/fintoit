# posts.py — the single source of truth for all blog posts.
# To add a new post:
#   1. Add an entry here
#   2. Create templates/posts/<slug>.html with the content
#   That's it. Search, filters, and cards are all automatic.

POSTS = [
    {
        "slug": "how-to-read-a-balance-sheet",
        "title": "How to Read a Balance Sheet (Without an Accounting Degree)",
        "excerpt": "Assets, liabilities, equity what they actually mean for your startup and how to spot warning signs in under 10 minutes.",
        "category": "Accounting",
        "category_slug": "accounting",
        "tag_style": "blue",
        "v_image": "balance-sheet.jpeg",
        "emoji": "📊",
        "visual_class": "other.png",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "June 15, 2026",
        "read_time": "7 min read",
        "featured": False,
    },
    {
        "slug": "what-is-mrr-and-how-to-grow-it",
        "title": "What Is MRR and How Do You Actually Grow It?",
        "excerpt": "Monthly Recurring Revenue is the number every SaaS investor will ask about first. Here's how to calculate it correctly and the levers that move it.",
        "category": "Accounting",
        "category_slug": "accounting",
        "tag_style": "blue",
        "v_image": "mrr-growth.png",
        "emoji": "📈",
        "visual_class": "green-bg",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "June 19, 2026",
        "read_time": "6 min read",
        "featured": False,
    },
        {
        "slug": "burn-rate-and-runway",
        "title": "Burn Rate and Runway: The Two Numbers That Keep Founders Up at Night",
        "excerpt": "How to calculate burn rate correctly, predict your runway with confidence, and avoid the cash crunch CB Insights found in 70% of recent shutdowns.",
        "category": "Forecasting",
        "category_slug": "forecasting",
        "tag_style": "purple",
        "v_image": "burn-rate.png",
        "emoji": "🔥",
        "visual_class": "dark-bg",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "July 3, 2026",
        "read_time": "12 min read",
        "featured": False,
    },
    {
        "slug": "scenario-planning-hiring-cashflow",
        "title": "Scenario Planning: What Happens to Your Cash When You Hire?",
        "excerpt": "Before you make your next hire, run the numbers. We break down exactly how adding engineers, sales reps, or contractors changes your runway — and how to model it like a CFO.",
        "category": "Forecasting",
        "category_slug": "forecasting",
        "tag_style": "purple",
        "v_image": "hiring-scenario.jpeg",
        "emoji": "🔭",
        "visual_class": "dark-bg",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "July 15, 2026",
        "read_time": "10 min read",
        "featured": False,
    },
    {
        "slug": "unit-economics-cac-ltv",
        "title": "Unit Economics: CAC, LTV, and Payback Period Explained",
        "excerpt": "The three numbers that tell investors whether your startup can actually scale — and how founders miscalculate them every day.",
        "category": "Accounting",
        "category_slug": "accounting",
        "tag_style": "blue",
        "v_image": "unit-economics.jpeg",
        "emoji": "🧮",
        "visual_class": "other.png",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "July 24, 2026",
        "read_time": "11 min read",
        "featured": False,
    },
    {
        "slug": "what-goes-in-a-board-report",
        "title": "What Actually Goes in a Board Report at Pre-Seed and Seed",
        "excerpt": "Investors want a monthly email that takes two minutes to read, not a deck. The four numbers it has to contain, the three sections that carry the story, and a filled-in example.",
        "category": "Accounting",
        "category_slug": "accounting",
        "tag_style": "blue",
        "v_image": "board-report.svg",
        "emoji": "📋",
        "visual_class": "blue-bg",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "August 18, 2026",
        "read_time": "7 min read",
        "featured": False,
    },
    {
        "slug": "how-much-to-raise-and-runway-before-raising",
        "title": "How Much Should You Raise, and How Much Runway Do You Need Before Raising Again?",
        "excerpt": "Most founders copy the market to pick a round size. It is derivable instead, from four inputs you already have. Here is the arithmetic, on a worked example.",
        "category": "Fundraising",
        "category_slug": "fundraising",
        "tag_style": "green",
        "v_image": "how-much-to-raise.svg",
        "emoji": "\U0001f9ee",
        "visual_class": "blue-bg",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "August 18, 2026",
        "read_time": "10 min read",
        "featured": False,
    },
    {
        "slug": "board-report-template",
        "title": "A Board Report Template You Can Fill In Today",
        "excerpt": "A blank template you can copy, plus two filled examples: a pre-revenue company with nothing to put on the revenue line, and a seed company in a month the numbers went the wrong way.",
        "category": "Accounting",
        "category_slug": "accounting",
        "tag_style": "blue",
        "v_image": "board-report-template.svg",
        "emoji": "\U0001f4dd",
        "visual_class": "blue-bg",
        "image": "other.png",
        "author": "Fintoit Team",
        "date": "September 2, 2026",
        "read_time": "10 min read",
        "featured": False,
    },
]

# Derive category list automatically from posts
CATEGORIES = ["All"] + sorted(set(p["category"] for p in POSTS))


def search_posts(query="", category=""):
    """Filter posts by search query and/or category. Returns list of matching posts."""
    results = POSTS

    if category and category.lower() != "all":
        results = [p for p in results if p["category"].lower() == category.lower()]

    if query:
        q = query.lower()
        results = [
            p for p in results
            if q in p["title"].lower()
            or q in p["excerpt"].lower()
            or q in p["category"].lower()
        ]

    return results


def get_post(slug):
    """Return a single post by slug, or None."""
    return next((p for p in POSTS if p["slug"] == slug), None)


def get_related(post, n=3):
    """Return up to n posts in the same category, excluding the current post."""
    same_cat = [p for p in POSTS if p["category"] == post["category"] and p["slug"] != post["slug"]]
    other = [p for p in POSTS if p["category"] != post["category"]]
    combined = same_cat + other
    return combined[:n]