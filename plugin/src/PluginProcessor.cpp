#include "PluginProcessor.h"
#include "PluginEditor.h"
#include <atomic>

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

    // (a) interleave the clean input into the to-network FIFO (the WebRtcClient encodes + sends it).
    for (int i = 0; i < n; ++i) { scratch[(size_t) i * 2] = l[i]; scratch[(size_t) i * 2 + 1] = r[i]; }
    toNetwork.push (scratch, n);

    // (b) replace the output with the separated audio coming back; silence on underrun (warming up).
    const int got = fromNetwork.pop (scratch, n);
    for (int i = 0; i < got; ++i) { l[i] = scratch[(size_t) i * 2]; r[i] = scratch[(size_t) i * 2 + 1]; }
    for (int i = got; i < n; ++i) { l[i] = 0.0f; r[i] = 0.0f; }
}

void RelaySplitProcessor::connect()
{
    if (client == nullptr)
        client = std::make_unique<WebRtcClient> (toNetwork, fromNetwork);
    client->connect (kLiveUrl);
}

void RelaySplitProcessor::disconnect()
{
    if (client != nullptr)
    {
        client->disconnect();
        client.reset();
    }
}

juce::AudioProcessorEditor* RelaySplitProcessor::createEditor() { return new RelaySplitEditor (*this); }

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() { return new RelaySplitProcessor(); }
