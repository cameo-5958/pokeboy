#pragma once
#include "mask.h"
#include "rng.h"
namespace pkai {
int64_t monotonic_ns();
class Scheduler {
public:
    enum Stage : uint8_t { Idle, Events, Read, Masking, SampleAction, Write, Ready };
    struct Identity {
        uint32_t sequence{}, reserved_draw{}, observation_hash{};
        uint64_t first_frame{}, last_frame{};
        uint16_t visible_frames{};
        uint8_t kind{};
        Action result{};
        void serialize(StateIO&);
    } request;
    Stage stage=Idle;
    uint64_t completions{}, timeouts{};
    uint64_t cost_ns[7]{}, compute_steps{};
    Scheduler();
    void reset();
    uint8_t poll(const Memory&,Tracker&,uint8_t kind,uint64_t frame);
    void step(const Memory&,Tracker&,int64_t absolute_deadline);
    bool pending() const {return stage!=Idle && stage!=Ready;}
    void serialize(StateIO&);
    Rng rng;
private:
    Observation observation{};
    Mask mask{};
    void publish(const Memory&,Tracker&);
};
}
