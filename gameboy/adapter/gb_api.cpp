#include "gb_api.h"
#include "core.h"
#include <cstring>

struct gb_handle {
    GameBoy gb;
    gb_mod_host_callback mod_callback = nullptr;
    void* mod_user = nullptr;
};

static_assert(static_cast<int>(gbmod::Status::not_found) == GB_MOD_NOT_FOUND,
              "public and internal mod status values must match");

static void dispatch_mod_host_call(void* user, uint32_t mod_handle,
                                   uint32_t import_index, gbmod::CpuContext& core) {
    gb_handle* h = static_cast<gb_handle*>(user);
    if (!h->mod_callback) return;
    gb_mod_cpu_context context{};
    context.struct_size = sizeof(context);
    context.af = core.af; context.bc = core.bc; context.de = core.de;
    context.hl = core.hl; context.sp = core.sp; context.pc = core.pc;
    context.ime = core.ime; context.halted = core.halted;
    h->mod_callback(h, mod_handle, import_index, &context, h->mod_user);
    core.af = context.af; core.bc = context.bc; core.de = context.de;
    core.hl = context.hl; core.sp = context.sp; core.pc = context.pc;
    core.ime = context.ime; core.halted = context.halted;
}

extern "C" {

gb_handle* gb_create(void)            { return new gb_handle(); }
void       gb_destroy(gb_handle* h)   { delete h; }

int gb_load_rom(gb_handle* h, const uint8_t* data, size_t len) {
    if (!h->gb.load_rom(data, len)) return 0;
    h->gb.reset_post_boot();
    return 1;
}

void gb_reset(gb_handle* h)     { h->gb.reset_post_boot(); }
void gb_run_frame(gb_handle* h) { h->gb.run_frame(); }

const uint8_t* gb_framebuffer(const gb_handle* h) { return h->gb.framebuffer(); }

void gb_framebuffer_argb(const gb_handle* h, uint32_t* out, const uint32_t palette[4]) {
    const uint8_t* fb = h->gb.framebuffer();
    for (int i = 0; i < GB_SCREEN_W * GB_SCREEN_H; i++)
        out[i] = palette[fb[i] & 3];
}

void gb_set_input(gb_handle* h, uint8_t buttons, uint8_t dpad) {
    h->gb.set_input(buttons, dpad);
}

int gb_read_audio(gb_handle* h, float* stereo, int max_frames) {
    return h->gb.read_audio(stereo, max_frames);
}

int gb_has_battery(const gb_handle* h) { return h->gb.has_battery() ? 1 : 0; }

const uint8_t* gb_save_ram(const gb_handle* h, size_t* len) {
    return h->gb.save_ram(len);
}

int gb_load_save_ram(gb_handle* h, const uint8_t* data, size_t len) {
    return h->gb.load_save_ram(data, len) ? 1 : 0;
}

void gb_rom_title(const gb_handle* h, char out[17]) {
    out[0] = 0;
    if (!h->gb.cart || h->gb.cart->rom.size() < 0x144) return;
    memcpy(out, h->gb.cart->rom.data() + 0x134, 16);
    out[16] = 0;
    for (int i = 0; i < 16; i++)
        if ((unsigned char)out[i] < 0x20 || (unsigned char)out[i] > 0x7E) { out[i] = 0; break; }
}

uint8_t gb_read_mem(gb_handle* h, uint16_t addr) {
    return h->gb.bus.read8(addr);
}

void gb_write_mem(gb_handle* h, uint16_t addr, uint8_t value) {
    h->gb.bus.write8(addr, value);
}

gb_mod_status gb_mod_load_symbols(gb_handle* h, const char* text, size_t len) {
    if (!h) return GB_MOD_INVALID_ARGUMENT;
    return static_cast<gb_mod_status>(h->gb.mods.load_symbols(text, len));
}

gb_mod_status gb_mod_load(gb_handle* h, const uint8_t* data, size_t len,
                          uint32_t* out_handle) {
    if (!h) return GB_MOD_INVALID_ARGUMENT;
    return static_cast<gb_mod_status>(h->gb.mods.load_package(data, len, out_handle));
}

gb_mod_status gb_mod_unload(gb_handle* h, uint32_t handle) {
    if (!h) return GB_MOD_INVALID_ARGUMENT;
    return static_cast<gb_mod_status>(h->gb.mods.unload_package(handle));
}

size_t gb_mod_count(const gb_handle* h) {
    return h ? h->gb.mods.package_count() : 0;
}

const char* gb_mod_id(const gb_handle* h, uint32_t handle) {
    return h ? h->gb.mods.package_id(handle) : nullptr;
}

const char* gb_mod_name(const gb_handle* h, uint32_t handle) {
    return h ? h->gb.mods.package_name(handle) : nullptr;
}

size_t gb_mod_import_count(const gb_handle* h, uint32_t handle) {
    return h ? h->gb.mods.import_count(handle) : 0;
}

const char* gb_mod_import_name(const gb_handle* h, uint32_t handle, size_t index) {
    return h ? h->gb.mods.import_name(handle, index) : nullptr;
}

const uint8_t* gb_mod_metadata(const gb_handle* h, uint32_t handle, size_t* len) {
    if (!h) { if (len) *len = 0; return nullptr; }
    return h->gb.mods.metadata(handle, len);
}

const char* gb_mod_last_error(const gb_handle* h) {
    return h ? h->gb.mods.last_error() : "invalid emulator handle";
}

void gb_mod_set_host_callback(gb_handle* h, gb_mod_host_callback callback, void* user) {
    if (!h) return;
    h->mod_callback = callback;
    h->mod_user = user;
    h->gb.mods.set_host_callback(callback ? dispatch_mod_host_call : nullptr,
                                 callback ? h : nullptr);
}

void gb_set_audio_mask(gb_handle* h, uint8_t mask) {
    h->gb.apu.set_out_mask(mask);
}

} // extern "C"
