// calc vs the ROM: the patched ROM's own GetDamageVarsFor*Attack,
// CalculateDamage, AdjustDamageForMoveType and CalcHitChance are executed in
// the emulator over randomised battle states and compared with pkai::calc.
#include "core.h"
#include "pkai/calc.h"
#include <fstream>
#include <iterator>
#include <iostream>
#include <cstdlib>
#include <random>
using namespace pkai;
using namespace pkai::symbols;
#define CHECK(x) do { if (!(x)) { std::cerr << __LINE__ << ": " << #x << "\n"; std::exit(1); } } while (0)

struct Rom {
    GameBoy g;
    Rom() {
        std::ifstream f(AI_ROM, std::ios::binary);
        std::vector<uint8_t> r{std::istreambuf_iterator<char>(f), {}};
        CHECK(g.load_rom(r.data(), r.size())); g.reset_post_boot();
        for (int i = 0; i < 4; ++i) g.run_frame();
    }
    // Run `call A; call B; ...` from a WRAM stub in bank `bank`, until the stub's end.
    bool chain(uint8_t bank, std::initializer_list<tables::Address> routines) {
        uint16_t p = 0xc100;
        for (auto r : routines) { g.bus.write8(p++, 0xcd); g.bus.write8(p++, r.address & 0xff); g.bus.write8(p++, r.address >> 8); }
        g.bus.write8(p, 0x18); g.bus.write8(p + 1, 0xfe);   // jr $ (sentinel)
        g.bus.write8(0x2000, bank); g.bus.write8(hLoadedROMBank, bank);
        g.cpu.pc = 0xc100; g.cpu.sp = 0xdff0; g.cpu.halted = false; g.cpu.ime = false;
        for (unsigned i = 0; i < 2000000 && g.cpu.pc != p; ++i) g.cpu.execute_next();
        return g.cpu.pc == p;   // false: the ROM froze (its own divide-by-zero bug)
    }
    void w8(uint16_t a, uint8_t v) { g.bus.write8(a, v); }
    void w16(uint16_t a, uint16_t v) { g.bus.write8(a, v >> 8); g.bus.write8(a + 1, v & 0xff); }
    uint16_t r16(uint16_t a) { return uint16_t(g.bus.read8(a) << 8) | g.bus.read8(a + 1); }
};

int main() {
  if (!std::ifstream(AI_ROM).good()) {       // 77: ctest SKIP_RETURN_CODE
    std::cerr << "no ROM at " << AI_ROM << ", build it with pred-patch/build_ai.sh\n";
    return 77;
  }

    Rom rom; Memory m = rom.g.ai_memory();
    std::mt19937 rng(20260910);
    auto pick = [&](unsigned lo, unsigned hi) { return lo + rng() % (hi - lo + 1); };

    // Table sanity: Bulbasaur (internal $99) is dex 1 Grass/Poison 45/49/49/45/65.
    auto b = calc::base_stats(m, 0x99); CHECK(b.valid && b.hp == 45 && b.atk == 49 && b.def == 49 && b.spd == 45 && b.spc == 65 && b.type1 == 0x16 && b.type2 == 0x03);
    CHECK(calc::base_stats(m, calc::MEW).valid && calc::base_stats(m, calc::MEW).hp == 100);
    auto ember = calc::move_data(m, 52); CHECK(ember.power == 40 && ember.type == 0x14 && ember.accuracy == 255);
    CHECK(calc::type_effect(m, 0x15, 0x14, 0x14) == 20);        // Water vs Fire
    CHECK(calc::type_effect(m, 0x04, 0x02, 0x02) == 0);         // Ground vs Flying
    CHECK(calc::type_effect(m, 0x17, 0x15, 0x02) == 40);        // Electric vs Water/Flying
    CHECK(calc::calc_stat(45, 15, 65535, 100, true) == 293);    // L100 max Bulbasaur HP (bonus capped at 63)
    CHECK(calc::calc_stat(49, 15, 65535, 100, false) == 196);

    unsigned checked = 0, crits = 0, immune = 0, frozen = 0;
    for (unsigned iter = 0; iter < 4000; ++iter) {
        const bool enemy_attacks = iter & 1;
        // Random valid species on both sides.
        uint8_t ps, es;
        do ps = uint8_t(pick(1, 190)); while (!calc::base_stats(m, ps).valid);
        do es = uint8_t(pick(1, 190)); while (!calc::base_stats(m, es).valid);
        const auto pb = calc::base_stats(m, ps), eb = calc::base_stats(m, es);
        // Random damaging move with the plain damage formula.
        calc::MoveData mv;
        do mv = calc::move_data(m, uint8_t(pick(1, 165)));
        while (mv.power == 0 || mv.effect == calc::OHKO || mv.effect == calc::SPECIAL_DAMAGE || mv.effect == calc::SUPER_FANG);
        calc::Combatant P{}, E{};
        P.species = ps; P.level = uint8_t(pick(2, 100)); P.types[0] = pb.type1; P.types[1] = pb.type2; P.base_speed = pb.spd;
        E.species = es; E.level = uint8_t(pick(2, 100)); E.types[0] = eb.type1; E.types[1] = eb.type2; E.base_speed = eb.spd;
        const bool wide = pick(0, 3) == 0;   // sometimes exercise the >255 scaling path
        auto stat = [&] { return uint16_t(wide ? pick(1, 999) : pick(1, 255)); };
        P.attack = stat(); P.defense = stat(); P.speed = stat(); P.special = stat();
        E.attack = stat(); E.defense = stat(); E.speed = stat(); E.special = stat();
        P.unmod_attack = stat(); P.unmod_defense = stat(); P.unmod_special = stat();
        // Enemy roster stats come from GetEnemyMonStat: base stats + DVs, no stat exp.
        const uint8_t dv0 = uint8_t(pick(0, 255)), dv1 = uint8_t(pick(0, 255));
        E.unmod_attack = calc::calc_stat(eb.atk, dv0 >> 4, 0, E.level, false);
        E.unmod_defense = calc::calc_stat(eb.def, dv0 & 15, 0, E.level, false);
        E.unmod_special = calc::calc_stat(eb.spc, dv1 & 15, 0, E.level, false);
        const bool crit = pick(0, 3) == 0;
        P.reflect = pick(0, 1); P.light_screen = pick(0, 1); E.reflect = pick(0, 1); E.light_screen = pick(0, 1);

        // WRAM image.
        rom.w8(wBattleMonSpecies, ps); rom.w8(wBattleMonLevel, P.level); rom.w8(wBattleMonType1, P.types[0]); rom.w8(wBattleMonType2, P.types[1]);
        rom.w16(wBattleMonAttack, P.attack); rom.w16(wBattleMonDefense, P.defense); rom.w16(wBattleMonSpeed, P.speed); rom.w16(wBattleMonSpecial, P.special);
        rom.w8(wEnemyMonSpecies, es); rom.w8(wEnemyMonLevel, E.level); rom.w8(wEnemyMonType1, E.types[0]); rom.w8(wEnemyMonType2, E.types[1]);
        rom.w16(wEnemyMonAttack, E.attack); rom.w16(wEnemyMonDefense, E.defense); rom.w16(wEnemyMonSpeed, E.speed); rom.w16(wEnemyMonSpecial, E.special);
        rom.w8(wEnemyMonDVs, dv0); rom.w8(wEnemyMonDVs + 1, dv1);
        rom.w8(wPlayerMonNumber, 0);
        rom.w16(wPartyMon1Attack, P.unmod_attack); rom.w16(wPartyMon1Defense, P.unmod_defense); rom.w16(wPartyMon1Special, P.unmod_special);
        rom.w8(wPlayerBattleStatus3, uint8_t((P.light_screen << 1) | (P.reflect << 2)));
        rom.w8(wEnemyBattleStatus3, uint8_t((E.light_screen << 1) | (E.reflect << 2)));
        rom.w8(wCriticalHitOrOHKO, crit); rom.w8(wMoveMissed, 0); rom.w8(wDamageMultipliers, 0);
        rom.w8(wLinkState, 0);
        const uint16_t base = enemy_attacks ? wEnemyMoveNum : wPlayerMoveNum;
        rom.w8(base, mv.id); rom.w8(base + 1, mv.effect); rom.w8(base + 2, mv.power); rom.w8(base + 3, mv.type); rom.w8(base + 4, mv.accuracy);
        rom.w8(hWhoseTurn, enemy_attacks);
        if (!rom.chain(15, {enemy_attacks ? tables::GetDamageVarsForEnemyAttack : tables::GetDamageVarsForPlayerAttack, tables::CalculateDamage, tables::AdjustDamageForMoveType})) { ++frozen; continue; }
        const uint16_t rom_damage = rom.r16(wDamage);
        const bool rom_missed = rom.g.bus.read8(wMoveMissed) != 0;
        const uint16_t ours = enemy_attacks ? calc::damage_roll(m, E, P, mv, crit, 255) : calc::damage_roll(m, P, E, mv, crit, 255);
        if (rom_damage != ours) {
            std::cerr << "mismatch iter " << iter << " move " << int(mv.id) << " crit " << crit << " enemy_attacks " << enemy_attacks
                      << " rom " << rom_damage << " ours " << ours << "\n"; return 1;
        }
        CHECK(rom_missed == (ours == 0));
        checked++; crits += crit; immune += ours == 0;

        // CalcHitChance for the same pair.
        P.accuracy_stage = uint8_t(pick(1, 13)); P.evasion_stage = uint8_t(pick(1, 13));
        E.accuracy_stage = uint8_t(pick(1, 13)); E.evasion_stage = uint8_t(pick(1, 13));
        rom.w8(wPlayerMonStatMods + 4, P.accuracy_stage); rom.w8(wPlayerMonStatMods + 5, P.evasion_stage);
        rom.w8(wEnemyMonStatMods + 4, E.accuracy_stage); rom.w8(wEnemyMonStatMods + 5, E.evasion_stage);
        rom.w8(base + 4, mv.accuracy);
        CHECK(rom.chain(15, {tables::CalcHitChance}));
        const auto d = enemy_attacks ? calc::damage(m, E, P, mv) : calc::damage(m, P, E, mv);
        const uint8_t rom_hit = rom.g.bus.read8(base + 4);
        const uint8_t raw = enemy_attacks ? calc::hit_chance(m, E, P, mv) : calc::hit_chance(m, P, E, mv);
        CHECK(d.hit_rate == (mv.effect == calc::SWIFT ? 255 : raw));
        if (rom_hit != raw) {
            std::cerr << "hit mismatch iter " << iter << " move " << int(mv.id) << " effect " << int(mv.effect) << " acc " << int(mv.accuracy)
                      << " stages " << int(enemy_attacks ? E.accuracy_stage : P.accuracy_stage) << "/" << int(enemy_attacks ? P.evasion_stage : E.evasion_stage)
                      << " rom " << int(rom_hit) << " ours " << int(raw) << "\n"; return 1;
        }
    }
    std::cout << "calc parity: " << checked << " damage cases (" << crits << " critical, " << immune << " immune, " << frozen << " skipped: ROM divide-by-zero freeze) + hit chance\n";

    // ko_probability sanity: a Level 100 Tauros Body Slam on a 1-HP target is ~1, on a 999-HP target is 0.
    calc::Combatant T{}; T.level = 100; T.types[0] = T.types[1] = 0; T.attack = T.unmod_attack = 298; T.base_speed = 110;
    calc::Combatant V{}; V.level = 100; V.types[0] = V.types[1] = 0; V.defense = V.unmod_defense = 200; V.hp = 1; V.max_hp = 300;
    auto bs = calc::move_data(m, 34); auto dm = calc::damage(m, T, V, bs);
    CHECK(dm.damaging && dm.min > 0 && dm.max >= dm.min && dm.crit_max > dm.max);
    CHECK(calc::ko_probability(m, T, V, bs, dm) >= 250);
    V.hp = 999; CHECK(calc::ko_probability(m, T, V, bs, dm) == 0);
    CHECK(calc::move_order(T, V, calc::QUICK_ATTACK, 34) == 1);
    return 0;
}
