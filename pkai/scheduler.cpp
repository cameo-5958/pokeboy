#include "scheduler.h"
#include <chrono>
#include <algorithm>
namespace pkai {
using namespace symbols;
int64_t monotonic_ns() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
Scheduler::Scheduler() {
    // Calibrate the stages actually shipped by the random backend. Model/kernel
    // calibration belongs to the later model tier, not to this baseline.
    std::array<uint8_t,65536> bytes{};
    Memory m{bytes.data(), [](void* p,uint16_t a){return static_cast<uint8_t*>(p)[a];},
        [](void* p,uint16_t a,uint8_t v){static_cast<uint8_t*>(p)[a]=v;},nullptr,0};
    Tracker t; Observation o; Mask legal;
    volatile uint32_t sink=0;
    for(unsigned stage_index=Events;stage_index<=Write;++stage_index) {
        uint64_t total=0;
        for(unsigned i=0;i<32;++i) {
            t.pending=1;
            auto start=monotonic_ns();
            switch(stage_index) {
            case Events:t.drain_one();sink=t.pending;break;
            case Read:o=observe(m,t);sink=o.active.hp;break;
            case Masking:legal=legal_mask(m,o,0);sink=legal.bits;break;
            case SampleAction:sink=random_action(legal,i,0).payload;break;
            case Write: {
                auto current=observe(m,t);auto mask=legal_mask(m,current,0);
                auto a=random_action(mask,i,0);m.write(wAIAction,a.kind);
                m.write(wAIAction+1,a.payload);m.write(wAIAction+2,a.sequence);
                m.write(wEnemyMoveListIndex,a.payload);m.write(wEnemySelectedMove,0xa5);
                sink=a.payload;break;
            }
            }
            total+=std::max<int64_t>(1,monotonic_ns()-start);
        }
        cost_ns[stage_index]=std::max<uint64_t>(1000,total/32);
    }
    (void)sink;
}
void Scheduler::reset() { stage=Idle;request={};rng.seed(1);completions=timeouts=compute_steps=0; }
void Scheduler::Identity::serialize(StateIO& s) {
    s.v(sequence);s.v(reserved_draw);s.v(observation_hash);s.v(first_frame);s.v(last_frame);
    s.v(visible_frames);s.v(kind);result.serialize(s);
}
static uint32_t hash_observation(const Observation& o) {
    uint32_t h=2166136261u;
    auto add=[&](uint32_t x){h=(h^x)*16777619u;};
    add(o.round);add(o.own_slot);add(o.trainer_class);add(o.count);add(o.disabled);
    for(auto& p:o.own) {add(p.species);add(p.hp);add(p.status);for(auto v:p.moves)add(v);}
    for(auto& p:o.player) {add(p.known);add(p.species);add(p.hp);add(p.status);for(auto v:p.moves)add(v);}
    return h;
}
void Scheduler::publish(const Memory& m,Tracker& t) {
    auto current=observe(m,t); auto legal=legal_mask(m,current,request.kind);
    if(!legal_action(legal,request.result)) request.result=random_action(legal,request.reserved_draw,uint8_t(request.sequence));
    mask=legal;
    if(legal_action(mask,request.result)) {
        m.write(wAIAction,request.result.kind);m.write(wAIAction+1,request.result.payload);m.write(wAIAction+2,request.result.sequence);
        if(request.result.kind==0) {
            m.write(wEnemyMoveListIndex,request.result.payload);
            m.write(wEnemySelectedMove,mask.struggle?0xa5:current.active.moves[request.result.payload]);
        }
    }
    stage=Ready;
}
uint8_t Scheduler::poll(const Memory& m,Tracker& t,uint8_t kind,uint64_t frame) {
    if(kind>1) return 2;
    if(stage==Idle) {
        ++request.sequence;request.kind=kind;request.reserved_draw=rng.next();
        request.first_frame=request.last_frame=frame;request.visible_frames=0;request.result={};
        request.observation_hash=hash_observation(observe(m,t));stage=Events;
    } else if(request.kind!=kind) return 2;
    if(frame!=request.last_frame) {
        auto delta=frame>=request.last_frame?frame-request.last_frame:0;
        request.visible_frames=uint16_t(std::min<uint64_t>(300,request.visible_frames+delta));request.last_frame=frame;
    }
    if(stage!=Ready && request.visible_frames>=300) {
        mask=legal_mask(m,observe(m,t),kind);request.result=random_action(mask,request.reserved_draw,uint8_t(request.sequence));
        publish(m,t);++timeouts;
    }
    if(stage==Ready) {
        publish(m,t); // Restore READY is transactional, including action bytes.
        stage=Idle;++completions;
        return legal_action(mask,request.result)?1:2;
    }
    return 0;
}
void Scheduler::step(const Memory& m,Tracker& t,int64_t deadline) {
    while(pending()) {
        auto start=monotonic_ns(); auto index=stage;
        if(deadline<=start || uint64_t(deadline-start)<cost_ns[index]+5000) return;
        switch(stage) {
        case Events: if(t.pending) t.drain_one();else stage=Read;break;
        case Read: observation=observe(m,t);stage=Masking;break;
        case Masking: mask=legal_mask(m,observation,request.kind);stage=SampleAction;break;
        case SampleAction: request.result=random_action(mask,request.reserved_draw,uint8_t(request.sequence));stage=Write;break;
        case Write: publish(m,t);break;
        default:return;
        }
        auto duration=std::max<int64_t>(1,monotonic_ns()-start);
        cost_ns[index]=(7*cost_ns[index]+duration)/8;++compute_steps;
    }
}
void Scheduler::serialize(StateIO& s) {
    s.v(stage);request.serialize(s);rng.serialize(s);s.v(completions);s.v(timeouts);
    if(stage>Ready || request.kind>1 || request.visible_frames>300) s.fail();
    if(!s.saving() && pending()) stage=Events;
}
}
