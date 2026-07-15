#include "core.h"
#include <algorithm>
#include <cstring>

bool GameBoy::load_rom(const uint8_t* data, size_t len) {
    cart = Cartridge::create(data, len);           // picks MBC from header byte $0147
    if (!cart) return false;
    bus.attach(cart.get(), &ppu, &timer, &joypad, &apu);
    cpu.bus = &bus;
    cpu.mods = &mods;
    mods.on_rom_loaded(cart.get());
    return true;
}

void GameBoy::reset_custom_boot() {
    cpu = CPU{};
    cpu.bus = &bus;
    cpu.mods = &mods;
    cpu.sp = 0xFFFE;
    cpu.pc = 0x0000;

    ppu = PPU{};
    memset(bus.vram, 0, sizeof(bus.vram));
    memset(bus.wram, 0, sizeof(bus.wram));
    memset(bus.oam, 0, sizeof(bus.oam));
    memset(bus.hram, 0, sizeof(bus.hram));
    bus.if_reg = 0;
    bus.ie_reg = 0;
    bus.boot_rom_bank = 0;
    bus.boot_rom_enabled = true;
    frame_budget = 0;
}

void GameBoy::reset_post_boot() {                  // values: PDF §4.3
    cpu = CPU{}; cpu.bus = &bus; cpu.mods = &mods;
    cpu.af = 0x01B0; cpu.bc = 0x0013; cpu.de = 0x00D8; cpu.hl = 0x014D;
    cpu.sp = 0xFFFE; cpu.pc = 0x0100;
    static const struct { uint16_t a; uint8_t v; } io[] = {
        {0xFF00,0xCF},{0xFF02,0x7E},{0xFF04,0xAB},{0xFF07,0xF8},{0xFF0F,0xE1},
        {0xFF10,0x80},{0xFF11,0xBF},{0xFF12,0xF3},{0xFF13,0xFF},{0xFF14,0xBF},
        {0xFF16,0x3F},{0xFF18,0xFF},{0xFF19,0xBF},{0xFF1A,0x7F},{0xFF1B,0xFF},
        {0xFF1C,0x9F},{0xFF1D,0xFF},{0xFF1E,0xBF},{0xFF20,0xFF},{0xFF23,0xBF},
        {0xFF24,0x77},{0xFF25,0xF3},{0xFF26,0xF1},{0xFF40,0x91},{0xFF41,0x85},
        {0xFF46,0xFF},{0xFF47,0xFC},{0xFF50,0x01},
    };
    for (auto& r : io) bus.write_io_raw(r.a, r.v);
    bus.boot_rom_enabled = false;
    bus.boot_rom_bank = 0;
    frame_budget = 0;
}

void GameBoy::run_frame() {
    frame_budget += 70224;
    while (frame_budget > 0) {
        int t = cpu.execute_next();
        ppu.tick(t, bus);
        timer.tick(t, bus);
        apu.tick(t);                               // no-op until M6
        frame_budget -= t;
    }
}

const uint8_t* GameBoy::save_ram(size_t* len) const {
    if (!cart || cart->ram.empty()) { *len = 0; return nullptr; }
    *len = cart->ram.size(); return cart->ram.data();
}

bool GameBoy::load_save_ram(const uint8_t* data, size_t len) {
    if (!cart || cart->ram.empty() || len > cart->ram.size()) return false;
    std::copy(data, data + len, cart->ram.begin());
    return true;
}

// FNV-1a over the ROM image. Identity only — cheap enough to run on every
// save/load and strong enough to catch "wrong ROM" and "patched ROM", which
// are the mistakes that produce states that load but then desync.
static uint32_t rom_fingerprint(const std::vector<uint8_t>& rom) {
    uint32_t h = 2166136261u;
    for (uint8_t b : rom) { h ^= b; h *= 16777619u; }
    return h;
}

// Visits every subsystem in a fixed order. Save and load share this one body,
// so the two directions cannot disagree about the layout.
void GameBoy::transfer_state(StateIO& s) {
    cpu.serialize(s);
    bus.serialize(s);
    ppu.serialize(s);
    timer.serialize(s);
    joypad.serialize(s);
    apu.serialize(s);
    cart->serialize(s);
    s.v(frame_budget);
}

bool GameBoy::save_state(std::vector<uint8_t>& out) {
    if (!cart) return false;
    out.clear();
    StateIO s(&out);
    uint8_t magic[4] = { 'G', 'B', 'S', 'T' };
    uint32_t version = STATE_VERSION;
    uint32_t rom_size = (uint32_t)cart->rom.size();
    uint32_t rom_hash = rom_fingerprint(cart->rom);
    s.arr(magic); s.v(version); s.v(rom_size); s.v(rom_hash);
    transfer_state(s);
    if (!s.ok()) { out.clear(); return false; }
    return true;
}

// Reads and validates the stream header. Returns false unless the state was
// produced by this build against this exact ROM image.
bool GameBoy::check_state_header(StateIO& s) const {
    uint8_t magic[4] = {};
    uint32_t version = 0, rom_size = 0, rom_hash = 0;
    s.arr(magic); s.v(version); s.v(rom_size); s.v(rom_hash);
    if (!s.ok()) return false;
    if (memcmp(magic, "GBST", 4) != 0) return false;
    if (version != STATE_VERSION) return false;
    if (rom_size != cart->rom.size()) return false;
    return rom_hash == rom_fingerprint(cart->rom);
}

bool GameBoy::load_state(const uint8_t* data, size_t len) {
    if (!cart || !data) return false;

    // Pass 1 parses the whole stream into a throwaway machine. A truncated or
    // malformed state must fail before the live machine is touched, otherwise a
    // bad file leaves a half-overwritten, unplayable emulator. Heap, not stack:
    // a GameBoy is ~300KB (the APU ring buffer alone is 256KB).
    {
        StateIO probe(data, len);
        if (!check_state_header(probe)) return false;
        auto scratch = std::unique_ptr<GameBoy>(new GameBoy());
        scratch->cart = Cartridge::create(cart->rom.data(), cart->rom.size());
        if (!scratch->cart) return false;
        scratch->transfer_state(probe);
        if (!probe.ok()) return false;
    }

    // Pass 2 replays the identical bytes into the live machine, which cannot
    // fail now that pass 1 accepted them. Applying in place is what keeps the
    // wiring intact: no serialize() touches a pointer, so bus/cpu back-pointers
    // and the mod runtime's cartridge pointer all stay bound to this machine.
    // (Copying a scratch machine over this one would dangle both.)
    StateIO s(data, len);
    check_state_header(s);
    transfer_state(s);
    return s.ok();
}
