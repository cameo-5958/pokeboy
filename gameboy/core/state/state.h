#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>
#include <type_traits>

// One visitor drives BOTH save and load: every subsystem implements a single
// serialize(StateIO&) that names its fields once. A field added to a subsystem
// is therefore always present in both directions, which is the failure mode a
// separate write_state()/read_state() pair invites (a field added to the writer
// and forgotten in the reader desyncs the machine silently, hours later).
//
// Scalars are transferred field-by-field rather than by blitting whole structs,
// so struct padding never lands in the stream. All supported targets (x86-64,
// arm64, wasm32) are little-endian; the format is not endian-portable and the
// header's version field is the guard if that ever changes.
class StateIO {
public:
    explicit StateIO(std::vector<uint8_t>* out) : out_(out) {}
    StateIO(const uint8_t* data, size_t len) : in_(data), in_len_(len) {}

    bool saving() const { return out_ != nullptr; }
    bool ok() const { return ok_; }
    void fail() { ok_ = false; }

    void bytes(void* p, size_t n) {
        if (!ok_) return;
        if (out_) {
            const uint8_t* b = static_cast<const uint8_t*>(p);
            out_->insert(out_->end(), b, b + n);
        } else {
            if (in_pos_ + n > in_len_) { ok_ = false; return; }   // truncated stream
            memcpy(p, in_ + in_pos_, n);
            in_pos_ += n;
        }
    }

    // Single trivially-copyable scalar.
    template <class T>
    void v(T& x) {
        static_assert(std::is_trivially_copyable<T>::value,
                      "state fields must be trivially copyable");
        bytes(&x, sizeof(T));
    }

    // Fixed-size C array.
    template <class T, size_t N>
    void arr(T (&a)[N]) {
        static_assert(std::is_trivially_copyable<T>::value,
                      "state arrays must be trivially copyable");
        bytes(a, sizeof(a));
    }

    // Length-prefixed byte vector whose size must match on load. Used for
    // cartridge RAM, where a size mismatch means the state belongs to a
    // different cartridge and must be rejected rather than truncated.
    void sized_bytes(std::vector<uint8_t>& v) {
        uint32_t n = static_cast<uint32_t>(v.size());
        this->v(n);
        if (!ok_) return;
        if (!saving() && n != v.size()) { ok_ = false; return; }
        bytes(v.data(), n);
    }

private:
    std::vector<uint8_t>* out_ = nullptr;
    const uint8_t* in_ = nullptr;
    size_t in_len_ = 0, in_pos_ = 0;
    bool ok_ = true;
};
