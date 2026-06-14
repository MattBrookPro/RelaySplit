#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include <atomic>

// RelaySplit plugin processor. Sits on a track, captures its audio, (Phase 2) ships it to the Modal
// GPU over WebRTC and plays back the separated vocal. Phase 1 is a clean passthrough with the
// real-time architecture in place.
//
// REAL-TIME RULE: processBlock runs on the audio thread and must NEVER block, allocate, or lock.
// In Phase 2 it will only (a) copy input into a lock-free FIFO for the network thread, and
// (b) copy separated output out of another lock-free FIFO — all transport/inference happens
// off-thread. The latency figures are plain atomics: written by the network thread, read by the UI.
class RelaySplitProcessor : public juce::AudioProcessor
{
public:
    RelaySplitProcessor();
    ~RelaySplitProcessor() override = default;

    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
    bool isBusesLayoutSupported (const BusesLayout&) const override;
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "RelaySplit"; }
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock&) override {}
    void setStateInformation (const void*, int) override {}

    // Live state, surfaced to the editor (and, later, written by the WebRTC/network thread).
    std::atomic<bool>  connected   { false };
    std::atomic<float> netRttMs    { 0.0f };
    std::atomic<float> inferenceMs { 0.0f };

private:
    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitProcessor)
};
