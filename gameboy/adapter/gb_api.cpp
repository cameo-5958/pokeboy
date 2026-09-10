#include "gb_api.h"
#include "core.h"
#include <cstring>
#include <vector>

struct gb_handle {
    GameBoy gb;
    std::vector<uint8_t> state_buf;   // backs the pointer gb_save_state returns
};

extern "C" {

gb_handle* gb_create(void)            { return new gb_handle(); }
void       gb_destroy(gb_handle* h)   { delete h; }

int gb_load_rom(gb_handle* h, const uint8_t* data, size_t len) {
    if (!h->gb.load_rom(data, len)) return 0;
    h->gb.reset_custom_boot();
    return 1;
}

void gb_reset(gb_handle* h)     { h->gb.reset_custom_boot(); }
void gb_reset_post_boot(gb_handle* h) { h->gb.reset_post_boot(); }
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

const uint8_t* gb_save_state(gb_handle* h, size_t* len) {
    if (!h->gb.save_state(h->state_buf)) { if (len) *len = 0; return nullptr; }
    if (len) *len = h->state_buf.size();
    return h->state_buf.data();
}

int gb_load_state(gb_handle* h, const uint8_t* data, size_t len) {
    return h->gb.load_state(data, len) ? 1 : 0;
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

void gb_ai_step(gb_handle* h, int64_t deadline) { if(h) h->gb.ai_step(deadline); }

void gb_set_audio_mask(gb_handle* h, uint8_t mask) {
    h->gb.apu.set_out_mask(mask);
}

} // extern "C"
