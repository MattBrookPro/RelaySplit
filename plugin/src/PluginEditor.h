#pragma once
#include <juce_audio_utils/juce_audio_utils.h>
#include "PluginProcessor.h"
#include "PeerMatrix.h"

// Editor: sign in to the control plane, connect this instance to the GPU (+ latency meter), and a
// session-wide peer-assignment matrix — every RelaySplit instance in the DAW, with per-instance and
// group peer assignment.
class RelaySplitEditor : public juce::AudioProcessorEditor,
                         private juce::Timer,
                         private juce::ChangeListener
{
public:
    explicit RelaySplitEditor (RelaySplitProcessor&);
    ~RelaySplitEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;
    void changeListenerCallback (juce::ChangeBroadcaster*) override;
    void refresh();
    void doLogin();

    RelaySplitProcessor& proc;

    juce::Label title;
    // account
    juce::TextEditor userBox, passBox;
    juce::TextButton loginBtn { "Log in" }, logoutBtn { "Log out" };
    juce::Label accountLabel, loginErr;
    // session/connection
    juce::TextButton connectBtn { "Connect" };
    juce::Label statusLabel, rttLabel, infLabel;
    // peer matrix
    juce::Viewport viewport;
    PeerMatrix matrix;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitEditor)
};
