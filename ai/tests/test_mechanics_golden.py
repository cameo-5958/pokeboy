"""Golden Gen 1 (showdown-mode) mechanics fixtures through the Battle API.

Style: property fixtures over seeded battles. Seed-dependent effects assert
"happens within N seeds" / "never happens within N seeds" instead of pinning
exact RNG streams, so they survive engine upgrades that reshuffle rolls while
still catching wrapper/layout bugs and mechanics regressions.
"""

from sim.battle import Battle
from sim.pack import PokemonSpec

MAX_SEEDS = 120


def duel(p1: PokemonSpec, p2: PokemonSpec, seed: int) -> Battle:
    return Battle([p1], [p2], seed=seed)


def spam(b: Battle, turns: int) -> None:
    """Both sides use their first legal action (move slot 0 when possible)."""
    for _ in range(turns):
        if b.winner:
            return
        acts = []
        for p in (1, 2):
            la = b.state(p).legal_actions
            acts.append(0 if 0 in la else la[0])
        b.step(acts[0], acts[1])


def active_status(b: Battle, player: int) -> str | None:
    s = b.state(player)
    return s.my_side["pokemon"][s.my_side["active_ix"]]["status"]


def active_hp(b: Battle, player: int) -> int:
    s = b.state(player)
    return s.my_side["pokemon"][s.my_side["active_ix"]]["hp"]


def exists_seed(predicate) -> bool:
    return any(predicate(seed) for seed in range(MAX_SEEDS))


# --- type chart / immunities -------------------------------------------------


def test_normal_cannot_hit_ghost():
    tauros = PokemonSpec("Tauros", ["Body Slam"])
    gengar = PokemonSpec("Gengar", ["Night Shade"])
    for seed in range(20):
        b = duel(tauros, gengar, seed)
        start = active_hp(b, 2)
        spam(b, 15)
        if b.winner == "p2" or b.winner is None:
            assert active_hp(b, 2) == start, f"seed {seed}: ghost took normal damage"


def test_electric_cannot_hit_ground():
    zapdos = PokemonSpec("Zapdos", ["Thunderbolt"])
    rhydon = PokemonSpec("Rhydon", ["Rock Slide"])
    for seed in range(20):
        b = duel(zapdos, rhydon, seed)
        start = active_hp(b, 2)
        spam(b, 10)
        if not b.winner or b.winner == "p2":
            assert active_hp(b, 2) == start, f"seed {seed}: ground took electric damage"


# --- fixed / exact damage ----------------------------------------------------


def test_seismic_toss_deals_exactly_level():
    chansey = PokemonSpec("Chansey", ["Seismic Toss"])
    snorlax = PokemonSpec("Snorlax", ["Reflect"])  # non-healer: damage stays visible

    def hits_exactly_100(seed: int) -> bool:
        b = duel(chansey, snorlax, seed)
        start = active_hp(b, 2)
        b.step(0, 0)
        dmg = start - active_hp(b, 2)
        return dmg == 100

    assert exists_seed(hits_exactly_100)
    # and it never deals any OTHER nonzero amount (only 0 on the 1/256 miss)
    for seed in range(40):
        b = duel(chansey, snorlax, seed)
        start = active_hp(b, 2)
        b.step(0, 0)
        assert start - active_hp(b, 2) in (0, 100)


# --- status ------------------------------------------------------------------


def test_thunder_wave_paralyzes():
    chansey = PokemonSpec("Chansey", ["Thunder Wave"])
    tauros = PokemonSpec("Tauros", ["Body Slam"])

    def paralyzed(seed: int) -> bool:
        b = duel(chansey, tauros, seed)
        b.step(0, 0)
        return active_status(b, 2) == "PAR"

    assert exists_seed(paralyzed)


def test_body_slam_never_paralyzes_normal_types():
    tauros = PokemonSpec("Tauros", ["Body Slam"])
    snorlax = PokemonSpec("Snorlax", ["Rest"])
    for seed in range(MAX_SEEDS):
        b = duel(tauros, snorlax, seed)
        spam(b, 8)
        assert active_status(b, 2) != "PAR", f"seed {seed}: normal-type paralyzed by Body Slam"


def test_body_slam_can_paralyze_non_normal_types():
    tauros = PokemonSpec("Tauros", ["Body Slam"])
    eggs = PokemonSpec("Exeggutor", ["Stun Spore"])  # grass/psychic punching bag

    def paralyzed(seed: int) -> bool:
        b = duel(tauros, eggs, seed)
        spam(b, 6)
        return active_status(b, 2) == "PAR"

    assert exists_seed(paralyzed)


def test_sleep_inflicted_and_sticks():
    jynx = PokemonSpec("Jynx", ["Lovely Kiss"])
    snorlax = PokemonSpec("Snorlax", ["Body Slam"])

    def slept(seed: int) -> bool:
        b = duel(jynx, snorlax, seed)
        b.step(0, 0)
        return active_status(b, 2) == "SLP"

    assert exists_seed(slept)


def test_freeze_never_thaws_naturally():
    jynx = PokemonSpec("Jynx", ["Blizzard"])
    chansey = PokemonSpec("Chansey", ["Soft-Boiled"])  # no fire moves, heals stall
    frozen_seed = None
    for seed in range(MAX_SEEDS):
        b = duel(jynx, chansey, seed)
        spam(b, 4)
        if active_status(b, 2) == "FRZ":
            frozen_seed = seed
            break
    assert frozen_seed is not None, "no freeze within seed budget"
    b = duel(jynx, chansey, frozen_seed)
    spam(b, 4)
    assert active_status(b, 2) == "FRZ"
    # jynx now stalls with Blizzard PP; chansey stays frozen forever
    for _ in range(20):
        if b.winner:
            break
        spam(b, 1)
        if not b.winner:
            assert active_status(b, 2) == "FRZ"


# --- move mechanics ----------------------------------------------------------


def test_recover_heals():
    alakazam = PokemonSpec("Alakazam", ["Recover"])
    tauros = PokemonSpec("Tauros", ["Body Slam"])

    def healed(seed: int) -> bool:
        b = duel(alakazam, tauros, seed)
        b.step(0, 0)  # take a body slam, recover is slower? either order fine
        hp_before = active_hp(b, 1)
        s = b.state(1)
        if b.winner or 0 not in s.legal_actions or hp_before == s.my_side["pokemon"][0]["max_hp"]:
            return False
        b.step(0, 0)
        return active_hp(b, 1) > hp_before

    assert exists_seed(healed)


def test_explosion_faints_user():
    gengar = PokemonSpec("Gengar", ["Explosion"])
    snorlax = PokemonSpec("Snorlax", ["Rest"])
    b = duel(gengar, snorlax, seed=1)
    b.step(0, 0)
    assert b.winner == "p2"  # user fainted, only pokemon on side 1


def _hb_pp(b: Battle) -> int:
    return b.state(1).my_side["pokemon"][0]["moves"][0]["pp"]


def test_hyper_beam_recharge_when_no_ko():
    # libpkmn (showdown mode) presents the recharge turn as a forced
    # move-slot-1 choice; its signature is: PP unchanged, no damage dealt.
    tauros = PokemonSpec("Tauros", ["Hyper Beam"])
    snorlax = PokemonSpec("Snorlax", ["Reflect"])  # bulky enough to survive

    def recharged(seed: int) -> bool:
        b = duel(tauros, snorlax, seed)
        start = active_hp(b, 2)
        b.step(0, 0)
        if b.winner or active_hp(b, 2) == start:  # missed: no recharge expected
            return False
        pp = _hb_pp(b)
        hp = active_hp(b, 2)
        spam(b, 1)
        return _hb_pp(b) == pp and active_hp(b, 2) == hp

    assert exists_seed(recharged)


def test_hyper_beam_recharge_skipped_on_ko():
    tauros = PokemonSpec("Tauros", ["Hyper Beam"])
    jynx = PokemonSpec("Jynx", ["Splash"])  # frail: OHKO fodder
    chansey = PokemonSpec("Chansey", ["Soft-Boiled"])

    def skipped(seed: int) -> bool:
        b = Battle([tauros], [jynx, chansey], seed=seed)
        b.step(0, 0)
        if b.state(2).request_kind != "force_switch":  # no OHKO this seed
            return False
        b.step(9, b.state(2).legal_actions[0])  # p1 passes, p2 replaces
        if b.winner:
            return False
        pp = _hb_pp(b)
        hp = active_hp(b, 2)
        spam(b, 1)  # if recharge were (wrongly) required, pp and hp would hold
        return _hb_pp(b) == pp - 1 and active_hp(b, 2) < hp

    assert exists_seed(skipped)


def test_substitute_costs_quarter_hp():
    rhydon = PokemonSpec("Rhydon", ["Substitute"])
    chansey = PokemonSpec("Chansey", ["Soft-Boiled"])
    b = duel(rhydon, chansey, seed=3)
    max_hp = b.state(1).my_side["pokemon"][0]["max_hp"]
    b.step(0, 0)
    assert active_hp(b, 1) == max_hp - max_hp // 4
