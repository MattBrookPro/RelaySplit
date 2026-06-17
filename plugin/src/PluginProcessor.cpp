#include "PluginProcessor.h"
#include "PluginEditor.h"
#include <atomic>
#include <thread>

static std::atomic<int> instanceCounter { 0 };

RelaySplitProcessor::RelaySplitProcessor()
    : AudioProcessor (BusesProperties()
          .withInput  ("Input",  juce::AudioChannelSet::stereo(), true)
          .withOutput ("Output", juce::AudioChannelSet::stereo(), true))
{
    instanceName = "RelaySplit " + juce::String (++instanceCounter);
    InstanceRegistry::get().add (this);
}

RelaySplitProcessor::~RelaySplitProcessor()
{
    InstanceRegistry::get().remove (this);
    disconnect();
}

void RelaySplitProcessor::setInstanceName (const juce::String& n)
{
    instanceName = n;
    InstanceRegistry::get().sendChangeMessage();
}

void RelaySplitProcessor::setGroupSelected (bool b)
{
    groupSelected = b;
    InstanceRegistry::get().sendChangeMessage();
}

void RelaySplitProcessor::setAssignedPeers (std::set<int> peers)
{
    assignedPeers = std::move (peers);
    auto& cc = ControlClient::get();
    if (cc.isLoggedIn())
    {
        if (channelId == 0) channelId = cc.createChannel (instanceName);  // lazily create this instance's channel
        if (channelId != 0) cc.setShares (channelId, assignedPeers);
    }
    InstanceRegistry::get().sendChangeMessage();
}

void RelaySplitProcessor::prepareToPlay (double, int samplesPerBlock)
{
    scratchFrames = juce::jmax (samplesPerBlock, 4096);
    scratch.allocate ((size_t) scratchFrames * 2, true);  // interleaved L,R staging
}

bool RelaySplitProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    return layouts.getMainOutputChannelSet() == juce::AudioChannelSet::stereo()
        && layouts.getMainInputChannelSet()  == juce::AudioChannelSet::stereo();
}

void RelaySplitProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;
    const int n = buffer.getNumSamples();

    // Not connected → passthrough (leave the buffer as-is). This keeps the plugin transparent
    // until the user hits Connect.
    if (client == nullptr || n > scratchFrames)
        return;

    float* l = buffer.getWritePointer (0);
    float* r = buffer.getNumChannels() > 1 ? buffer.getWritePointer (1) : l;

    // (a) Broadcaster only: interleave the clean input into the to-network FIFO (the WebRtcClient
    // encodes + sends it). A receiver sends no uplink — it just plays the remote broadcast.
    if (uplinkEnabled.load())
    {
        for (int i = 0; i < n; ++i) { scratch[(size_t) i * 2] = l[i]; scratch[(size_t) i * 2 + 1] = r[i]; }
        toNetwork->push (scratch, n);
    }

    // (b) replace the output with the separated audio coming back; silence on underrun (warming up).
    const int got = fromNetwork->pop (scratch, n);
    for (int i = 0; i < got; ++i) { l[i] = scratch[(size_t) i * 2]; r[i] = scratch[(size_t) i * 2 + 1]; }
    for (int i = got; i < n; ++i) { l[i] = 0.0f; r[i] = 0.0f; }
}

void RelaySplitProcessor::connect()
{
    if (client == nullptr)
        client = std::make_unique<WebRtcClient> (toNetwork, fromNetwork);

    if (receiveChannelId > 0)
    {
        // Receiver: tune into a peer's broadcast (downlink only — don't ship this track's input).
        uplinkEnabled = false;
        client->connect (kLiveUrl, WebRtcClient::Mode::Receive, std::to_string (receiveChannelId));
    }
    else
    {
        // Broadcaster: key the stream by THIS instance's channel id so assigned peers can tune in
        // (the same id the shares table — and the web "Tune in" link — use).
        auto& cc = ControlClient::get();
        if (cc.isLoggedIn() && channelId == 0) channelId = cc.createChannel (instanceName);
        uplinkEnabled = true;
        client->connect (kLiveUrl, WebRtcClient::Mode::Broadcast,
                         channelId > 0 ? std::to_string (channelId) : std::string());
    }
}

void RelaySplitProcessor::disconnect()
{
    if (client == nullptr) return;
    // The blocking part of teardown is the worker join, which can stall if the worker is mid-HTTP
    // (the connecting phase). Do it on a detached thread so the message thread returns instantly —
    // the UI flips to "Connect" right away. Safe because the FIFOs are shared_ptrs: the dying worker
    // keeps them alive via its own refs until it finishes, even if the processor is gone by then.
    client->requestStop();
    std::thread ([c = std::move (client)]() mutable { c.reset(); }).detach();
}

juce::AudioProcessorEditor* RelaySplitProcessor::createEditor() { return new RelaySplitEditor (*this); }

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() { return new RelaySplitProcessor(); }
