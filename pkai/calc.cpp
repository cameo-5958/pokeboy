#include "calc.h"
#include <algorithm>
namespace pkai::calc {
using tables::Address;

static uint8_t tb(const Memory& m, Address a, unsigned i) { return m.table(a, i); }

MoveData move_data(const Memory& m, uint8_t id) {
    MoveData d{};
    if (id == 0 || id > 165) return d;
    const unsigned row = (id - 1u) * 6u;
    d.id = id; d.effect = tb(m, tables::Moves, row + 1); d.power = tb(m, tables::Moves, row + 2);
    d.type = tb(m, tables::Moves, row + 3); d.accuracy = tb(m, tables::Moves, row + 4); d.pp = tb(m, tables::Moves, row + 5);
    return d;
}

BaseStats base_stats(const Memory& m, uint8_t species) {
    BaseStats b{};
    if (species == 0 || species > 190) return b;
    Address a = tables::BaseStats; unsigned row = 0;
    if (species == MEW) a = tables::MewBaseStats;
    else {
        const uint8_t dex = tb(m, tables::PokedexOrder, species - 1u);
        if (dex == 0 || dex > 150) return b;   // MissingNo. and glitch slots
        row = (dex - 1u) * 28u;
    }
    b.hp = tb(m, a, row + 1); b.atk = tb(m, a, row + 2); b.def = tb(m, a, row + 3);
    b.spd = tb(m, a, row + 4); b.spc = tb(m, a, row + 5); b.type1 = tb(m, a, row + 6); b.type2 = tb(m, a, row + 7);
    b.valid = true;
    return b;
}

uint8_t type_pair(const Memory& m, uint8_t attack, uint8_t defend) {
    for (unsigned i = 0; i < 3 * 128; i += 3) {
        const uint8_t a = tb(m, tables::TypeEffects, i);
        if (a == 0xff) break;
        if (a == attack && tb(m, tables::TypeEffects, i + 1) == defend) return tb(m, tables::TypeEffects, i + 2);
    }
    return 10;
}

uint16_t type_effect(const Memory& m, uint8_t move_type, uint8_t t1, uint8_t t2) {
    unsigned r = 10;
    for (unsigned i = 0; i < 3 * 128; i += 3) {
        const uint8_t a = tb(m, tables::TypeEffects, i);
        if (a == 0xff) break;
        const uint8_t d = tb(m, tables::TypeEffects, i + 1);
        if (a == move_type && (d == t1 || d == t2)) r = r * tb(m, tables::TypeEffects, i + 2) / 10;
    }
    return uint16_t(r);
}

void stage_ratio(const Memory& m, uint8_t stage, uint8_t& num, uint8_t& den) {
    stage = std::clamp<uint8_t>(stage, 1, 13);
    num = tb(m, tables::StatModifierRatios, (stage - 1u) * 2u);
    den = tb(m, tables::StatModifierRatios, (stage - 1u) * 2u + 1u);
    if (!den) { num = den = 1; }
}

uint16_t apply_stage(const Memory& m, uint16_t stat, uint8_t stage) {
    uint8_t n, d; stage_ratio(m, stage, n, d);
    uint32_t v = uint32_t(stat) * n / d;
    return uint16_t(std::clamp<uint32_t>(v, 1, 999));
}

uint16_t calc_stat(uint8_t base, uint8_t dv, uint16_t stat_exp, uint8_t level, bool hp) {
    unsigned root = 0;
    while (root < 255 && root * root < stat_exp) ++root;      // ceil(sqrt(stat_exp))
    const unsigned bonus = root / 4;
    unsigned v = ((2u * (base + dv) + bonus) * level) / 100u;
    return uint16_t(v + (hp ? level + 10u : 5u));
}

uint16_t player_statexp_prior(uint8_t level) {
    // Tunable public-info prior: a party member raised normally has roughly
    // level^2 * 4 stat exp per stat by the time it reaches `level`.
    const uint32_t v = uint32_t(level) * level * 4u;
    return uint16_t(std::min<uint32_t>(v, 65535));
}

static bool high_crit(const Memory& m, uint8_t move) {
    for (unsigned i = 0; i < 16; ++i) {
        const uint8_t v = tb(m, tables::HighCriticalMoves, i);
        if (v == 0xff) return false;
        if (v == move) return true;
    }
    return false;
}

uint16_t damage_roll(const Memory& m, const Combatant& atk, const Combatant& def, const MoveData& mv, bool crit, uint8_t r) {
    const bool multi = mv.effect == TWO_TO_FIVE || mv.effect == EFFECT_1E;
    if (mv.power == 0 && !multi) return 0;
    const bool physical = mv.type < SPECIAL_TYPE;
    uint16_t A, D;
    if (physical) {
        D = def.defense; if (def.reflect) D = uint16_t(D * 2);
        A = atk.attack;
        if (crit) { D = def.unmod_defense; A = atk.unmod_attack; }
    } else {
        D = def.special; if (def.light_screen) D = uint16_t(D * 2);
        A = atk.special;
        if (crit) { D = def.unmod_special; A = atk.unmod_special; }
    }
    if ((A >> 8) || (D >> 8)) { D >>= 2; A >>= 2; if (A == 0) A = 1; }
    uint8_t a8 = uint8_t(A), c8 = uint8_t(D);
    if (mv.effect == EXPLODE) { c8 >>= 1; if (!c8) c8 = 1; }
    if (!c8) c8 = 1;                                  // the ROM would divide by zero here
    unsigned e = crit ? uint8_t(atk.level * 2u) : atk.level;
    unsigned x = (2u * e) / 5u + 2u;
    x = x * mv.power; x = x * a8; x = x / c8; x = x / 50u;
    unsigned dmg = std::min(x, 997u) + 2u;
    if (mv.type == atk.types[0] || mv.type == atk.types[1]) dmg += dmg >> 1;
    for (unsigned i = 0; i < 3 * 128; i += 3) {
        const uint8_t a = tb(m, tables::TypeEffects, i);
        if (a == 0xff) break;
        const uint8_t d = tb(m, tables::TypeEffects, i + 1);
        if (a == mv.type && (d == def.types[0] || d == def.types[1])) dmg = dmg * tb(m, tables::TypeEffects, i + 2) / 10u;
    }
    if (dmg == 0) return 0;
    if (dmg >= 2) dmg = dmg * r / 255u;
    return uint16_t(dmg);
}

uint8_t hit_chance(const Memory& m, const Combatant& atk, const Combatant& def, const MoveData& mv) {
    uint8_t n, d; stage_ratio(m, atk.accuracy_stage, n, d);
    uint32_t acc = uint32_t(mv.accuracy) * n / d; if (!acc) acc = 1;
    stage_ratio(m, uint8_t(14 - std::clamp<int>(def.evasion_stage, 1, 13)), n, d);
    acc = acc * n / d; if (!acc) acc = 1;
    return uint8_t(std::min<uint32_t>(acc, 255));
}

Damage damage(const Memory& m, const Combatant& atk, const Combatant& def, const MoveData& mv) {
    Damage out{};
    if (!mv.id) return out;
    out.stab = mv.type == atk.types[0] || mv.type == atk.types[1];
    out.type_effect = type_effect(m, mv.type, def.types[0], def.types[1]);
    out.priority = mv.id == QUICK_ATTACK ? 1 : mv.id == COUNTER ? -1 : 0;
    out.hit_rate = (mv.effect == SWIFT || atk.x_accuracy) ? 255 : hit_chance(m, atk, def, mv);
    if (mv.effect == OHKO) {
        out.damaging = true; out.fixed = true;
        if (atk.speed < def.speed) { out.hit_rate = 0; return out; }
        out.min = out.max = out.crit_min = out.crit_max = 65535;
        return out;
    }
    if (mv.effect == SPECIAL_DAMAGE) {
        out.damaging = true; out.fixed = true;
        uint16_t v = atk.level;                            // Seismic Toss, Night Shade
        if (mv.id == 49) v = 20; else if (mv.id == 82) v = 40;   // Sonic Boom, Dragon Rage
        uint16_t lo = v, hi = v;
        if (mv.id == 149) { lo = 1; hi = uint16_t(std::max<unsigned>(1, (atk.level * 3u) / 2u)); }  // Psywave
        out.immune = out.type_effect == 0;
        if (out.immune) lo = hi = 0;
        out.min = out.crit_min = lo; out.max = out.crit_max = hi;
        return out;
    }
    if (mv.effect == SUPER_FANG) {
        out.damaging = true; out.fixed = true;
        out.immune = out.type_effect == 0;
        const uint16_t v = out.immune ? 0 : uint16_t(std::max<unsigned>(1, def.hp / 2u));
        out.min = out.max = out.crit_min = out.crit_max = v;
        return out;
    }
    const bool multi = mv.effect == TWO_TO_FIVE || mv.effect == EFFECT_1E;
    if (mv.power == 0 && !multi) return out;              // status move
    out.damaging = true;
    if (multi) { out.hits_min = 2; out.hits_max = 5; }
    else if (mv.effect == ATTACK_TWICE || mv.effect == TWINEEDLE) { out.hits_min = out.hits_max = 2; }
    out.min = damage_roll(m, atk, def, mv, false, 217); out.max = damage_roll(m, atk, def, mv, false, 255);
    out.crit_min = damage_roll(m, atk, def, mv, true, 217); out.crit_max = damage_roll(m, atk, def, mv, true, 255);
    out.immune = out.max == 0;
    // CriticalHitTest
    unsigned b = atk.base_speed >> 1;
    if (atk.focus_energy) b >>= 1; else b = std::min(255u, b << 1);
    if (high_crit(m, mv.id)) { b = std::min(255u, b << 1); b = std::min(255u, b << 1); } else b >>= 1;
    out.crit_rate = mv.power ? uint8_t(b) : 0;
    return out;
}

uint8_t ko_probability(const Memory& m, const Combatant& atk, const Combatant& def, const MoveData& mv, const Damage& d) {
    if (!d.damaging || d.immune || def.substitute || def.hp == 0) return 0;
    const double hit = d.hit_rate / 256.0;
    if (d.fixed) return uint8_t((d.min >= def.hp ? hit : d.max >= def.hp ? hit * 0.5 : 0.0) * 255.0 + 0.5);
    // Hit-count distribution for 2-5 hit moves: 3/8, 3/8, 1/8, 1/8.
    const double pn[6] = {0, 0, 0.375, 0.375, 0.125, 0.125};
    double p = 0;
    for (int crit = 0; crit < 2; ++crit) {
        const double pc = crit ? d.crit_rate / 256.0 : 1.0 - d.crit_rate / 256.0;
        if (pc <= 0) continue;
        double pr = 0;
        for (unsigned r = 217; r <= 255; ++r) {
            const unsigned per_hit = damage_roll(m, atk, def, mv, crit != 0, uint8_t(r));
            if (d.hits_min == d.hits_max) { if (per_hit * d.hits_min >= def.hp) pr += 1; }
            else for (unsigned n = 2; n <= 5; ++n) if (per_hit * n >= def.hp) pr += pn[n];
        }
        p += pc * pr / 39.0;
    }
    return uint8_t(std::min(255.0, p * hit * 255.0 + 0.5));
}

int8_t move_order(const Combatant& atk, const Combatant& def, uint8_t atk_move, uint8_t def_move) {
    const int pa = atk_move == QUICK_ATTACK ? 1 : atk_move == COUNTER ? -1 : 0;
    const int pd = def_move == QUICK_ATTACK ? 1 : def_move == COUNTER ? -1 : 0;
    if (pa != pd) return pa > pd ? 1 : -1;
    if (atk.speed == def.speed) return 0;
    return atk.speed > def.speed ? 1 : -1;
}

}
