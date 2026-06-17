#pragma once
#include <juce_events/juce_events.h>
#include <mutex>
#include <vector>

class RelaySplitProcessor;

// Process-wide registry of live plugin instances → "session-aware siblings": every RelaySplit
// instance in one DAW shares this process, so a static registry lets any instance see and edit the
// others. Editors listen for changes (instance added/removed, assignments changed) and refresh.
class InstanceRegistry : public juce::ChangeBroadcaster
{
public:
    static InstanceRegistry& get();
    void add (RelaySplitProcessor* p);
    void remove (RelaySplitProcessor* p);
    std::vector<RelaySplitProcessor*> snapshot();

private:
    std::mutex m;
    std::vector<RelaySplitProcessor*> instances;
};
