// C ABI over the C++ core. Every frontend (Win32 now, iOS later via
// Objective-C/Swift bridging) talks to the emulator through this header only.
#pragma once
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct gb_handle gb_handle;

// Status values returned by the mod APIs. A detailed message is available
// through gb_mod_last_error after any non-zero result.
typedef enum gb_mod_status {
    GB_MOD_OK = 0,
    GB_MOD_INVALID_ARGUMENT = 1,
    GB_MOD_NO_ROM = 2,
    GB_MOD_BAD_SYMBOLS = 3,
    GB_MOD_BAD_PACKAGE = 4,
    GB_MOD_UNSUPPORTED_VERSION = 5,
    GB_MOD_ROM_MISMATCH = 6,
    GB_MOD_MISSING_SYMBOL = 7,
    GB_MOD_CONFLICT = 8,
    GB_MOD_LINK_ERROR = 9,
    GB_MOD_NOT_FOUND = 10,
} gb_mod_status;

// Register state visible to a synchronous host import. The TypeScript host
// may update any field before returning; AF's low flag nibble is always masked.
typedef struct gb_mod_cpu_context {
    uint32_t struct_size;
    uint16_t af, bc, de, hl, sp, pc;
    uint8_t ime, halted;
    uint8_t reserved[2];
} gb_mod_cpu_context;

typedef void (*gb_mod_host_callback)(
    gb_handle* gb,
    uint32_t mod_handle,
    uint32_t import_index,
    gb_mod_cpu_context* context,
    void* user);

// Button bit masks for gb_set_input (1 = held)
enum {
    GB_BTN_A      = 0x01, GB_BTN_B    = 0x02,
    GB_BTN_SELECT = 0x04, GB_BTN_START = 0x08,
    GB_PAD_RIGHT  = 0x01, GB_PAD_LEFT = 0x02,
    GB_PAD_UP     = 0x04, GB_PAD_DOWN = 0x08,
};

enum { GB_SCREEN_W = 160, GB_SCREEN_H = 144, GB_AUDIO_RATE = 44100 };

gb_handle* gb_create(void);
void       gb_destroy(gb_handle* gb);

// Loads a ROM image (copied internally) and starts the bundled custom boot ROM.
// Returns 1 on success, 0 on failure.
int  gb_load_rom(gb_handle* gb, const uint8_t* data, size_t len);
void gb_reset(gb_handle* gb);
void gb_reset_post_boot(gb_handle* gb); // deterministic reset that skips custom boot

// Runs exactly one video frame (70224 T-cycles).
void gb_run_frame(gb_handle* gb);

// 160*144 bytes, one shade 0-3 per pixel (0 = lightest).
const uint8_t* gb_framebuffer(const gb_handle* gb);

// Convenience: expand the framebuffer into 160*144 pixels of packed
// 0xAARRGGBB using the given 4-entry palette (index 0 = lightest shade).
void gb_framebuffer_argb(const gb_handle* gb, uint32_t* out, const uint32_t palette[4]);

void gb_set_input(gb_handle* gb, uint8_t buttons, uint8_t dpad);

// Drains up to max_frames stereo float sample frames at GB_AUDIO_RATE.
// Returns the number of frames written to `stereo` (interleaved L,R).
int gb_read_audio(gb_handle* gb, float* stereo, int max_frames);

// Battery-backed cartridge RAM. gb_save_ram returns NULL if the cart has none.
int            gb_has_battery(const gb_handle* gb);
const uint8_t* gb_save_ram(const gb_handle* gb, size_t* len);
int            gb_load_save_ram(gb_handle* gb, const uint8_t* data, size_t len);

// ROM title from the cartridge header (up to 16 chars + NUL).
void gb_rom_title(const gb_handle* gb, char out[17]);

// Reads one byte from the emulated address space (game-state peeks, e.g.
// the current map/music id in WRAM). Safe for RAM; IO reads are live.
uint8_t gb_read_mem(gb_handle* gb, uint16_t addr);
void    gb_write_mem(gb_handle* gb, uint16_t addr, uint8_t value);

// Replaces the current ROM symbol document. The accepted form is RGBDS/pret
// .sym text: one `BB:AAAA SymbolName` record per line. Active mods are relinked
// atomically; failure leaves both symbols and ROM untouched.
gb_mod_status gb_mod_load_symbols(gb_handle* gb, const char* text, size_t len);

// Loads/unloads a precompiled .gbmod package. Linking is transactional and may
// be done between frames without resetting emulated RAM or CPU state.
gb_mod_status gb_mod_load(gb_handle* gb, const uint8_t* data, size_t len,
                          uint32_t* out_handle);
gb_mod_status gb_mod_unload(gb_handle* gb, uint32_t handle);
size_t        gb_mod_count(const gb_handle* gb);

// Package metadata/import discovery. Returned pointers remain valid until the
// next mod or ROM mutation on this handle.
const char*    gb_mod_id(const gb_handle* gb, uint32_t handle);
const char*    gb_mod_name(const gb_handle* gb, uint32_t handle);
size_t         gb_mod_import_count(const gb_handle* gb, uint32_t handle);
const char*    gb_mod_import_name(const gb_handle* gb, uint32_t handle, size_t index);
const uint8_t* gb_mod_metadata(const gb_handle* gb, uint32_t handle, size_t* len);
const char*    gb_mod_last_error(const gb_handle* gb);

// Installs the synchronous module-to-host bridge. It is invoked only by a D3
// trap site emitted and registered by the linker, never by ordinary ROM code.
void gb_mod_set_host_callback(gb_handle* gb, gb_mod_host_callback callback, void* user);

// Output-mix channel mask: bit n (0-3) = 0 silences APU channel n+1 in the
// mixed output without affecting emulation. 0x0F (default) = all audible.
// Used by the custom-music player to mute game music but keep SFX channels.
void gb_set_audio_mask(gb_handle* gb, uint8_t mask);

#ifdef __cplusplus
}
#endif
