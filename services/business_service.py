import os

from sqlalchemy.orm import Session

from models.domain_models import BusinessConfigModel
from services.storage_service import public_logo_url
from utils.google_review import build_google_review_url, extract_place_id
from utils.slugs import RESERVED_SLUGS, feedback_path_for, slugify


def _get_default_businesses() -> dict:
    from dotenv import load_dotenv

    load_dotenv(override=True)
    return {
        "technobuzz": {
            "name": "TechnoBuzz",
            "id": "TECHNOBUZZ-001",
            "route_slug": "",
            "collection": "feedback",
            "place_id": extract_place_id(
                os.getenv(
                    "GOOGLE_REVIEW_URL",
                    "https://search.google.com/local/writereview?placeid=ChIJqaVAInOVwjsRUdO8dqmpR68",
                )
            ),
            "logo_filename": "images/technobuzz_logo.jpg",
            "scope": "Software development, web design, cloud infrastructure, network architecture, cybersecurity, and managed IT support.",
            "examples_1star": "buggy deployments or broken code, critical downtime due to poor infrastructure planning, missed project deadlines, lack of communication from the support team, security vulnerabilities left unpatched, unprofessional conduct or incompetence",
            "examples_2star": "slow response to support tickets, complex UI/UX in web design",
            "examples_3star": "technical team is skilled but the project management is disorganized, billing confusion or slow response",
            "examples_4star": "better documentation, more frequent status updates",
            "examples_5star": "seamless cloud migration, reliable network uptime, intuitive design interface, proactive security measures, knowledgeable engineering team, project delivered ahead of schedule",
        },
        "boardwale": {
            "name": "Boardwale",
            "id": "BOARDWALE-001",
            "route_slug": "board_001",
            "collection": "boardwale_feedback",
            "place_id": extract_place_id(
                os.getenv(
                    "BOARDWALE_GOOGLE_REVIEW_URL",
                    "https://search.google.com/local/writereview?placeid=ChIJx_7z8zCVwjsRnY-8LbFss_c",
                )
            ),
            "logo_filename": "images/boardwale_logo.png",
            "scope": "3D signage, indoor boards, outdoor boards, custom business signage solutions, printing, finishing, and installation.",
            "examples_1star": "poor material quality, delayed installation, spelling errors on prints, peeling signs, unresponsive customer service",
            "examples_2star": "incorrect colors used, rough finishing on edges, slow communication",
            "examples_3star": "design is good but installation was messy, decent boards but pricing was confusing",
            "examples_4star": "better communication during the design phase, faster installation",
            "examples_5star": "excellent 3D signage quality, professional finishing, on-time installation, great custom design, high-quality materials, team understood requirements perfectly",
        },
        "jawa_showroom": {
            "name": "Jawa Showroom",
            "id": "JAWA-SHOWROOM-001",
            "route_slug": "showroom_001",
            "collection": "jawa_showroom_feedback",
            "place_id": extract_place_id(os.getenv("JAWA_GOOGLE_REVIEW_URL", "")),
            "logo_filename": "images/jawa_logo.png",
            "scope": "Jawa motorcycle showroom and dealership experience including showroom experience, motorcycle consultation, sales support, test rides, purchase assistance, finance/EMI assistance, booking, delivery and customer service.",
            "examples_1star": "rude sales executives, terrible customer service, bikes unavailable for test ride, extremely delayed delivery, finance team was unhelpful, zero product knowledge",
            "examples_2star": "showroom was too crowded and disorganized, sales staff ignored us for a long time, booking process was confusing, test ride was rushed",
            "examples_3star": "average experience, staff was polite but didn't know much about the bike specs, delivery took longer than promised but the bike is good",
            "examples_4star": "great test ride experience, helpful staff during the finance process, good overall showroom ambiance, just a slight delay in paperwork",
            "examples_5star": "excellent dealership experience, very knowledgeable and polite sales executives, smooth EMI and exchange process, fantastic test ride arrangements, memorable delivery ceremony",
        },
        "rutuja_battery": {
            "name": "Rutuja Battery",
            "id": "RUTUJA-BATTERY-001",
            "route_slug": "1",
            "collection": "rutuja_battery_feedback",
            "place_id": extract_place_id(os.getenv("RUTUJA_GOOGLE_REVIEW_URL", "")),
            "logo_filename": "images/rutuja_logo.png",
            "scope": "Battery retail, inverter battery and automotive battery sales and service business in Dhayari and Manaji Nagar, Pune. Authorized Exide dealer offering two-wheeler, car, and commercial vehicle batteries, inverter/UPS batteries, installation, replacement, testing, jump-start assistance, and warranty support.",
            "examples_1star": "poor service, delayed installation, overpriced, sold a faulty battery, communication issues, unhelpful staff, ignored warranty claims",
            "examples_2star": "mixed experience, battery is fine but installation was delayed, average product, communication could be better, wait time was high",
            "examples_3star": "service is okay, pricing is slightly higher, installation was quick but staff could be more professional",
            "examples_4star": "good battery life, prompt service, helpful staff for battery checkup, reasonable prices",
            "examples_5star": "excellent and fast installation, very reliable service, best prices in the area, very polite staff, great after-sales support",
        },
        "wada_misal": {
            "name": "Wada Misal",
            "id": "WADA-MISAL-001",
            "route_slug": "wada_misal",
            "collection": "wada_misal_feedback",
            "place_id": extract_place_id(os.getenv("WADA_MISAL_GOOGLE_REVIEW_URL", "")),
            "logo_filename": "images/wada_misal_logo.jpg",
            "scope": "Authentic Maharashtrian restaurant serving Misal Pav, Vada Pav, Poha, tea, and other traditional snacks. Dine-in, takeaway, and family-friendly eating experience.",
            "examples_1star": "terrible taste, very unhygienic place, slow service, cold food, found a bug in my misal, rude waiters",
            "examples_2star": "misal was too spicy with no flavor, pav was stale, tables were dirty, too crowded and noisy",
            "examples_3star": "average misal, nothing special. quantity is less for the price. okay for a quick snack",
            "examples_4star": "good authentic taste, nice ambiance, service was quick, clean environment",
            "examples_5star": "best misal in town, absolutely delicious and spicy! very hygienic, great family place, fast and polite service, definitely coming back",
        },
    }


def _serialize_business(b: BusinessConfigModel, salesman_names: dict = None) -> dict:
    from datetime import date as date_cls

    from services.plan_service import PLANS, plan_status

    place_id = b.place_id or extract_place_id(b.google_review_url or "")
    salesman_names = salesman_names or {}
    join_date = getattr(b, "join_date", None)
    expiry_date = getattr(b, "expiry_date", None)
    plan_code = getattr(b, "plan_code", "") or ""
    exec_id = getattr(b, "sales_executive_id", None)
    return {
        "name": b.name,
        "id": b.id,
        "route_slug": b.route_slug,
        "collection": b.collection,
        "place_id": place_id,
        "google_review_url": b.google_review_url or build_google_review_url(place_id),
        "logo_filename": b.logo_filename or "",
        "logo_url": public_logo_url(b.logo_filename or ""),
        "scope": b.scope,
        "examples_1star": b.examples_1star,
        "examples_2star": b.examples_2star,
        "examples_3star": b.examples_3star,
        "examples_4star": b.examples_4star,
        "examples_5star": b.examples_5star,
        "plan_code": plan_code,
        "plan_label": PLANS.get(plan_code, {}).get("label", ""),
        "plan_amount": getattr(b, "plan_amount", 0) or 0,
        "join_date": join_date.isoformat() if join_date else "",
        "expiry_date": expiry_date.isoformat() if expiry_date else "",
        "plan_status": plan_status(expiry_date),
        "sales_executive_id": exec_id,
        "salesman_name": salesman_names.get(exec_id, ""),
        "is_expired": bool(expiry_date and expiry_date < date_cls.today()),
        "key": b.key,
        "mobile": getattr(b, "mobile", "") or "",
        "alternate_mobile": getattr(b, "alternate_mobile", "") or "",
        "email": getattr(b, "email", "") or "",
        "address": getattr(b, "address", "") or "",
        "feedback_path": feedback_path_for(b.key, getattr(b, "route_slug", "") or ""),
        "payment_due": False,
        "franchise_id": getattr(b, "franchise_id", None),
        "area": getattr(b, "area", "") or "",
    }


BUSINESS_PAGE_SIZES = (12, 24, 48, 100)
DEFAULT_BUSINESS_PAGE_SIZE = 100


def _serialize_business_card(b: BusinessConfigModel, salesman_names: dict = None) -> dict:
    """Compact row for the businesses grid — no long scope/example fields."""
    from datetime import date as date_cls

    from services.plan_service import PLANS, plan_status

    salesman_names = salesman_names or {}
    join_date = getattr(b, "join_date", None)
    expiry_date = getattr(b, "expiry_date", None)
    plan_code = getattr(b, "plan_code", "") or ""
    exec_id = getattr(b, "sales_executive_id", None)
    return {
        "key": b.key,
        "name": b.name,
        "id": b.id,
        "route_slug": b.route_slug or "",
        "plan_code": plan_code,
        "plan_label": PLANS.get(plan_code, {}).get("label", ""),
        "plan_amount": getattr(b, "plan_amount", 0) or 0,
        "join_date": join_date.isoformat() if join_date else "",
        "expiry_date": expiry_date.isoformat() if expiry_date else "",
        "plan_status": plan_status(expiry_date),
        "sales_executive_id": exec_id,
        "salesman_name": salesman_names.get(exec_id, ""),
        "is_expired": bool(expiry_date and expiry_date < date_cls.today()),
        "mobile": getattr(b, "mobile", "") or "",
        "alternate_mobile": getattr(b, "alternate_mobile", "") or "",
        "email": getattr(b, "email", "") or "",
        "address": getattr(b, "address", "") or "",
        "feedback_path": feedback_path_for(b.key, getattr(b, "route_slug", "") or ""),
        "payment_due": False,
        "franchise_id": getattr(b, "franchise_id", None),
        "area": getattr(b, "area", "") or "",
    }


def _page_numbers(current: int, pages: int) -> list:
    if pages <= 7:
        return list(range(1, pages + 1))
    pages_out = []
    if current <= 4:
        pages_out.extend(range(1, 6))
        pages_out.extend(["…", pages])
    elif current >= pages - 3:
        pages_out.extend([1, "…"])
        pages_out.extend(range(pages - 4, pages + 1))
    else:
        pages_out.extend([1, "…", current - 1, current, current + 1, "…", pages])
    return pages_out


def _businesses_query(
    db: Session,
    sales_executive_id: int = None,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    franchise_id: int = None,
):
    from sqlalchemy import or_

    q = db.query(BusinessConfigModel)
    if sales_executive_id:
        q = q.filter(BusinessConfigModel.sales_executive_id == sales_executive_id)
    if franchise_id:
        q = q.filter(BusinessConfigModel.franchise_id == franchise_id)
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(
            or_(
                BusinessConfigModel.name.ilike(like),
                BusinessConfigModel.key.ilike(like),
                BusinessConfigModel.id.ilike(like),
                BusinessConfigModel.route_slug.ilike(like),
                BusinessConfigModel.collection.ilike(like),
                BusinessConfigModel.mobile.ilike(like),
                BusinessConfigModel.alternate_mobile.ilike(like),
                BusinessConfigModel.email.ilike(like),
            )
        )
    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").date()
            q = q.filter(BusinessConfigModel.join_date >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").date()
            q = q.filter(BusinessConfigModel.join_date <= dt)
        except ValueError:
            pass
    return q


def list_businesses_page(
    db: Session,
    user=None,
    search: str = "",
    page: int = 1,
    per_page: int = DEFAULT_BUSINESS_PAGE_SIZE,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Paginated client grid. Loads one page only — safe at 3000+ rows."""
    from math import ceil

    from sqlalchemy import func
    from sqlalchemy.orm import load_only

    from models.domain_models import SalesExecutive
    from services.franchise_service import scope_for_user

    per_page = per_page if per_page in BUSINESS_PAGE_SIZES else DEFAULT_BUSINESS_PAGE_SIZE
    page = max(1, int(page or 1))
    search = (search or "").strip()[:80]

    scope = scope_for_user(db, user)
    exec_id = None
    franchise_id = None
    if scope["blocked"]:
        return {
            "rows": [],
            "total": 0,
            "page": 1,
            "pages": 0,
            "per_page": per_page,
            "search": search,
            "page_numbers": [],
            "start": 0,
            "end": 0,
        }
    if scope["executive_id"]:
        exec_id = scope["executive_id"]
    if scope["franchise_id"]:
        franchise_id = scope["franchise_id"]

    q = _businesses_query(
        db,
        sales_executive_id=exec_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        franchise_id=franchise_id,
    )
    total = q.with_entities(func.count(BusinessConfigModel.key)).order_by(None).scalar() or 0
    pages = ceil(total / per_page) if total else 0
    if pages:
        page = min(page, pages)

    rows = (
        q.options(
            load_only(
                BusinessConfigModel.key,
                BusinessConfigModel.name,
                BusinessConfigModel.id,
                BusinessConfigModel.route_slug,
                BusinessConfigModel.collection,
                BusinessConfigModel.plan_code,
                BusinessConfigModel.plan_amount,
                BusinessConfigModel.join_date,
                BusinessConfigModel.expiry_date,
                BusinessConfigModel.sales_executive_id,
                BusinessConfigModel.mobile,
                BusinessConfigModel.alternate_mobile,
                BusinessConfigModel.email,
                BusinessConfigModel.address,
                BusinessConfigModel.franchise_id,
                BusinessConfigModel.area,
            )
        )
        .order_by(BusinessConfigModel.join_date.desc(), BusinessConfigModel.name.asc())
        .offset((page - 1) * per_page if pages else 0)
        .limit(per_page)
        .all()
    )

    ids = {b.sales_executive_id for b in rows if b.sales_executive_id}
    salesman_names = {}
    if ids:
        salesman_names = dict(
            db.query(SalesExecutive.id, SalesExecutive.name).filter(SalesExecutive.id.in_(ids)).all()
        )

    start = (page - 1) * per_page + 1 if total else 0
    end = min(page * per_page, total)
    cards = [_serialize_business_card(b, salesman_names) for b in rows]
    from services.payment_service import unpaid_business_keys

    due_keys = unpaid_business_keys(db, [c["key"] for c in cards])
    for card in cards:
        card["payment_due"] = card["key"] in due_keys
    return {
        "rows": cards,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "search": search,
        "page_numbers": _page_numbers(page, pages) if pages else [],
        "start": start,
        "end": end,
    }


def seed_initial_businesses(db: Session) -> None:
    if db.query(BusinessConfigModel).count() == 0:
        for key, data in _get_default_businesses().items():
            place_id = extract_place_id(data.get("place_id", ""))
            db.add(
                BusinessConfigModel(
                    key=key,
                    name=data.get("name", ""),
                    id=data.get("id", ""),
                    route_slug=data.get("route_slug", ""),
                    collection=data.get("collection", ""),
                    place_id=place_id,
                    google_review_url=build_google_review_url(place_id),
                    logo_filename=data.get("logo_filename", ""),
                    scope=data.get("scope", ""),
                    examples_1star=data.get("examples_1star", ""),
                    examples_2star=data.get("examples_2star", ""),
                    examples_3star=data.get("examples_3star", ""),
                    examples_4star=data.get("examples_4star", ""),
                    examples_5star=data.get("examples_5star", ""),
                )
            )
        db.commit()
        return

    changed = False
    for b in db.query(BusinessConfigModel).all():
        place_id = extract_place_id(b.place_id or b.google_review_url or "")
        url = build_google_review_url(place_id)
        if (b.place_id or "") != place_id or (b.google_review_url or "") != url:
            b.place_id = place_id
            b.google_review_url = url
            changed = True
    if changed:
        db.commit()


def get_all_businesses(db: Session, sales_executive_id: int = None) -> dict:
    """Read and return businesses from the database, optionally for one salesman."""
    from models.domain_models import SalesExecutive

    salesman_names = {e.id: e.name for e in db.query(SalesExecutive).all()}
    q = db.query(BusinessConfigModel)
    if sales_executive_id:
        q = q.filter(BusinessConfigModel.sales_executive_id == sales_executive_id)
    businesses = q.order_by(BusinessConfigModel.name.asc()).all()
    return {b.key: _serialize_business(b, salesman_names) for b in businesses}


def ensure_feedback_routes(db: Session) -> None:
    """Fill missing route_slug / collection so every saved business has a live page."""
    changed = False
    for b in db.query(BusinessConfigModel).order_by(BusinessConfigModel.key.asc()).all():
        slug = (b.route_slug or "").strip()
        if not slug or slug in RESERVED_SLUGS:
            b.route_slug = allocate_route_slug(db, b.key, b.name or "", b.key, exclude_key=b.key)
            changed = True
        if not (b.collection or "").strip():
            b.collection = allocate_collection(db, "", b.route_slug or "", b.key, exclude_key=b.key)
            changed = True
    if changed:
        db.commit()


def get_businesses_for_user(db: Session, user) -> dict:
    """Admin sees every client; a salesman sees only the businesses assigned to them."""
    from models.domain_models import UserRole
    from services.sales_service import get_executive_for_user

    if not user:
        return {}
    if user.role == UserRole.ADMIN:
        return get_all_businesses(db)
    own = get_executive_for_user(db, user)
    if not own:
        return {}
    return get_all_businesses(db, sales_executive_id=own.id)


def user_owns_business(db: Session, user, business_key: str) -> bool:
    from models.domain_models import UserRole
    from services.franchise_service import get_franchise_for_user
    from services.sales_service import get_executive_for_user

    if not user or not business_key:
        return False
    if user.role == UserRole.ADMIN:
        return True
    business = get_business(db, business_key)
    if not business:
        return False
    if user.role == UserRole.FRANCHISE:
        org = get_franchise_for_user(db, user)
        return bool(org and business.get("franchise_id") == org.id)
    own = get_executive_for_user(db, user)
    if not own:
        return False
    return business.get("sales_executive_id") == own.id


def distribute_businesses_to_sales(db: Session) -> None:
    """Give every salesman a share of clients. Unassigned first; rebalance if some have none."""
    from models.domain_models import SalesExecutive

    execs = (
        db.query(SalesExecutive)
        .filter(SalesExecutive.is_active.is_(True))
        .order_by(SalesExecutive.id.asc())
        .all()
    )
    if not execs:
        return

    businesses = db.query(BusinessConfigModel).order_by(BusinessConfigModel.key.asc()).all()
    if not businesses:
        return

    counts = {e.id: 0 for e in execs}
    for b in businesses:
        if b.sales_executive_id in counts:
            counts[b.sales_executive_id] += 1

    unassigned = [b for b in businesses if not b.sales_executive_id]
    changed = False
    for b in unassigned:
        exec_id = min(counts, key=lambda i: (counts[i], i))
        b.sales_executive_id = exec_id
        counts[exec_id] += 1
        changed = True

    idle = [eid for eid, count in counts.items() if count == 0]
    if idle and len(idle) * 2 >= len(execs):
        for i, b in enumerate(businesses):
            exec_id = execs[i % len(execs)].id
            if b.sales_executive_id != exec_id:
                b.sales_executive_id = exec_id
                changed = True

    if changed:
        db.commit()


def get_business(db: Session, business_key: str) -> dict:
    """Get a specific business configuration by key."""
    from models.domain_models import SalesExecutive

    b = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    if not b:
        return None
    salesman_names = {}
    if getattr(b, "sales_executive_id", None):
        exec_ = db.query(SalesExecutive).filter(SalesExecutive.id == b.sales_executive_id).first()
        if exec_:
            salesman_names[exec_.id] = exec_.name
    data = _serialize_business(b, salesman_names)
    from services.payment_service import unpaid_business_keys

    data["payment_due"] = b.key in unpaid_business_keys(db, [b.key])
    return data


def _slug_taken(db: Session, value: str, exclude_key: str = None) -> bool:
    from sqlalchemy import or_

    if not value:
        return False
    q = db.query(BusinessConfigModel).filter(
        or_(
            BusinessConfigModel.route_slug == value,
            BusinessConfigModel.key == value,
            BusinessConfigModel.collection == value,
        )
    )
    if exclude_key:
        q = q.filter(BusinessConfigModel.key != exclude_key)
    return q.first() is not None


def allocate_route_slug(db: Session, desired: str, name: str, business_key: str, exclude_key: str = None) -> str:
    base = slugify(desired) or slugify(business_key) or slugify(name) or "business"
    if base in RESERVED_SLUGS:
        base = f"{base}_page"
    candidate = base
    n = 2
    while _slug_taken(db, candidate, exclude_key=exclude_key):
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def allocate_collection(db: Session, desired: str, route_slug: str, business_key: str, exclude_key: str = None) -> str:
    base = slugify(desired) or f"{route_slug}_feedback" or f"{slugify(business_key)}_feedback"
    if not base:
        base = "feedback"
    candidate = base
    n = 2
    while True:
        q = db.query(BusinessConfigModel).filter(BusinessConfigModel.collection == candidate)
        if exclude_key:
            q = q.filter(BusinessConfigModel.key != exclude_key)
        if q.first() is None:
            return candidate
        candidate = f"{base}_{n}"
        n += 1


def find_business_by_route(db: Session, slug: str) -> dict:
    """Resolve a public feedback URL slug against route_slug, then key, then collection."""
    slug = (slug or "").strip().strip("/")
    if not slug:
        return None
    for column in (
        BusinessConfigModel.route_slug,
        BusinessConfigModel.key,
        BusinessConfigModel.collection,
    ):
        b = db.query(BusinessConfigModel).filter(column == slug).first()
        if b:
            return get_business(db, b.key)
    return None


def save_business(db: Session, business_key: str, data: dict):
    """Save or update a business and credit 10% plan commission to the salesman wallet."""
    from services.plan_service import credit_plan_to_wallet, resolve_plan

    b = db.query(BusinessConfigModel).filter(BusinessConfigModel.key == business_key).first()
    is_new = b is None
    old_plan = (b.plan_code if b else "") or ""
    old_join = b.join_date if b else None
    if not b:
        b = BusinessConfigModel(key=business_key)
        db.add(b)

    place_id = extract_place_id(data.get("place_id", ""))
    plan = resolve_plan(data.get("plan_code"), data.get("join_date"))
    raw_exec = data.get("sales_executive_id")
    exec_id = None
    if raw_exec not in (None, "", 0, "0"):
        exec_id = int(raw_exec)

    b.name = data.get("name", "")
    b.id = data.get("id", "")
    b.route_slug = allocate_route_slug(
        db,
        data.get("route_slug", ""),
        data.get("name", ""),
        business_key,
        exclude_key=business_key,
    )
    b.collection = allocate_collection(
        db,
        data.get("collection", ""),
        b.route_slug,
        business_key,
        exclude_key=business_key,
    )
    b.place_id = place_id
    b.google_review_url = build_google_review_url(place_id)
    b.logo_filename = data.get("logo_filename", "")
    b.scope = data.get("scope", "")
    b.examples_1star = data.get("examples_1star", "")
    b.examples_2star = data.get("examples_2star", "")
    b.examples_3star = data.get("examples_3star", "")
    b.examples_4star = data.get("examples_4star", "")
    b.examples_5star = data.get("examples_5star", "")
    b.mobile = data.get("mobile", "") or ""
    b.alternate_mobile = data.get("alternate_mobile", "") or ""
    b.email = data.get("email", "") or ""
    b.address = data.get("address", "") or ""
    b.plan_code = plan["plan_code"]
    b.plan_amount = plan["plan_amount"]
    b.join_date = plan["join_date"]
    b.expiry_date = plan["expiry_date"]
    b.sales_executive_id = exec_id
    b.area = (data.get("area") or "").strip()
    raw_fr = data.get("franchise_id")
    if raw_fr not in (None, "", 0, "0"):
        b.franchise_id = int(raw_fr)
    elif exec_id:
        from models.domain_models import SalesExecutive

        assigned = db.query(SalesExecutive).filter(SalesExecutive.id == exec_id).first()
        b.franchise_id = assigned.franchise_id if assigned else None
    else:
        b.franchise_id = None

    db.commit()

    plan_changed = plan["plan_code"] and (
        is_new or plan["plan_code"] != old_plan or plan["join_date"] != old_join
    )
    from services.payment_service import razorpay_configured

    if plan_changed and exec_id and not razorpay_configured():
        credit_plan_to_wallet(
            db,
            exec_id,
            business_key,
            plan["plan_code"],
            plan["plan_amount"],
            plan["join_date"],
        )
    if plan["plan_code"] and exec_id:
        from services.sales_service import upsert_plan_booking

        upsert_plan_booking(db, b, commit=True)
