"""Country CIQ registry for the 2026 cruising grounds (FL / Bahamas / Caribbean).

The prose, fee and office fields were checked against the ``official_urls`` on
the date in each entry's ``verified`` field. Fees, pre-arrival apps and ports
still change between seasons — the briefing shows the verified date and the
primary URLs so a pre-passage re-check is quick. Dollar amounts are USD.
"""

from __future__ import annotations

from .model import CountryCIQ

_NOONSITE = "https://www.noonsite.com/place/{}/"
_VERIFIED = "2026-09-09"

REGISTRY: dict[str, CountryCIQ] = {
    "usa": CountryCIQ(
        key="usa",
        name="United States",
        ports_of_entry="any CBP port of entry; small-vessel reporting at CBP-designated "
                       "locations (list at cbp.gov/travel/pleasure-boats)",
        pre_arrival="report on/just before arrival via the free CBP ROAM app (iOS/Android) "
                    "or by phone to CBP, who direct you to the nearest port of entry. SVRS "
                    "is retired and float plans are no longer accepted.",
        on_arrival="only the master goes ashore to report; all passengers and crew stay "
                   "aboard until CBP grants release. A foreign-flag vessel may hold a CBP "
                   "Cruising Licence that waives repeated formal entry.",
        documents="passports, USCG Certificate of Documentation or state registration, last "
                  "foreign port clearance, CBP annual user-fee decal number",
        official_urls=(
            "https://www.cbp.gov/travel/pleasure-boats-private-flyers/pleasure-boat-overview",
            "https://www.cbp.gov/travel/pleasure-boats",
            "https://dtops.cbp.dhs.gov/",
        ),
        fees="CBP annual user-fee decal (Form 339V), required for vessels 30 ft and over: "
             "$34.83 per calendar year, expires 31 Dec. Buy at dtops.cbp.dhs.gov or mail "
             "CBP Attn: DTOPS Program Administrator, 8899 East 56th St, Indianapolis IN "
             "46249. No separate arrival fee for a decal-holding vessel. Overtime fees may "
             "apply for after-hours in-person clearance.",
        offices="Report through ROAM from anywhere; CBP pleasure-boat reporting locations "
                "and phone numbers are listed by state at cbp.gov/travel/pleasure-boats. "
                "CBP INFO Center: 1-877-227-5511.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("usa-east-coast"),),
        notes="A US citizen on a US-flag vessel still must report every arrival from foreign "
              "waters — the Bahamas and Turks & Caicos both count.",
    ),
    "bahamas": CountryCIQ(
        key="bahamas",
        name="The Bahamas",
        ports_of_entry="Bimini, West End (Old Bahama Bay), Grand Cay, Chub Cay, Nassau, "
                       "Freeport, Marsh Harbour, Spanish Wells, Great Harbour Cay, "
                       "Georgetown (Exuma), and others — full list at bahamascustoms.gov.bs",
        pre_arrival="Bahamas Click2Clear: use the yellow 'Request Cruising Permit' button — "
                    "no username or password, and no registration for a permit of 12 months "
                    "or less. Create the Inbound Declaration and pay online; a Customs "
                    "officer then approves it in person at the port of entry. (Only the "
                    "2-year Frequent Digital Cruising Card needs a registered account.)",
        on_arrival="Q flag; only the captain lands to finish clearance at the port; crew "
                   "stay aboard until cleared. Immigration cards per person. Firearms must "
                   "be declared and are normally bonded.",
        documents="passports, boat registration, last port clearance, crew/passenger list, "
                  "Click2Clear declaration reference, proof of the paid permit",
        official_urls=(
            "https://www.bahamascustoms.gov.bs/visitor-info/"
            "marine-vessel-declarations-cruising-permits/",
            "https://click2clear.bahamas.gov.bs/",
        ),
        fees="Cruising permit by length (a your vessel is the 'exceeding 50-100 ft' tier): "
             "30-day $350 (no return visit), 6-month $750 (one free return visit), 12-month "
             "$1,000 (two free return visits). The permit fee covers Customs + Immigration "
             "attendance and transport. Anchorage fee — payable once per permit, ONLY if "
             "the vessel is not staying at a marina: $100 (30-day) / $350 (6- or 12-month) "
             "for 50-100 ft. Fishing permit $300 (over 50 ft), valid 30 days only. "
             "Passenger tax $20/person (historically the first 3 are exempt — confirm at "
             "payment). Outbound clearance (Form C28A) $75.",
        offices="Bahamas Customs (Nassau HQ): (242) 604-3124 / 604-3125, fax (242) "
                "322-6223. Clearance is completed with the officer at whichever port of "
                "entry you arrive at.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("bahamas"),),
        notes="Fee structure was overhauled in 2024-25; the figures above are the current "
              "official schedule. West End or Bimini is the usual first stop from Florida.",
    ),
    "turks and caicos": CountryCIQ(
        key="turks and caicos",
        name="Turks & Caicos Islands",
        ports_of_entry="Providenciales (Sapodilla Bay, South Dock, Blue Haven Marina, "
                       "South Bank Marina, Southside Marina), Grand Turk (South Dock), "
                       "South Caicos (Cockburn Harbour)",
        pre_arrival="SailClear (sailclear.com) — the master must give notice of arrival at "
                    "least 24 hours ahead, with vessel and passenger particulars and a "
                    "declaration of any animals, firearms or dutiable goods.",
        on_arrival="Q flag; proceed directly to a port of entry and stay aboard until full "
                   "clearance is granted. Customs then Immigration, both at the port. The "
                   "cruising permit is issued by the Proper Officer on clearance.",
        documents="passports valid 6 months, ship's registration, last (outbound) clearance "
                  "/ Zarpe, crew list, proof of insurance",
        official_urls=(
            "https://borderforce.gov.tc/vessel-clearance",
            "https://customs.gov.tc/pgs?pg=vesselclearance",
        ),
        fees="Customs inbound clearance $50 and outbound clearance $50, plus an overtime "
             "fee for clearance outside working hours. Cruising permit $300, valid 90 days "
             "— renew if staying beyond three months. Spearfishing / fishing needs a "
             "separate licence; national-park mooring fees apply at the Princess Alexandra "
             "park off Provo (DECR / gov.tc).",
        offices="Border Force / Customs handle vessel clearance at the port of entry. "
                "Provo Customs is at the airport / South Dock; contacts via "
                "borderforce.gov.tc and customs.gov.tc.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("turks-and-caicos-islands"),),
        notes="Provo (Sapodilla Bay) is the usual first stop from the Bahamas. Clearance "
              "moved to Border Force with a SailClear pre-arrival requirement — the old "
              "'just hail on 16' guidance is out of date.",
    ),
    "british virgin islands": CountryCIQ(
        key="british virgin islands",
        name="British Virgin Islands",
        ports_of_entry="Road Town / Road Harbour (ferry dock, Tortola), Sopers Hole / "
                       "West End (Tortola), Tortola airport, Virgin Gorda (St Thomas Bay), "
                       "Gun Creek (Virgin Gorda), Great Harbour (Jost Van Dyke)",
        pre_arrival="SailClear pre-registration, then clear in person at a customs post. "
                    "Check the current entry/health portal as well.",
        on_arrival="Q flag; the captain clears Customs then Immigration at the same post. "
                   "The cruising permit is charged per person per day.",
        documents="passports, registration, crew list, last port clearance, SailClear "
                  "reference",
        official_urls=("https://gov.vg/", "https://www.sailclear.com/"),
        fees="⚠️ Cruising-permit fees rose on 1 August — re-check gov.vg. As announced: "
             "charter cruising permit $6/person/day (BVI-based) or $16/person/day "
             "(foreign-based); the private-recreational rate also changed — verify. Plus a "
             "$10/person environmental/tourism levy at the port of entry, roughly "
             "$10-25/person initial clearance depending on port and day-vs-overnight, and "
             "the BVI National Parks Trust permit at $4/person/day.",
        offices="HM Customs (284) 468-6800, bvicustoms@gov.vg. Customs and Immigration are "
                "co-located at each port of entry listed above.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("british-virgin-islands"),),
        notes="Fees jumped in August 2026 and the exact private-vessel schedule needs "
              "confirming at gov.vg before a trip. National Parks Trust permit is separate "
              "from the cruising permit.",
    ),
    "usvi": CountryCIQ(
        key="usvi",
        name="US Virgin Islands",
        ports_of_entry="Cruz Bay (St John), Charlotte Amalie & Red Hook (St Thomas), "
                       "Christiansted & Frederiksted (St Croix)",
        pre_arrival="CBP ROAM app, or phone the local CBP office. Arriving from the BVI is "
                    "a foreign arrival even though it is a short hop.",
        on_arrival="as for the mainland US — the master reports, everyone else stays aboard "
                   "until CBP grants release.",
        documents="passports, USCG documentation or state registration, last foreign "
                  "clearance, CBP annual user-fee decal number",
        official_urls=(
            "https://www.cbp.gov/travel/pleasure-boats",
            "https://dtops.cbp.dhs.gov/",
        ),
        fees="Same as the mainland US: CBP annual user-fee decal $34.83/calendar year for "
             "vessels 30 ft and over (dtops.cbp.dhs.gov). Overtime fees for after-hours "
             "in-person clearance.",
        offices="CBP Cruz Bay and Red Hook are the usual first stops from the BVI; "
                "St Thomas CBP: (340) 774-2510. CBP INFO Center 1-877-227-5511.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("us-virgin-islands"),),
        notes="No cruising permit or per-person fee — it is US territory. The decal is the "
              "only recurring charge.",
    ),
    "dominican republic": CountryCIQ(
        key="dominican republic",
        name="Dominican Republic",
        ports_of_entry="Luperón, Puerto Bahía & Bannister (Samaná), Punta Cana, Casa de "
                       "Campo (La Romana), Boca Chica, Barahona",
        pre_arrival="call the port / marina on VHF 16 before arrival. The Navy (Armada de "
                    "República Dominicana) boards and inspects at the first port — expect "
                    "an on-board visit.",
        on_arrival="Q flag; stay aboard. The Navy, Customs (Aduanas), Immigration "
                   "(Migración) and the Port Authority (APORDOM) each clear you and most "
                   "levy a fee. A despacho (coastwise sailing permit) is required to move "
                   "between DR ports and is strictly enforced.",
        documents="passports, registration, last port clearance, several copies of the "
                  "crew list, USD cash in small bills, pet papers if applicable",
        official_urls=(
            "https://www.godominicanrepublic.com/travel/air-and-sea-travel",
            "https://aduanas.gob.do/",
            "https://migracion.gob.do/",
        ),
        fees="No consolidated official fee schedule is published; charges are per-agency "
             "and vary by port. Recent cruiser reports: roughly $110-150 total to check in "
             "(Luperón runs higher than other ports). The Armada issues despachos free of "
             "charge, though a tip is customary. Budget generously in small USD bills. The "
             "Navy typically grants 60 days' cruising on arrival.",
        offices="Handled by the boarding officials at the port of entry. Luperón: through "
                "the Comandancia (Navy post) and the marina office.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("dominican-republic"),),
        notes="The DR is the 'thorny path' waypoint between the Bahamas/TCI and Puerto "
              "Rico. Fees are opaque and negotiated in cash; the despacho between ports is "
              "not optional.",
    ),
    "puerto rico": CountryCIQ(
        key="puerto rico",
        name="Puerto Rico (US)",
        ports_of_entry="any US CBP location — Fajardo / Puerto del Rey, San Juan, Ponce, "
                       "Mayagüez, Culebra, Vieques",
        pre_arrival="CBP ROAM app — this is US territory, so the Mona Passage crossing from "
                    "the DR is a foreign arrival.",
        on_arrival="the master reports via ROAM or by phone; everyone else stays aboard "
                   "until CBP authorises.",
        documents="passports, USCG documentation or state registration, last foreign (DR) "
                  "clearance, CBP annual user-fee decal number",
        official_urls=(
            "https://www.cbp.gov/travel/pleasure-boats",
            "https://dtops.cbp.dhs.gov/",
        ),
        fees="Same as the mainland US: CBP annual user-fee decal $34.83/calendar year for "
             "vessels 30 ft and over. No cruising permit.",
        offices="CBP Fajardo and San Juan handle most yacht arrivals; CBP San Juan: "
                "(787) 253-4533. CBP INFO Center 1-877-227-5511.",
        verified=_VERIFIED,
        secondary_urls=(_NOONSITE.format("puerto-rico"),),
        notes="Fajardo / Puerto del Rey is the standard first stop from the DR or the "
              "Spanish Virgins (Culebra / Vieques).",
    ),
}

ALIASES: dict[str, str] = {
    "bvi": "british virgin islands",
    "tci": "turks and caicos",
    "turks & caicos": "turks and caicos",
    "turks and caicos islands": "turks and caicos",
    "united states": "usa",
    "us": "usa",
    "u.s.": "usa",
    "u.s. virgin islands": "usvi",
    "us virgin islands": "usvi",
    "usvi": "usvi",
    "the bahamas": "bahamas",
    "dr": "dominican republic",
    "pr": "puerto rico",
}


def resolve(name: str) -> CountryCIQ | None:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    return REGISTRY.get(key)
