"""
entities.py — synthetic-but-realistic entity pools for the Alma corpus generator.

Hard rule: NO real PHI.
  * Provider / client / agent names are invented from generic name pools.
  * Clients are referenced by first name + last initial (or bare initials).
  * Member IDs and claim numbers are *format-shaped* fakes (right shape, fake digits).
  * Payer names ARE real — they are public companies, not protected health info.

Everything is driven by a caller-supplied seeded ``random.Random`` so the whole
corpus is reproducible from a single seed.
"""
from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------------
# Real payers (behavioral-health relevant). weight = relative share of tickets
# that name a commercial payer. Aetna is intentionally heavy because the
# 2026-05-20 rate-change incident centres on it.
# fmt: (display_name, key, weight, member_id_style)
# --------------------------------------------------------------------------
COMMERCIAL_PAYERS = [
    ("Aetna", "aetna", 22, "alpha1+9"),
    ("Cigna", "cigna", 11, "U+8"),
    ("Evernorth Behavioral Health", "evernorth", 4, "U+8"),
    ("UnitedHealthcare", "uhc", 13, "9digits"),
    ("Optum Behavioral Health", "optum", 6, "9digits"),
    ("Anthem Blue Cross Blue Shield", "anthem", 9, "bcbs"),
    ("Blue Cross Blue Shield of Massachusetts", "bcbsma", 4, "bcbs"),
    ("Empire BlueCross BlueShield", "empire", 3, "bcbs"),
    ("Horizon Blue Cross Blue Shield of NJ", "horizon", 3, "bcbs"),
    ("Oscar Health", "oscar", 3, "9digits"),
    ("Oxford Health Plans", "oxford", 3, "9digits"),
    ("UMR", "umr", 3, "9digits"),
    ("Carelon Behavioral Health", "carelon", 4, "alpha1+9"),
]

# EAP payers — used for the EAP / employee-assistance TRC family.
EAP_PAYERS = [
    ("Spring Health", "spring", 8, "eap"),
    ("Lyra Health", "lyra", 7, "eap"),
    ("Modern Health", "modern", 3, "eap"),
    ("Carelon EAP", "carelon_eap", 3, "eap"),
    ("Aetna Resources For Living", "aetna_rfl", 3, "eap"),
    ("ComPsych", "compsych", 2, "eap"),
    ("Optum EAP", "optum_eap", 2, "eap"),
]

_PAYER_STYLE = {key: style for _, key, _, style in COMMERCIAL_PAYERS + EAP_PAYERS}
_PAYER_NAME = {key: name for name, key, _, style in COMMERCIAL_PAYERS + EAP_PAYERS}

# CPT codes therapists actually bill (used in claims / credentialing / rate threads).
CPT_CODES = {
    "90791": "diagnostic intake",
    "90832": "30-min psychotherapy",
    "90834": "45-min psychotherapy",
    "90837": "60-min psychotherapy",
    "90846": "family therapy w/o patient",
    "90847": "family therapy w/ patient",
    "90853": "group psychotherapy",
    "99214": "E/M established, moderate",
    "99213": "E/M established, low",
    "90785": "interactive complexity add-on",
}

# States where Alma operates (real). Weighted toward Alma's heaviest markets.
STATES = (
    ["NY"] * 8 + ["CA"] * 7 + ["NJ"] * 5 + ["MA"] * 4 + ["CT"] * 4 + ["IL"] * 4
    + ["TX"] * 4 + ["FL"] * 4 + ["PA"] * 3 + ["CO"] * 3 + ["WA"] * 3 + ["GA"] * 2
    + ["NC"] * 2 + ["VA"] * 2 + ["MD"] * 2 + ["DC"] * 1 + ["OR"] * 2 + ["AZ"] * 2
    + ["MI"] * 2 + ["OH"] * 2 + ["MN"] * 1 + ["TN"] * 1
)

CREDENTIALS = (
    ["LCSW"] * 6 + ["LMFT"] * 5 + ["LPC"] * 4 + ["LMHC"] * 4 + ["LCPC"] * 3
    + ["PsyD"] * 3 + ["PhD"] * 2 + ["LP"] * 1 + ["LICSW"] * 2 + ["LPCC"] * 2
    + ["LCMHC"] * 1 + ["LMSW"] * 1
)

# Generic name pools (NOT real individuals — combinatorial fakes).
FIRST_NAMES = [
    "Sarah", "Michael", "Jessica", "David", "Emily", "Daniel", "Rachel", "Jason",
    "Ashley", "Christopher", "Amanda", "Matthew", "Lauren", "Andrew", "Megan",
    "Joshua", "Stephanie", "Brandon", "Nicole", "Ryan", "Hannah", "Kevin",
    "Samantha", "Justin", "Brittany", "Aaron", "Kayla", "Eric", "Victoria",
    "Adam", "Maria", "Carlos", "Priya", "Wei", "Aisha", "Jamal", "Sofia",
    "Diego", "Yuki", "Omar", "Nina", "Andre", "Leah", "Marcus", "Elena",
    "Tyler", "Gabriela", "Devin", "Naomi", "Ravi",
]
LAST_NAMES = [
    "Chen", "Patel", "Nguyen", "Garcia", "Johnson", "Williams", "Brown", "Jones",
    "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
    "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis",
    "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres",
    "Hill", "Green", "Adams", "Baker", "Nelson", "Carter", "Mitchell", "Roberts",
    "Kim", "Cohen", "Okafor",
]
AGENT_FIRST = [
    "Taylor", "Jordan", "Morgan", "Casey", "Riley", "Alex", "Sam", "Jamie",
    "Drew", "Quinn", "Avery", "Reese", "Devon", "Cameron", "Skyler", "Parker",
    "Harper", "Rowan", "Emerson", "Logan",
]


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(n))


def member_id(rng: random.Random, payer_key: str) -> str:
    """Format-shaped fake member ID matching the payer's real ID style."""
    style = _PAYER_STYLE.get(payer_key, "9digits")
    if style == "alpha1+9":          # Aetna "W" + 9, Carelon
        return rng.choice("WXKQ") + _digits(rng, 9)
    if style == "U+8":               # Cigna / Evernorth
        return "U" + _digits(rng, 8)
    if style == "bcbs":              # 3-letter alpha prefix + 9 digits
        pre = "".join(rng.choice(string.ascii_uppercase) for _ in range(3))
        return pre + _digits(rng, 9)
    if style == "eap":               # EAP authorization-style code
        return "EAP-" + _digits(rng, 6)
    return _digits(rng, 9)           # UHC / Optum / Oscar / Oxford / UMR


def claim_no(rng: random.Random) -> str:
    return "CLM" + _digits(rng, 9)


def auth_no(rng: random.Random) -> str:
    return "AUTH-" + _digits(rng, 7)


def money(rng: random.Random, lo: int, hi: int, cents: bool = True) -> str:
    base = rng.randint(lo, hi)
    if cents and rng.random() < 0.7:
        base = base + rng.choice([0, 0.50, 0.25, 0.75, 0.13, 0.42, 0.88, 0.07])
        return f"{base:.2f}"
    return f"{base:.2f}"


# --------------------------------------------------------------------------
# Per-ticket entity context
# --------------------------------------------------------------------------
@dataclass
class Ctx:
    """Everything an archetype builder needs to realise one ticket."""
    rng: random.Random
    ticket_id: int
    trc: str
    family: str
    leaf: str
    topic: str
    payer_name: str
    payer_key: str
    provider_name: str       # "Dr. Sarah Chen, LCSW"
    provider_first: str
    provider_id: str
    client_name: str         # "Jordan M." or "" if no specific client
    client_initials: str     # "J.M."
    client_id: str           # "" if no specific client
    agent_name: str          # "Taylor"
    agent_id: str
    state: str
    member_id: str
    week_start: date
    created: datetime
    is_needle: bool = False
    needle_label: str = ""
    # scratch space archetypes can stash computed amounts/codes for tags
    extra: dict = field(default_factory=dict)


def _pick_weighted(rng: random.Random, items):
    """items: list of (value, weight). Returns a value."""
    total = sum(w for _, w in items)
    r = rng.uniform(0, total)
    upto = 0.0
    for val, w in items:
        upto += w
        if r <= upto:
            return val
    return items[-1][0]


class EntityFactory:
    """Samples reusable + per-ticket entities from a seeded RNG.

    Providers/clients/agents are drawn from bounded pools so the same provider
    recurs across tickets (realistic — a heavy Aetna user files several).
    """

    def __init__(self, rng: random.Random, n_providers=480, n_clients=2600, n_agents=55):
        self.rng = rng
        self.providers = [self._make_provider(i) for i in range(n_providers)]
        self.clients = [self._make_client(i) for i in range(n_clients)]
        self.agents = [self._make_agent(i) for i in range(n_agents)]

    # -- pools -------------------------------------------------------------
    def _make_provider(self, i):
        f = self.rng.choice(FIRST_NAMES)
        l = self.rng.choice(LAST_NAMES)
        cred = self.rng.choice(CREDENTIALS)
        honor = "Dr. " if cred in ("PsyD", "PhD") else ""
        return {
            "id": f"p-{100000 + i}",
            "first": f,
            "name": f"{honor}{f} {l}, {cred}",
            "cred": cred,
            "state": self.rng.choice(STATES),
        }

    def _make_client(self, i):
        f = self.rng.choice(FIRST_NAMES)
        li = self.rng.choice(LAST_NAMES)[0]
        return {
            "id": f"c-{500000 + i}",
            "name": f"{f} {li}.",
            "initials": f"{f[0]}.{li}.",
        }

    def _make_agent(self, i):
        return {"id": f"a-{9000 + i}", "name": self.rng.choice(AGENT_FIRST)}

    # -- per-ticket --------------------------------------------------------
    def payer_for(self, family: str):
        """Choose a real payer appropriate to the TRC family."""
        if family == "eap":
            name = _pick_weighted(self.rng, [(k, w) for _, k, w, _ in EAP_PAYERS])
            return _PAYER_NAME[name], name
        if family == "aetna_incident":
            return "Aetna", "aetna"
        name = _pick_weighted(self.rng, [(k, w) for _, k, w, _ in COMMERCIAL_PAYERS])
        return _PAYER_NAME[name], name

    def build_ctx(self, ticket_id, trc, family, leaf, topic, week_start, created, needs_client=True):
        prov = self.rng.choice(self.providers)
        agent = self.rng.choice(self.agents)
        payer_name, payer_key = self.payer_for(family)
        if needs_client:
            cl = self.rng.choice(self.clients)
            cname, cinit, cid = cl["name"], cl["initials"], cl["id"]
        else:
            cname, cinit, cid = "", "", ""
        return Ctx(
            rng=self.rng,
            ticket_id=ticket_id,
            trc=trc,
            family=family,
            leaf=leaf,
            topic=topic,
            payer_name=payer_name,
            payer_key=payer_key,
            provider_name=prov["name"],
            provider_first=prov["first"],
            provider_id=prov["id"],
            client_name=cname,
            client_initials=cinit,
            client_id=cid,
            agent_name=agent["name"],
            agent_id=agent["id"],
            state=prov["state"],
            member_id=member_id(self.rng, payer_key),
            week_start=week_start,
            created=created,
        )
