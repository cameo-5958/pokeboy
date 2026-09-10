#pragma once
// Exact Gen I battle arithmetic, reimplemented from pret/pokered
// (engine/battle/core.asm: GetDamageVarsFor*Attack, CalculateDamage,
// AdjustDamageForMoveType, RandomizeDamage, CriticalHitTest, CalcHitChance;
// home/move_mon.asm: CalcStat). Every table is read from the loaded ROM
// through the generated offsets, never transcribed.
#include <cstdint>
#include "memory.h"
namespace pkai::calc {

constexpr uint8_t SPECIAL_TYPE = 0x14;   // types >= FIRE use Special
constexpr uint8_t MEW = 0x15;            // internal species id
constexpr uint8_t STRUGGLE = 165, QUICK_ATTACK = 98, COUNTER = 68;
enum Effect : uint8_t {
    DRAIN_HP = 0x03, EXPLODE = 0x07, DREAM_EATER = 0x08, SWIFT = 0x11,
    TWO_TO_FIVE = 0x1d, EFFECT_1E = 0x1e, OHKO = 0x26, CHARGE = 0x27,
    SUPER_FANG = 0x28, SPECIAL_DAMAGE = 0x29, TRAPPING = 0x2a, FLY = 0x2b,
    ATTACK_TWICE = 0x2c, TWINEEDLE = 0x4d, HYPER_BEAM = 0x50,
};

struct MoveData { uint8_t id{}, effect{}, power{}, type{}, accuracy{}, pp{}; };
struct BaseStats { uint8_t hp{}, atk{}, def{}, spd{}, spc{}, type1{}, type2{}; bool valid{}; };

MoveData move_data(const Memory&, uint8_t move_id);          // 1..165
BaseStats base_stats(const Memory&, uint8_t species);        // internal id
// TypeEffects multiplier (x10) for one (attacking type, defending type) pair; 10 if absent.
uint8_t type_pair(const Memory&, uint8_t attack_type, uint8_t defend_type);
// Product of both defender-type multipliers as applied by AdjustDamageForMoveType,
// in tenths: 10 = neutral, 0 = immune, 40 = 4x.
uint16_t type_effect(const Memory&, uint8_t move_type, uint8_t def_type1, uint8_t def_type2);
// StatModifierRatios lookup for a stage 1..13 (7 = neutral).
void stage_ratio(const Memory&, uint8_t stage, uint8_t& num, uint8_t& den);
uint16_t apply_stage(const Memory&, uint16_t stat, uint8_t stage);   // floor(stat*num/den), min 1, max 999

// CalcStat: base, DV (0..15), stat exp, level. hp selects the HP formula.
uint16_t calc_stat(uint8_t base, uint8_t dv, uint16_t stat_exp, uint8_t level, bool hp);
// Stat-exp prior used for the player's (public-info) stat estimates.
uint16_t player_statexp_prior(uint8_t level);

struct Combatant {
    uint8_t species{}, level{}, types[2]{}, base_speed{};
    uint16_t hp{}, max_hp{};
    uint16_t attack{}, defense{}, speed{}, special{};             // stage-modified (wBattleMon/wEnemyMon)
    uint16_t unmod_attack{}, unmod_defense{}, unmod_special{};    // roster values used on a critical hit
    bool reflect{}, light_screen{}, focus_energy{}, x_accuracy{}, substitute{};
    uint8_t accuracy_stage{7}, evasion_stage{7};
};

struct Damage {
    bool damaging{};      // move deals HP damage through the formula
    bool immune{};        // type product 0 (the ROM turns this into a miss)
    bool fixed{};         // SPECIAL_DAMAGE / SUPER_FANG / OHKO: no random band
    uint16_t min{}, max{};            // per hit, non-critical, r = 217 / 255
    uint16_t crit_min{}, crit_max{};  // per hit, critical
    uint8_t crit_rate{};              // /256 (CriticalHitTest's b)
    uint8_t hit_rate{};               // /256 (CalcHitChance result; 255 = 1/256 miss)
    uint8_t hits_min{1}, hits_max{1}; // multi-hit effects
    uint16_t type_effect{100};        // 100 = neutral
    bool stab{};
    int8_t priority{};                // +1 Quick Attack, -1 Counter
};

// CalcHitChance: raw accuracy byte (/256) after accuracy and evasion stages.
uint8_t hit_chance(const Memory&, const Combatant& atk, const Combatant& def, const MoveData&);
// Damage for one roll r in [217,255] (non-critical when crit=false).
uint16_t damage_roll(const Memory&, const Combatant& atk, const Combatant& def, const MoveData&, bool crit, uint8_t r);
Damage damage(const Memory&, const Combatant& atk, const Combatant& def, const MoveData&);
// Probability (0..255 scale) that one use of the move KOs the defender from its current HP,
// including hit chance, critical hits and the random band (multi-hit uses the mean hit count).
uint8_t ko_probability(const Memory&, const Combatant& atk, const Combatant& def, const MoveData&, const Damage&);
// +1 attacker moves first, -1 defender moves first, 0 speed tie (50/50).
int8_t move_order(const Combatant& atk, const Combatant& def, uint8_t atk_move, uint8_t def_move);

}
