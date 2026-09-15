#pragma once
// Fixed-point primitives of the §8.3 integer contract, mirroring
// ai/models/pep_int.py one for one. Everything is int32/int64; no float.
#include <cstdint>
namespace pkai::fx {

static_assert((-1 >> 1) == -1, "arithmetic right shift required");

constexpr int32_t I8_MIN = -127, I8_MAX = 127;   // symmetric int8: -128 is never produced
constexpr int32_t I16_MIN = -32768, I16_MAX = 32767;
constexpr int64_t I32_MIN = -(int64_t(1) << 31), I32_MAX = (int64_t(1) << 31) - 1;
constexpr int32_t MASK16 = -32768;               // masked logit
constexpr int32_t Q14_ONE = 1 << 14;

inline int64_t clamp64(int64_t x, int64_t lo, int64_t hi) { return x < lo ? lo : x > hi ? hi : x; }
inline int32_t sat32(int64_t x) { return int32_t(clamp64(x, I32_MIN, I32_MAX)); }
inline int32_t sat16(int64_t x) { return int32_t(clamp64(x, I16_MIN, I16_MAX)); }
inline int32_t sat8(int64_t x) { return int32_t(clamp64(x, I8_MIN, I8_MAX)); }

// Arithmetic right shift by s >= 0 rounding half to even (pep_int.rshift_round_even).
inline int64_t rshift_round_even(int64_t x, unsigned s) {
    if (s == 0) return x;
    const int64_t q = x >> s;
    const int64_t r = x & ((int64_t(1) << s) - 1);
    const int64_t half = int64_t(1) << (s - 1);
    return q + ((r > half) || (r == half && (q & 1)));
}

// Saturating rounding doubling high multiply (VQRDMULH): sat32((2*x*m + 2^31) >> 32).
inline int32_t srdmh(int32_t x, int32_t m) { return sat32((int64_t(x) * m + (int64_t(1) << 30)) >> 31); }

// int32 accumulator -> [lo, hi] via Q0.31 multiplier and rounding shift. A negative
// shift is a saturating left shift of the accumulator before the multiply.
inline int32_t requant(int32_t acc, int32_t mult, int shift, int32_t lo, int32_t hi) {
    int64_t x = acc;
    if (shift < 0) x = sat32(x << (unsigned(-shift) > 32 ? 32 : unsigned(-shift)));
    const int32_t y = srdmh(int32_t(x), mult);
    const int64_t z = shift > 0 ? rshift_round_even(y, unsigned(shift)) : y;
    return int32_t(clamp64(z, lo, hi));
}

// Number of significant bits of a positive value (0 for 0).
inline unsigned bit_length(int64_t x) {
    unsigned n = 0;
    while (x > 0) { ++n; x >>= 1; }
    return n;
}

// 257-entry Q0.15 exp(-d) table indexed by d16 >> 4 with linear interpolation on the low 4 bits.
inline int32_t exp_lut(int32_t d16, const int16_t* lut) {
    int32_t i = d16 >> 4; if (i > 256) i = 256;
    const int32_t frac = d16 & 15;
    const int32_t lo = lut[i], hi = lut[i + 1 > 256 ? 256 : i + 1];
    return lo + (((hi - lo) * frac) >> 4);
}

// 257-entry Q1.14 gate table over Q3.12 input: index = (v + 32768) >> 8, interpolation on the low 8 bits.
inline int32_t gate_lut(int32_t v_q12, const int16_t* lut) {
    const int32_t u = sat16(v_q12) + 32768;
    const int32_t i = u >> 8, frac = u & 255;
    const int32_t lo = lut[i], hi = lut[i + 1];
    return lo + (((hi - lo) * frac) >> 8);
}

}
