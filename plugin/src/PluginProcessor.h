#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include <atomic>
#include <memory>
#include <set>
#include "StereoFifo.h"
#include "WebRtcClient.h"
#include "ControlClient.h"
#include "InstanceRegistry.h"

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
    bool  isConnecting() const { return client != nullptr && client->isConnecting(); }
    WebRtcClient::Status connectionStatus() const
        { return client ? client->status() : WebRtcClient::Status::Disconnected; }
    float rttMs() const        { return client ? client->rttMs() : 0.0f; }
    float inferenceMs() const  { return client ? client->inferenceMs() : 0.0f; }

    // The deployed live separator container (self-contained /offer signalling path).
    static constexpr const char* kLiveUrl = "https://blitzncs--relaysplit-live-web.modal.run";

    // Session / peer assignment (control plane). Instances in one DAW share a process; the
    // InstanceRegistry makes them session-aware so any editor can assign peers across siblings.
    juce::String getInstanceName() const { return instanceName; }
    void setInstanceName (const juce::String& n);
    const std::set<int>& getAssignedPeers() const { return assignedPeers; }
    void setAssignedPeers (std::set<int> peers);   // creates the channel if needed, syncs shares
    bool isGroupSelected() const { return groupSelected; }
    void setGroupSelected (bool b);

    // Receive mode: 0 = broadcast my own input (assigned peers can tune in); >0 = tune INTO that
    // peer broadcast's channel id (downlink only). Switch while disconnected, then Connect.
    int  getReceiveChannel() const { return receiveChannelId; }
    void setReceiveChannel (int id) { receiveChannelId = id; }

private:
    // Shared with the WebRtcClient so a worker being torn down off-thread can't outlive its buffers.
    std::shared_ptr<StereoFifo> toNetwork   { std::make_shared<StereoFifo> (96000) };  // ~2 s @ 48 kHz
    std::shared_ptr<StereoFifo> fromNetwork { std::make_shared<StereoFifo> (96000) };
    std::unique_ptr<WebRtcClient> client;

    juce::String instanceName;
    int channelId = 0;
    int receiveChannelId = 0;            // 0 = broadcaster; >0 = receiving that channel
    std::atomic<bool> uplinkEnabled { true };  // audio thread: push input only when broadcasting
    std::set<int> assignedPeers;
    bool groupSelected = false;

    juce::HeapBlock<float> scratch;    // interleave/deinterleave staging, sized in prepareToPlay
    int scratchFrames = 0;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitProcessor)
};
