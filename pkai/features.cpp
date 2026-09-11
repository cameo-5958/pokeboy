#include "features.h"
#include <algorithm>
#include <cmath>
namespace pkai {
using namespace symbols;
namespace {

int8_t q(double v) { v = std::clamp(v, -1.0, 1.0); return int8_t(std::lround(v * 127.0)); }
int8_t flag(bool b) { return b ? 127 : 0; }
int8_t frac(unsigned num, unsigned den) { return den ? q(double(num) / double(den)) : 0; }

// Battle status bit positions (constants/battle_constants.asm).
enum : uint8_t { STORING_ENERGY = 0, THRASHING = 1, MULTI_ATTACK = 2, FLINCHED = 3, CHARGING = 4, TRAPPING = 5, INVULNERABLE = 6, CONFUSED = 7 };
enum : uint8_t { X_ACCURACY = 0, MIST = 1, PUMPED = 2, SUBSTITUTE = 4, RECHARGE = 5, RAGE = 6, SEEDED = 7 };
enum : uint8_t { BADLY_POISONED = 0, LIGHT_SCREEN = 1, REFLECT = 2, TRANSFORMED = 3 };
bool bit(uint8_t v, uint8_t b) { return (v >> b) & 1; }

// Status one-hot: sleep, poison, burn, freeze, paralysis, toxic(from status3).
void status_feats(int8_t* f, uint8_t status, bool toxic) {
    f[0] = flag(status & 1); f[1] = flag(bit(status, 3)); f[2] = flag(bit(status, 4));
    f[3] = flag(bit(status, 5)); f[4] = flag(bit(status, 6)); f[5] = flag(toxic);
}
void stage_feats(int8_t* f, const uint8_t* stages) { for (int i = 0; i < 6; ++i) f[i] = q((int(stages[i]) - 7) / 6.0); }
void volatile_feats(int8_t* f, const uint8_t* s) {   // 12 flags
    f[0] = flag(bit(s[0], CONFUSED)); f[1] = flag(bit(s[1], SUBSTITUTE)); f[2] = flag(bit(s[2], REFLECT));
    f[3] = flag(bit(s[2], LIGHT_SCREEN)); f[4] = flag(bit(s[1], SEEDED)); f[5] = flag(bit(s[1], PUMPED));
    f[6] = flag(bit(s[1], MIST)); f[7] = flag(bit(s[0], TRAPPING)); f[8] = flag(bit(s[1], RECHARGE) || bit(s[0], CHARGING));
    f[9] = flag(bit(s[1], X_ACCURACY)); f[10] = flag(bit(s[0], STORING_ENERGY)); f[11] = flag(bit(s[2], TRANSFORMED));
}

uint8_t best_type_effect(const Memory& m, const uint8_t* moves, unsigned n, uint8_t t1, uint8_t t2) {
    unsigned best = 0;
    for (unsigned i = 0; i < n; ++i) if (moves[i]) best = std::max<unsigned>(best, calc::type_effect(m, calc::move_data(m, moves[i]).type, t1, t2));
    return uint8_t(std::min(best, 40u));
}

void move_feats(Token& t, const Memory& m, const calc::Combatant& atk, const calc::Combatant& def, uint8_t move_id, bool disabled, bool struggle) {
    const auto mv = calc::move_data(m, move_id);
    const auto d = calc::damage(m, atk, def, mv);
    t.cat[0] = move_id; t.cat[1] = mv.effect; t.cat[2] = mv.id ? mv.type + 1 : 0;
    int8_t* f = t.f;
    f[0] = flag(mv.id); f[1] = flag(disabled); f[2] = flag(d.stab); f[3] = q(d.type_effect / 40.0);
    const unsigned hp = std::max<unsigned>(1, def.hp);
    f[4] = frac(std::min<unsigned>(d.min, hp), hp); f[5] = frac(std::min<unsigned>(d.max, hp), hp);
    f[6] = frac(std::min<unsigned>(d.crit_max, hp), hp);
    f[7] = q(calc::ko_probability(m, atk, def, mv, d) / 255.0);
    f[8] = q(d.hit_rate / 255.0); f[9] = q(d.crit_rate / 255.0); f[10] = q(d.priority);
    f[11] = flag(mv.id && mv.type < calc::SPECIAL_TYPE); f[12] = q(mv.power / 255.0); f[13] = q(mv.accuracy / 255.0);
    f[14] = q((d.hits_min + d.hits_max) / 10.0); f[15] = flag(d.immune); f[16] = flag(d.fixed); f[17] = flag(struggle);
    f[18] = flag(d.damaging); f[19] = frac(std::min<unsigned>(d.max, def.max_hp), std::max<unsigned>(1, def.max_hp));
}

}

calc::Combatant own_combatant(const Memory& m, const Observation& o) {
    calc::Combatant c{};
    const auto& a = o.active;
    const auto b = calc::base_stats(m, a.species);
    c.species = a.species; c.level = a.level; c.types[0] = a.types[0]; c.types[1] = a.types[1]; c.base_speed = b.spd;
    c.hp = a.hp; c.max_hp = a.max_hp;
    c.attack = a.stats[0]; c.defense = a.stats[1]; c.speed = a.stats[2]; c.special = a.stats[3];
    // GetEnemyMonStat: base stats + DVs, no stat exp.
    c.unmod_attack = calc::calc_stat(b.atk, a.dvs[0] >> 4, 0, a.level, false);
    c.unmod_defense = calc::calc_stat(b.def, a.dvs[0] & 15, 0, a.level, false);
    c.unmod_special = calc::calc_stat(b.spc, a.dvs[1] & 15, 0, a.level, false);
    c.reflect = bit(o.battle_status[2], REFLECT); c.light_screen = bit(o.battle_status[2], LIGHT_SCREEN);
    c.focus_energy = bit(o.battle_status[1], PUMPED); c.x_accuracy = bit(o.battle_status[1], X_ACCURACY);
    c.substitute = bit(o.battle_status[1], SUBSTITUTE) && o.substitute > 0;
    c.accuracy_stage = o.stages[4]; c.evasion_stage = o.stages[5];
    return c;
}

calc::Combatant player_combatant(const Memory& m, const Observation& o) {
    calc::Combatant c{};
    const auto& p = o.player[std::min<uint8_t>(o.player_slot, 5)];
    const auto b = calc::base_stats(m, p.species);
    c.species = p.species; c.level = p.level; c.types[0] = p.types[0]; c.types[1] = p.types[1]; c.base_speed = b.spd;
    c.hp = p.hp; c.max_hp = p.max_hp;
    const uint16_t prior = calc::player_statexp_prior(p.level);
    c.unmod_attack = calc::calc_stat(b.atk, 8, prior, p.level, false);
    c.unmod_defense = calc::calc_stat(b.def, 8, prior, p.level, false);
    c.unmod_special = calc::calc_stat(b.spc, 8, prior, p.level, false);
    const uint16_t speed = calc::calc_stat(b.spd, 8, prior, p.level, false);
    c.attack = calc::apply_stage(m, c.unmod_attack, o.player_stages[0]);
    c.defense = calc::apply_stage(m, c.unmod_defense, o.player_stages[1]);
    c.speed = calc::apply_stage(m, speed, o.player_stages[2]);
    c.special = calc::apply_stage(m, c.unmod_special, o.player_stages[3]);
    c.reflect = bit(o.player_visible_status[2], REFLECT); c.light_screen = bit(o.player_visible_status[2], LIGHT_SCREEN);
    c.focus_energy = bit(o.player_visible_status[1], PUMPED);
    c.substitute = bit(o.player_visible_status[1], SUBSTITUTE);
    c.accuracy_stage = o.player_stages[4]; c.evasion_stage = o.player_stages[5];
    return c;
}

Features build_features(const Memory& m, const Observation& o, const Mask& mask, uint8_t request_kind) {
    Features F{};
    F.legal = mask.bits; F.request_kind = request_kind;
    const auto own = own_combatant(m, o);
    const auto ply = player_combatant(m, o);
    const auto& pa = o.player[std::min<uint8_t>(o.player_slot, 5)];
    unsigned own_alive = 0, ply_known_alive = 0, ply_known = 0;
    for (unsigned i = 0; i < o.own_count; ++i) own_alive += o.own[i].hp > 0;
    for (unsigned i = 0; i < o.player_count; ++i) { ply_known += o.player[i].known; ply_known_alive += o.player[i].known && o.player[i].hp > 0; }
    const uint8_t own_move_ids[4] = {o.active.moves[0], o.active.moves[1], o.active.moves[2], o.active.moves[3]};

    auto add = [&](TokenType type, uint8_t index) -> Token& {
        Token& t = F.tokens[F.count++]; t.type = type; t.index = index; t.present = 1; return t;
    };

    // Field token.
    {
        Token& t = add(TokField, 0);
        t.cat[0] = o.trainer_class; t.cat[1] = request_kind + 1;
        t.f[0] = q(std::min<unsigned>(o.round, 50) / 50.0); t.f[1] = q(own_alive / 6.0); t.f[2] = q(ply_known_alive / 6.0);
        t.f[3] = q(o.player_count / 6.0); t.f[4] = q((o.player_count - std::min<unsigned>(ply_known, o.player_count)) / 6.0);
        t.f[5] = q(std::min<uint8_t>(o.count, 5) / 5.0); t.f[6] = flag(request_kind == 1); t.f[7] = q(o.own_count / 6.0);
        t.f[8] = q(calc::move_order(own, ply, 0, 0) / 1.0);
        t.f[9] = flag(mask.struggle);
    }
    // Own Pokémon tokens (switch candidates 4..9).
    for (unsigned i = 0; i < 6; ++i) {
        Token& t = add(TokOwnMon, uint8_t(i));
        const auto& p = o.own[i];
        const bool present = i < o.own_count && p.species;
        t.present = present; t.candidate = uint8_t(5 + i);
        if (!present) continue;
        const bool active = i == o.own_slot;
        const auto b = calc::base_stats(m, p.species);
        t.cat[0] = p.species; t.cat[1] = p.types[0] + 1; t.cat[2] = p.types[1] + 1;
        t.cat[3] = uint16_t(p.species) * 191u + pa.species;   // matchup vs the player's active
        int8_t* f = t.f;
        f[0] = flag(active); f[1] = flag(p.hp == 0); f[2] = frac(p.hp, p.max_hp); f[3] = q(p.level / 100.0);
        status_feats(f + 4, p.status, active && bit(o.battle_status[2], BADLY_POISONED));
        if (active) { stage_feats(f + 10, o.stages); volatile_feats(f + 16, o.battle_status); }
        f[28] = q(calc::move_order(active ? own : calc::Combatant{}, ply, 0, 0) / 1.0);
        if (!active) {
            // Bench: speed estimate from roster stats vs player active, best type effect both ways.
            calc::Combatant bench{}; bench.speed = p.stats[2];
            f[28] = q(calc::move_order(bench, ply, 0, 0) / 1.0);
        }
        f[29] = q(best_type_effect(m, p.moves, 4, pa.types[0], pa.types[1]) / 40.0);
        f[30] = q(best_type_effect(m, pa.moves, 4, p.types[0], p.types[1]) / 40.0);
        f[31] = flag(mask.legal(4 + i)); f[32] = q(p.max_hp / 999.0); f[33] = q(b.spd / 255.0);
        f[34] = q(p.stats[0] / 999.0); f[35] = q(p.stats[1] / 999.0); f[36] = q(p.stats[2] / 999.0); f[37] = q(p.stats[3] / 999.0);
    }
    // Player Pokémon tokens.
    for (unsigned i = 0; i < 6; ++i) {
        Token& t = add(TokPlayerMon, uint8_t(i));
        const bool in_party = i < o.player_count;
        t.present = in_party;
        if (!in_party) continue;
        const auto& p = o.player[i];
        const bool active = i == o.player_slot;
        int8_t* f = t.f;
        f[0] = flag(active); f[1] = flag(p.known);
        if (!p.known) continue;
        t.cat[0] = p.species; t.cat[1] = p.types[0] + 1; t.cat[2] = p.types[1] + 1;
        t.cat[3] = uint16_t(p.species) * 191u + o.active.species;
        f[2] = flag(p.hp == 0); f[3] = frac(p.hp, p.max_hp); f[4] = q(p.level / 100.0);
        status_feats(f + 5, p.status, active && bit(o.player_visible_status[2], BADLY_POISONED));
        if (active) { stage_feats(f + 11, o.player_stages); volatile_feats(f + 17, o.player_visible_status); }
        f[29] = q(std::min<unsigned>(o.round - std::min(o.round, p.observed_round), 50) / 50.0);
        unsigned revealed = 0; for (auto v : p.moves) revealed += v != 0;
        f[30] = q(revealed / 4.0);
        f[31] = q(best_type_effect(m, p.moves, 4, o.active.types[0], o.active.types[1]) / 40.0);
        f[32] = q(best_type_effect(m, own_move_ids, 4, p.types[0], p.types[1]) / 40.0);
        f[33] = q(p.max_hp / 999.0);
    }
    // Own move tokens (candidates 0..3).
    for (unsigned i = 0; i < 4; ++i) {
        Token& t = add(TokOwnMove, uint8_t(i));
        t.candidate = uint8_t(1 + i);
        const uint8_t id = mask.struggle && i == 0 ? calc::STRUGGLE : o.active.moves[i];
        t.present = id != 0;
        if (!id) continue;
        move_feats(t, m, own, ply, id, i + 1 == o.disabled, mask.struggle && i == 0);
        t.f[20] = flag(mask.legal(i));
    }
    // Player move tokens (revealed only), computed against our active.
    for (unsigned i = 0; i < 4; ++i) {
        Token& t = add(TokPlayerMove, uint8_t(i));
        const uint8_t id = pa.known ? pa.moves[i] : 0;
        t.present = id != 0;
        if (!id) continue;
        move_feats(t, m, ply, own, id, false, false);
    }
    // Item tokens (candidates 10..15).
    for (unsigned i = 0; i < 6; ++i) {
        Token& t = add(TokItem, uint8_t(i));
        t.candidate = uint8_t(11 + i); t.cat[0] = uint16_t(i + 1);
        t.f[0] = flag(mask.legal(10 + i)); t.f[1] = frac(o.active.hp, o.active.max_hp);
        t.f[2] = q(std::min<uint8_t>(o.count, 5) / 5.0); t.f[3] = flag(o.active.status != 0);
        t.f[4] = flag(mask.item != 0 && mask.legal(10 + i));
    }
    return F;
}

uint32_t features_hash(const Features& F) {
    uint32_t h = 2166136261u;
    auto add = [&](uint32_t x) { h = (h ^ x) * 16777619u; };
    add(F.count); add(F.legal); add(F.request_kind);
    for (unsigned i = 0; i < F.count; ++i) {
        const Token& t = F.tokens[i];
        add(t.type); add(t.index); add(t.present); add(t.candidate);
        for (auto c : t.cat) add(c);
        for (auto v : t.f) add(uint8_t(v));
    }
    return h;
}

}
