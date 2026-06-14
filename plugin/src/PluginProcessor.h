#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include <memory>
#include "StereoFifo.h"
#include "WebRtcClient.h"

// RelaySplit plugin processor. Captures the track's audio, ships it to the Modal GPU over WebRTC,
// and plays back the separated vocal — with a live latency meter.
//
// REAL-TIME RULE: processBlock runs on the audio thread and only (a) interleaves the input into the
// to-network FIFO and (b) reads separated audio out of the from-network FIFO. No allocation, no
// locking, no sockets here — all transport / Opus / WebRTC happen on the WebRtcClient worker thread.
class RelaySplitProcessor : public juce::AudioProcessor
{
public:
    RelaySplitProcessor();
    ~RelaySplitProcessor() override;

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

    // UI controls (called on the message thread).
    void connect();
    void disconnect();
    bool  isConnected() const  { return client != nullptr && client->isConnected(); }
    float rttMs() const        { return client ? client->rttMs() : 0.0f; }
    float inferenceMs() const  { return client ? client->inferenceMs() : 0.0f; }

    // The deployed live separator container (self-contained /offer signalling path).
    static constexpr const char* kLiveUrl = "https://blitzncs--relaysplit-live-web.modal.run";

private:
    StereoFifo toNetwork   { 96000 };  // ~2 s at 48 kHz
    StereoFifo fromNetwork { 96000 };
    std::unique_ptr<WebRtcClient> client;

    juce::HeapBlock<float> scratch;    // interleave/deinterleave staging, sized in prepareToPlay
    int scratchFrames = 0;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitProcessor)
};
