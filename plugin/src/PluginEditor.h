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
    void populateReceive();   // (re)load the "shared with me" broadcasts into the Listen-to box
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
    // receive mode: which broadcast (if any) this instance tunes into
    juce::Label receiveLabel { {}, "Listen to:" };
    juce::ComboBox receiveBox;
    juce::TextButton receiveRefreshBtn { juce::String::fromUTF8 ("↻") };  // ↻ reload shared list
    std::vector<int> receiveIds;   // parallel to receiveBox items: index -> channel id (0 = broadcast)
    // peer matrix
    juce::Viewport viewport;
    PeerMatrix matrix;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (RelaySplitEditor)
};
