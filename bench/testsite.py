"""A local test site for the benchmark: the page types that broke real runs, with made-up data.

Every name here is invented. Pages:
  /people      search with a results list, pagination, a filter drawer without "Experience level",
               and a search box that returns "No results found" for quoted/parenthesised queries
               (the behaviour reported on LinkedIn in docs/reviews/2026-10-07-linkedin-findings.md)
  /catalog     60 products in a virtualised list: only the rows near the screen exist in the DOM
  /catalog/item/<n>   one product
  /companies   8 companies; their founding year and HQ are only on each company's own page
"""
import html
import json
import re

from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Route

# ---------------------------------------------------------------- data (fixed, invented)

ACME = "Acme Mobility"
PEOPLE = [
    # (name, title, company, location)
    ("Mira Okafor", "Technical Recruiter", ACME, "Austin, Texas"),
    ("Devin Castellano", "Software Engineer II", ACME, "Austin, Texas"),
    ("Hana Lindqvist", "Product Designer", ACME, "Seattle, Washington"),
    ("Tobias Wren", "Senior Talent Acquisition Partner", ACME, "Denver, Colorado"),
    ("Priyanka Solanki", "Software Engineer, Recruiting Platform", ACME, "Austin, Texas"),
    ("Jonah Abernathy", "Data Scientist", ACME, "Chicago, Illinois"),
    ("Lucia Ferreira", "Senior Recruiter", ACME, "Miami, Florida"),
    ("Kwame Mensah", "Engineering Manager", ACME, "Austin, Texas"),
    ("Sofia Brandt", "Product Manager", ACME, "Seattle, Washington"),
    ("Rafael Quintero", "University Talent Acquisition Lead", ACME, "Austin, Texas"),
    ("Elena Varga", "Staff Software Engineer", ACME, "Remote"),
    ("Omar Haddad", "Recruiter", ACME, "Phoenix, Arizona"),
    ("Grace Whitfield", "Marketing Manager", ACME, "New York, New York"),
    ("Ines Moreau", "HR Business Partner", ACME, "Denver, Colorado"),
    ("Callum Reyes", "Site Reliability Engineer", ACME, "Austin, Texas"),
    ("Yuki Tanabe", "Sourcing Recruiter", ACME, "Seattle, Washington"),
    ("Nadia Petrov", "Security Engineer", ACME, "Remote"),
    ("Samuel Achebe", "Finance Analyst", ACME, "Chicago, Illinois"),
    ("Freya Holm", "Talent Acquisition Coordinator", ACME, "Austin, Texas"),
    ("Mateo Silva", "Frontend Engineer", ACME, "Miami, Florida"),
    ("Aisha Rahman", "Legal Counsel", ACME, "New York, New York"),
    ("Pieter de Vries", "Executive Recruiter", ACME, "Boston, Massachusetts"),
    ("Chloe Martin", "UX Researcher", ACME, "Seattle, Washington"),
    ("Arjun Mehta", "Machine Learning Engineer", ACME, "Austin, Texas"),
    ("Beatrix Nolan", "Customer Success Manager", ACME, "Denver, Colorado"),
    ("Ravi Shankar", "Head of Talent Acquisition", ACME, "Austin, Texas"),
    ("Lena Fischer", "Operations Manager", ACME, "Phoenix, Arizona"),
    ("Diego Alvarez", "Mobile Engineer", ACME, "Remote"),
    ("Hugo Lambert", "Recruiting Operations Analyst", ACME, "Austin, Texas"),
    ("Zara Ibrahim", "Technical Recruiter", ACME, "Toronto, Ontario"),
    # other companies (distractors for searches without a company filter)
    ("Marcus Bell", "Technical Recruiter", "Borealis Pay", "Austin, Texas"),
    ("Ana Costa", "Talent Acquisition Partner", "Borealis Pay", "Lisbon, Portugal"),
    ("Felix Ortmann", "Senior Recruiter", "Kestrel Health", "Berlin, Germany"),
    ("Imogen Clarke", "Software Engineer", "Kestrel Health", "London, England"),
    ("Theo Nakamura", "Recruiter (formerly Acme Mobility)", "Borealis Pay", "Seattle, Washington"),
]


def is_target(title, company):
    return company == ACME and re.search(r"Recruiter|Talent Acquisition", title) is not None


PRODUCTS = []
for i in range(1, 61):
    cents = (i * 1373) % 4200 + 499          # deterministic spread of prices, $4.99 … $46.98
    PRODUCTS.append({"id": i, "name": f"{['Field', 'Harbor', 'Summit', 'Cedar', 'Pixel', 'Atlas'][i % 6]} "
                                       f"{['Lamp', 'Kettle', 'Backpack', 'Notebook', 'Speaker', 'Mug', 'Clock', 'Scarf', 'Bottle', 'Planter'][i % 10]} {i}",
                     "price": f"${cents // 100}.{cents % 100:02d}", "cents": cents, "rating": f"{3 + (i * 7) % 20 / 10:.1f}"})

COMPANIES = [
    ("Borealis Pay", 1998, "Oslo"), ("Kestrel Health", 2004, "Leeds"), ("Juniper Freight", 1987, "Rotterdam"),
    ("Moonrise Labs", 2015, "Wellington"), ("Tamarind Foods", 1992, "Pune"), ("Vantage Rail", 1979, "Lyon"),
    ("Quillworks", 2011, "Portland"), ("Saltmarsh Energy", 2001, "Aberdeen"),
]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- pages

STYLE = """<style>body{font-family:system-ui,sans-serif;margin:0;background:#f3f2ef}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 20px;z-index:5;display:flex;gap:12px;align-items:center}
main{max-width:760px;margin:16px auto;background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 20px}
.card{border-bottom:1px solid #eee;padding:12px 0}.name{font-weight:600}.muted{color:#666;font-size:14px}
button{padding:6px 12px}#drawer{display:none;border:1px solid #ccc;padding:12px;margin:8px 0;background:#fafafa}
.pager a{margin:0 6px}</style>"""


def page(title, body):
    return HTMLResponse(f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(title)}</title>{STYLE}</head>"
                        f"<body>{body}</body></html>")


def search(q, company):
    """Plain keywords: every word must match (OR joins neighbours). Quotes/parentheses: no results."""
    if re.search(r'["()]', q):
        return None
    words = [w for w in q.split() if w.upper() not in ("AND", "NOT")]
    groups, i = [], 0
    while i < len(words):
        g = [words[i]]
        while i + 2 < len(words) and words[i + 1].upper() == "OR":
            g.append(words[i + 2])
            i += 2
        groups.append([w.lower() for w in g if w.upper() != "OR"])
        i += 1
    out = []
    for p in PEOPLE:
        if company and slug(p[2]) != company:
            continue
        hay = " ".join(p).lower()
        if all(any(w.rstrip("s") in hay for w in g) for g in groups if g):
            out.append(p)
    return out


async def people(request):
    q = request.query_params.get("keywords", "").strip()
    company = request.query_params.get("company", "")
    n = max(1, int(request.query_params.get("page", "1") or 1))
    header = (f"<header><b>Peoplebook</b><form action='/people'><input name=keywords size=40 value='{html.escape(q, quote=True)}' "
              f"placeholder='Search people'>{f'<input type=hidden name=company value={company}>' if company else ''}"
              f"<button>Search</button></form></header>")
    if not q and not company:
        return page("Peoplebook", header + "<main><p>Search for people by name, title or company.</p></main>")
    found = search(q, company)
    drawer = ("<button onclick=\"document.getElementById('drawer').style.display='block'\">All filters</button>"
              "<div id=drawer><form action='/people'><input type=hidden name=keywords value='" + html.escape(q, quote=True) + "'>"
              "<p><b>Current company</b><br>" + "".join(
                  f"<label><input type=radio name=company value={slug(c)}> {c}</label><br>" for c in sorted({p[2] for p in PEOPLE}))
              + "</p><p><b>Locations</b><br><label><input type=checkbox disabled> United States</label><br>"
              "<label><input type=checkbox disabled> Canada</label></p><p><b>Industry</b><br>"
              "<label><input type=checkbox disabled> Software</label><br><label><input type=checkbox disabled> Transportation</label></p>"
              "<p><b>School</b><br><input disabled placeholder='Add a school'></p><button>Show results</button></form></div>")
    if not found:
        return page("Search results", header + f"<main>{drawer}<h3>No results found</h3><p class=muted>Try shortening or "
                    "rephrasing your search.</p></main>")
    per = 10
    pages = (len(found) + per - 1) // per
    rows = "".join(f"<div class=card><div class=name>{html.escape(p[0])}</div><div>{html.escape(p[1])} at {html.escape(p[2])}</div>"
                   f"<div class=muted>{html.escape(p[3])}</div><button>Connect</button></div>"
                   for p in found[(n - 1) * per:n * per])

    def link(k, label):
        return f"<a href='/people?keywords={html.escape(q, quote=True)}{'&company=' + company if company else ''}&page={k}'>{label}</a>"
    pager = "<div class=pager>" + (link(n - 1, "Previous") if n > 1 else "") + "".join(
        link(k, str(k)) if k != n else f"<b>{k}</b>" for k in range(1, pages + 1)) + (link(n + 1, "Next") if n < pages else "") + "</div>"
    return page("Search results", header + f"<main>{drawer}<p class=muted>About {len(found)} results</p>{rows}{pager}</main>")


async def catalog(request):
    data = json.dumps([{k: p[k] for k in ("id", "name", "price", "rating")} for p in PRODUCTS])
    return page("Catalog", """<header><b>Shopwise</b> catalog</header>
<div id=list style="position:relative;max-width:760px;margin:16px auto"></div>
<script>
const items = %s, H = 64, list = document.getElementById('list');
list.style.height = (items.length * H) + 'px';
function render() {   // like big feeds: only rows near the screen are in the page at all
  const top = window.scrollY - list.offsetTop, first = Math.max(0, Math.floor(top / H) - 3),
        last = Math.min(items.length, Math.ceil((top + innerHeight) / H) + 3);
  list.innerHTML = items.slice(first, last).map((p, k) => `<div class=card style="position:absolute;left:0;right:0;top:${(first + k) * H}px;height:${H - 8}px;background:#fff;padding:4px 16px">
    <a class=name href="/catalog/item/${p.id}">${p.name}</a> <span>${p.price}</span> <span class=muted>rating ${p.rating}</span></div>`).join('');
}
addEventListener('scroll', render); addEventListener('resize', render); render();
</script>""" % data)


async def item(request):
    p = PRODUCTS[int(request.path_params["n"]) - 1]
    return page(p["name"], f"<header><b>Shopwise</b></header><main><h2>{p['name']}</h2><p>Price: <b>{p['price']}</b></p>"
                f"<p>Customer rating: {p['rating']} out of 5</p><p class=muted>Ships in 2–4 days.</p></main>")


async def companies(request):
    rows = "".join(f"<div class=card><a class=name href='/companies/{slug(c)}'>{c}</a></div>" for c, _, _ in COMPANIES)
    return page("Companies", f"<header><b>Firmlist</b></header><main><h2>Companies</h2>{rows}</main>")


async def company(request):
    c = next(c for c in COMPANIES if slug(c[0]) == request.path_params["s"])
    return page(c[0], f"<header><b>Firmlist</b></header><main><h2>{c[0]}</h2><p>Founded: {c[1]}</p>"
                f"<p>Headquarters: {c[2]}</p><p class=muted>Employees: {len(c[0]) * 117}</p>"
                f"<a href='/companies'>Back to all companies</a></main>")


app = Starlette(routes=[Route("/people", people), Route("/catalog", catalog), Route("/catalog/item/{n:int}", item),
                        Route("/companies", companies), Route("/companies/{s}", company)])
