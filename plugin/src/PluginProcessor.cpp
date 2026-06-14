#include "PluginProcessor.h"
#include "PluginEditor.h"

RelaySplitProcessor::RelaySplitProcessor()
    : AudioProcessor (BusesProperties()
          .withInput  ("Input",  juce::AudioChannelSet::stereo(), true)
          .withOutput ("Output", juce::AudioChannelSet::stereo(), true))
{
}

void RelaySplitProcessor::prepareToPlay (double, int) {}

bool RelaySplitProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    // Stereo in / stereo out — matches the Demucs path on the GPU.
    return layouts.getMainOutputChannelSet() == juce::AudioChannelSet::stereo()
        && layouts.getMainInputChannelSet()  == juce::AudioChannelSet::stereo();
}

void RelaySplitProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    // Phase 1: passthrough. Phase 2 replaces this with two lock-free FIFO copies only:
    //   - write `buffer` (the clean input) to the to-network FIFO,
    //   - overwrite `buffer` with separated audio read from the from-network FIFO.
    // No allocation / locking / sockets on this thread — the network + Opus + WebRTC run elsewhere.
    juce::ignoreUnused (buffer);
}

juce::AudioProcessorEditor* RelaySplitProcessor::createEditor()
{
    return new RelaySplitEditor (*this);
}

// JUCE plugin entry point.
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new RelaySplitProcessor();
}
