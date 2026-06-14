#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include "PluginProcessor.h"

// Minimal control surface: a Connect button and the live latency meter (net RTT + inference),
// polled from the processor's atomics on a timer. Phase 2 wires Connect to the WebRTC session.
class RelaySplitEditor : public juce::AudioProcessorEditor, private juce::Timer
{
public:
    explicit RelaySplitEditor (RelaySplitProcessor&);
    ~RelaySplitEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;

    RelaySplitProcessor& proc;
    juce::TextButton connectButton { "Connect" };
    juce::Label title, statusLabel, rttLabel, infLabel;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitEditor)
};
